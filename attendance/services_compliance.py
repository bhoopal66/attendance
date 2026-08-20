"""
The employee compliance block — assembled server-side, per viewer.

The five expiry-tracked documents are READ FROM `EmployeeDocument`. They are
not copied onto the employee, because a number that exists in two places
eventually disagrees with itself, and an Emirates ID that disagrees with
itself is worse than one that is missing.

Everything here returns only what the viewer is entitled to. A field the
viewer may not see is absent from the result, not present-and-hidden: see
`compliance_access` for why that distinction is the whole control.
"""

import datetime
import logging

from . import compliance_access as access

logger = logging.getLogger('attendance')

# 90 / 60 / 30. Ordered widest-first so the first match is the *widest* band a
# date falls in; the caller wants the tightest, so the list is walked in
# reverse. Keeping one ordered definition avoids a second, disagreeing copy.
EXPIRY_TIERS = [(90, 'tier90'), (60, 'tier60'), (30, 'tier30')]

# (document_type, label, carries a number worth masking)
DOCUMENT_ROWS = [
    ('emirates_id',       'Emirates ID',       True),
    ('passport',          'Passport',          True),
    ('uae_visa',          'UAE Visa',          True),
    ('labour_card',       'Labour Card / Work Permit', True),
    ('medical_insurance', 'Medical Insurance', False),
]

TIER_LABELS = {
    'expired':  'Expired',
    'tier30':   'Expires within 30 days',
    'tier60':   'Expires within 60 days',
    'tier90':   'Expires within 90 days',
    'valid':    'Valid',
    'missing':  'Not recorded',
}


def expiry_tier(expiry_date, today=None):
    """'missing' | 'expired' | 'tier30' | 'tier60' | 'tier90' | 'valid'.

    A missing date is NOT valid. Treating "no expiry recorded" as fine is how a
    lapsed labour card stays invisible until an inspector finds it.
    """
    if not expiry_date:
        return 'missing'
    today = today or datetime.date.today()
    days = (expiry_date - today).days
    if days < 0:
        return 'expired'
    for limit, name in reversed(EXPIRY_TIERS):
        if days <= limit:
            return name
    return 'valid'


def days_to_expiry(expiry_date, today=None):
    if not expiry_date:
        return None
    return (expiry_date - (today or datetime.date.today())).days


def _latest_documents(employee):
    """Newest document per type for this employee, in-house or remote.

    Newest by expiry date, falling back to creation. A renewed passport is a
    second row of the same type; the block must show the live one, not the
    first one ever filed.
    """
    from .models import EmployeeDocument

    field = 'employee' if employee.__class__.__name__ == 'Employee' else 'remote_employee'
    rows = EmployeeDocument.objects.filter(**{field: employee})
    best = {}
    for doc in rows:
        current = best.get(doc.document_type)
        if current is None:
            best[doc.document_type] = doc
            continue
        a = (doc.expiry_date or datetime.date.min, doc.id)
        b = (current.expiry_date or datetime.date.min, current.id)
        if a > b:
            best[doc.document_type] = doc
    return best


def document_rows(employee, role, today=None):
    """The five expiry-tracked rows, filtered to what this viewer may see."""
    see_number = access.can_view(role, 'identity_number')
    see_expiry = access.can_view(role, 'identity_expiry')
    if not (see_number or see_expiry):
        return []

    latest = _latest_documents(employee)
    out = []
    for doc_type, label, has_number in DOCUMENT_ROWS:
        doc = latest.get(doc_type)
        expiry = doc.expiry_date if doc else None
        row = {
            'type': doc_type,
            'label': label,
            'recorded': doc is not None,
            'document_id': doc.id if doc else None,
            'verified': bool(doc and doc.is_verified),
        }
        if has_number and see_number:
            row['number'] = access.field_payload(
                role, 'identity_number', doc.document_number if doc else '')
        else:
            row['number'] = None
        if see_expiry:
            tier = expiry_tier(expiry, today)
            row.update({
                'expiry': expiry,
                'days': days_to_expiry(expiry, today),
                'tier': tier,
                'tier_label': TIER_LABELS[tier],
            })
        out.append(row)
    return out


def field_rows(employee, role, today=None):
    """The block's own fields, each gated and each aware of the Admin rule."""
    dept = getattr(employee, 'department', '') or ''
    rows = []

    def add(group, key, label, value, extra=None):
        payload = access.field_payload(role, group, value, department=dept)
        if payload is None:
            return
        row = {'group': group, 'key': key, 'label': label}
        row.update(payload)
        if extra:
            row.update(extra)
        rows.append(row)

    add('identity_meta', 'visa_type', 'Visa type',
        employee.get_visa_type_display() if employee.visa_type else '')
    add('contract', 'contract_type', 'Contract type',
        employee.get_contract_type_display() if employee.contract_type else '',
        {'gratuity_basis': True,
         'unknown_note': ('No contract type recorded — the gratuity basis is '
                          'unknown. It is deliberately not assumed.')
         if not employee.contract_type else ''})

    state = employee.probation_state(today)
    add('probation', 'probation_end_date', 'Probation ends',
        employee.probation_end.isoformat() if employee.probation_end else '',
        {'inferred': employee.probation_is_inferred,
         'state': state,
         'review_due': (employee.probation_review_due.isoformat()
                        if employee.probation_review_due else ''),
         'inferred_note': ('Derived from joining date + 90 days — not confirmed '
                           'by anyone.') if employee.probation_is_inferred else ''})

    # getattr, not attribute access. Bank details and emergency contact were
    # added to Employee only — RemoteEmployee has no such columns. Reaching for
    # them directly crashed the block for every remote employee, which is the
    # dual-employee trap this codebase keeps setting: code written against the
    # in-house model that silently excludes ~30 remote staff. Here it was not
    # even silent, it was an AttributeError.
    has_bank = hasattr(employee, 'bank_name')
    if has_bank:
        add('bank', 'bank_name', 'Bank name', employee.bank_name or '')
        add('bank', 'bank_routing_code', 'IBAN', employee.bank_routing_code or '')
    add('commission_plan', 'commission_plan_code', 'Commission plan',
        employee.commission_plan_code or '')
    add('system_ids', 'taamul_connect_user_id', 'TaamulConnect user ID',
        employee.taamul_connect_user_id or '')
    if hasattr(employee, 'emergency_contact_name'):
        add('emergency', 'emergency_contact_name', 'Emergency contact',
            employee.emergency_contact_name or '')
        add('emergency', 'emergency_contact_phone', 'Emergency phone',
            employee.emergency_contact_phone or '')
    return rows


def partner_bank_rows(employee, role):
    dept = getattr(employee, 'department', '') or ''
    if not access.can_view(role, 'partner_banks', department=dept):
        return None
    links = employee.partner_banks.select_related('bank').all()
    return [{'id': l.id, 'bank': l.bank.name, 'bank_id': l.bank_id,
             'is_primary': l.is_primary} for l in links]


# Which role may WRITE each field. Deliberately narrower than read access:
# an Executive Director reads bank details, but HR owns them. Kept beside the
# read matrix so the form and the save handler cannot disagree about who may
# change what — the handler imports this same dict.
WRITE_RULES = {
    'visa_type':               ('identity_meta',  {access.HR_ADMIN}),
    'contract_type':           ('contract',       {access.HR_ADMIN}),
    'probation_end_date':      ('probation',      {access.HR_ADMIN, access.MANAGER}),
    'commission_plan_code':    ('commission_plan', {access.HR_ADMIN}),
    'taamul_connect_user_id':  ('system_ids',     {access.HR_ADMIN, access.IT}),
    'emergency_contact_name':  ('emergency',      {access.HR_ADMIN, access.MANAGER}),
    'emergency_contact_phone': ('emergency',      {access.HR_ADMIN, access.MANAGER}),
}


def writable_fields(role, employee):
    """Fields this viewer may change on this employee.

    Both tests must pass: the role has to be a writer for the field, AND the
    field has to apply to this employee at all — otherwise the Admin exception
    would be enforced on reading and quietly bypassed on writing.
    """
    dept = getattr(employee, 'department', '') or ''
    out = set()
    for field, (group, writers) in WRITE_RULES.items():
        if role in writers and access.can_view(role, group, department=dept):
            if field.startswith('emergency') and not hasattr(employee, 'emergency_contact_name'):
                continue
            out.add(field)
    return out


def build_block(employee, viewer, today=None):
    """Everything the compliance section needs, for one viewer, one employee."""
    role = access.role_of(viewer)
    dept = getattr(employee, 'department', '') or ''
    docs = document_rows(employee, role, today)
    fields = field_rows(employee, role, today)
    banks = partner_bank_rows(employee, role)

    worst = 'valid'
    order = ['valid', 'tier90', 'tier60', 'tier30', 'missing', 'expired']
    for row in docs:
        tier = row.get('tier')
        if tier and order.index(tier) > order.index(worst):
            worst = tier

    writable = writable_fields(role, employee)
    return {
        'role': role,
        'writable': writable,
        'can_write': bool(writable),
        # True when the model simply has no bank/emergency columns (remote
        # staff). The UI must say "this system does not hold it" rather than
        # "not recorded", which would read as an HR omission.
        'lacks_bank_fields': not hasattr(employee, 'bank_name'),
        'role_label': access.ROLE_LABELS.get(role, ''),
        'has_any': bool(docs or fields or banks),
        'documents': docs,
        'fields': fields,
        'partner_banks': banks,
        'is_admin_department': dept == access.ADMIN_DEPARTMENT,
        'worst_tier': worst,
        'worst_tier_label': TIER_LABELS[worst],
        'review_state': employee.compliance_review_state(today),
        'reviewed_at': employee.compliance_reviewed_at,
        'reviewed_by': employee.compliance_reviewed_by,
        'review_due_date': employee.compliance_review_due_date,
    }


def reveal(employee, viewer, group, key):
    """Return one full masked value, or None if the viewer may not have it.

    The caller is expected to write an audit entry. Reading an identity number
    is an event, not a page render.
    """
    dept = getattr(employee, 'department', '') or ''
    role = access.role_of(viewer)
    if not access.can_reveal(role, group, department=dept):
        return None
    if group == 'bank':
        if not hasattr(employee, 'bank_name'):
            return None
        return {'bank_name': employee.bank_name,
                'bank_routing_code': employee.bank_routing_code}.get(key)
    if group == 'identity_number':
        doc = _latest_documents(employee).get(key)
        return doc.document_number if doc else None
    if group == 'visa_number':
        from .services_identity import current_visa
        visa = current_visa(employee)
        if not visa:
            return None
        return {
            'uid_number': visa.uid_number,
            'visa_file_number': visa.visa_file_number,
            'residence_permit_number': visa.residence_permit_number,
        }.get(key)
    return None


# ══════════════════════════════════════════════════════════════════════════
# Watchlist — the same tiers, across everybody
# ══════════════════════════════════════════════════════════════════════════

TIER_ORDER = ['valid', 'tier90', 'tier60', 'tier30', 'missing', 'expired']


def _worse(a, b):
    return a if TIER_ORDER.index(a) >= TIER_ORDER.index(b) else b


def latest_documents_bulk(employees_by_key):
    """{(kind, id): {doc_type: doc}} for everyone, in ONE query.

    The per-employee version issues a query each. Across 150 staff that is 150
    round trips for a page that exists to be opened every morning, so the
    watchlist gets a bulk path rather than a loop over the single-employee one.
    """
    from .models import EmployeeDocument

    inhouse_ids = [pk for kind, pk in employees_by_key if kind == 'inhouse']
    remote_ids = [pk for kind, pk in employees_by_key if kind == 'remote']

    from django.db.models import Q
    rows = EmployeeDocument.objects.filter(
        Q(employee_id__in=inhouse_ids) | Q(remote_employee_id__in=remote_ids)
    ).only('employee_id', 'remote_employee_id', 'document_type',
           'expiry_date', 'is_verified', 'id')

    best = {}
    for doc in rows:
        key = (('inhouse', doc.employee_id) if doc.employee_id
               else ('remote', doc.remote_employee_id))
        bucket = best.setdefault(key, {})
        current = bucket.get(doc.document_type)
        if current is None or ((doc.expiry_date or datetime.date.min, doc.id)
                               > (current.expiry_date or datetime.date.min, current.id)):
            bucket[doc.document_type] = doc
    return best


def watchlist(viewer, today=None, include_inactive=False):
    """Every active employee's compliance standing, banded 90/60/30.

    Gated by the same matrix as the profile block: a viewer who may not see
    expiry dates gets an empty list, not a list of blanks.
    """
    from .models import Employee, RemoteEmployee

    role = access.role_of(viewer)
    see_expiry = access.can_view(role, 'identity_expiry')
    see_probation = access.can_view(role, 'probation')
    # Whether the compliance REVIEW cadence is this viewer's business. It is an
    # HR record, not a line-management one: a Manager watching their own team's
    # probation should not be handed a list of people HR has not re-verified.
    see_review = see_expiry
    if not (see_expiry or see_probation):
        return {'permitted': False, 'rows': [], 'counts': {}, 'role': role,
                'see_expiry': False, 'see_probation': False}

    today = today or datetime.date.today()

    people = []
    for model, kind in ((Employee, 'inhouse'), (RemoteEmployee, 'remote')):
        qs = model.objects.all() if include_inactive else model.objects.filter(is_active=True)
        for emp in qs:
            people.append(((kind, emp.id), emp))

    docs_by_key = latest_documents_bulk(dict(people)) if see_expiry else {}

    rows = []
    # `counts` bands DOCUMENTS. `clear` counts PEOPLE with nothing outstanding
    # at all. They were one dict at first, and the "clear" figure silently
    # included anyone whose documents were fine but whose review was stale —
    # a stat card reading "6 clear" above a table listing three of them.
    counts = {t: 0 for t in TIER_ORDER}
    clear = 0
    review_counts = {'never': 0, 'due': 0, 'current': 0}
    probation_due = 0

    for key, emp in people:
        worst = 'valid'
        doc_cells = []
        if see_expiry:
            bucket = docs_by_key.get(key, {})
            for doc_type, label, _has_number in DOCUMENT_ROWS:
                doc = bucket.get(doc_type)
                tier = expiry_tier(doc.expiry_date if doc else None, today)
                worst = _worse(worst, tier)
                doc_cells.append({
                    'type': doc_type, 'label': label, 'tier': tier,
                    'tier_label': TIER_LABELS[tier],
                    'expiry': doc.expiry_date if doc else None,
                    'days': days_to_expiry(doc.expiry_date if doc else None, today),
                })

        review_state = emp.compliance_review_state(today) if see_review else 'current'
        prob_state = emp.probation_state(today) if see_probation else 'none'

        # A row earns its place by having something wrong with it. A page that
        # lists everybody equally is a directory, not a watchlist.
        if (worst == 'valid' and review_state == 'current'
                and prob_state != 'review_due'):
            clear += 1
            counts['valid'] += 1
            review_counts[review_state] += 1
            continue

        counts[worst] += 1
        review_counts[review_state] += 1
        if prob_state == 'review_due':
            probation_due += 1

        rows.append({
            'key': key, 'employee_type': key[0], 'employee_id': key[1],
            'name': emp.name, 'tcr': getattr(emp, 'tcr_id', '') or '',
            'person_id': getattr(emp, 'person_id', '') or '',
            'department': emp.department or '',
            'worst_tier': worst, 'worst_tier_label': TIER_LABELS[worst],
            'documents': doc_cells,
            'review_state': review_state,
            'reviewed_at': emp.compliance_reviewed_at,
            'probation_state': prob_state,
            'probation_review_due': emp.probation_review_due,
            'probation_is_inferred': emp.probation_is_inferred,
        })

    rows.sort(key=lambda r: (-TIER_ORDER.index(r['worst_tier']), r['name'].lower()))
    return {
        'permitted': True, 'role': role,
        'see_expiry': see_expiry, 'see_probation': see_probation,
        'rows': rows, 'counts': counts, 'clear': clear,
        'review_counts': review_counts,
        'probation_due': probation_due,
        'total_people': len(people),
        'flagged': len(rows),
        'today': today,
    }
