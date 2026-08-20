"""
Who may see which employee compliance field.

WHY THIS IS A MODULE AND NOT A TEMPLATE CONDITION
-------------------------------------------------
Hiding a field with `{% if %}` puts the value in the HTML and then declines to
draw it. The number is still in the page source, still in the browser cache,
still in anything that scrapes the page. For an Emirates ID or an IBAN that is
not a control, it is a decoration.

So visibility is decided here, server-side, and a value the viewer may not see
is never put into the context at all. The template renders what it is given.

MASKING VS VISIBILITY - TWO DIFFERENT QUESTIONS
-----------------------------------------------
    can_view    - may this role know the field exists and see it at all?
    is_masked   - is the value shown shortened by default?
    can_reveal  - may this role ask for the full value?

Emirates ID and IBAN are masked for everyone who can see them. Revealing is a
deliberate act, and the caller is expected to write an audit entry when it
happens: an identity number read is a thing that should be answerable later.

ROLE, NOT PAGE
--------------
This is deliberately separate from `has_section_access`, which answers "may
this user open the Employees page". A user can be allowed into the page and
still have no business seeing anyone's passport number. Granting a sidebar
section must never quietly grant identity data.
"""

# Role constants mirror UserProfile.ROLE_* so this module stays importable
# without pulling in the model layer (and therefore testable without a DB).
HR_ADMIN = 'hr_admin'
EXEC_DIRECTOR = 'exec_director'
MANAGER = 'manager'
IT = 'it'

ROLE_LABELS = {
    HR_ADMIN: 'HR Admin',
    EXEC_DIRECTOR: 'Executive Director',
    MANAGER: 'Manager',
    IT: 'IT',
}

# group -> definition. `roles` is exactly the Visibility column of the spec.
FIELD_GROUPS = {
    'identity_number': {
        'label': 'Identity numbers',
        'detail': 'Emirates ID, passport, visa and labour card numbers',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': True,
    },
    'identity_expiry': {
        'label': 'Identity expiry dates',
        'detail': 'Emirates ID, passport, visa, labour card and medical insurance expiry',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': False,
    },
    'identity_meta': {
        'label': 'Visa type',
        'detail': 'Category of UAE visa — not a number, so not masked',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': False,
    },
    'visa_number': {
        'label': 'Visa identity numbers',
        'detail': 'UID, visa file number and residence permit number (EmployeeVisa)',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': True,
    },
    'bank': {
        'label': 'Bank details',
        'detail': 'Bank name and IBAN — drives WPS SIF generation',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': True,
    },
    'contract': {
        'label': 'Contract type',
        'detail': 'Limited / Unlimited — the gratuity computation basis',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': False,
    },
    'probation': {
        'label': 'Probation',
        'detail': 'Probation end date and the day-75 review trigger',
        'roles': {MANAGER, HR_ADMIN},
        'masked': False,
    },
    'partner_banks': {
        'label': 'Assigned partner banks',
        'detail': 'RO target setting and commission plan link',
        'roles': {MANAGER, HR_ADMIN, EXEC_DIRECTOR},
        'masked': False,
    },
    'commission_plan': {
        'label': 'Commission plan code',
        'detail': 'Bridge to the DSA commission engine',
        'roles': {HR_ADMIN, EXEC_DIRECTOR},
        'masked': False,
    },
    'system_ids': {
        'label': 'System identifiers',
        'detail': 'TaamulConnect user ID — API sync key, access provisioning',
        'roles': {HR_ADMIN, IT},
        'masked': False,
    },
    'emergency': {
        'label': 'Emergency contact',
        'detail': 'Duty-of-care record',
        'roles': {HR_ADMIN, MANAGER},
        'masked': False,
    },
}

# Groups that do not apply to Admin-department staff. Both are sales
# instruments: an Admin employee has no RO target and no commission plan, so
# showing them an empty row invites someone to fill it in.
ADMIN_EXCLUDED_GROUPS = {'partner_banks', 'commission_plan'}

ADMIN_DEPARTMENT = 'Admin'


def role_of(user):
    """The viewer's business role, or '' when none is assigned.

    No implicit grant. A superuser with no role assigned sees no compliance
    data — being able to administer the system is not the same as having a
    reason to read someone's passport number, and the whole point of this
    table is that the two are decided separately.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return ''
    profile = getattr(user, 'profile', None)
    if profile is None:
        return ''
    return profile.role or ''


def applies_to(group, department):
    """Does this group apply to an employee in this department at all?"""
    if group in ADMIN_EXCLUDED_GROUPS and (department or '') == ADMIN_DEPARTMENT:
        return False
    return group in FIELD_GROUPS


def can_view(role, group, department=None):
    spec = FIELD_GROUPS.get(group)
    if spec is None:
        return False
    if department is not None and not applies_to(group, department):
        return False
    return role in spec['roles']


def is_masked(group):
    spec = FIELD_GROUPS.get(group)
    return bool(spec and spec['masked'])


def can_reveal(role, group, department=None):
    """Reveal is only meaningful for a masked group the role can already see."""
    return is_masked(group) and can_view(role, group, department)


def visible_groups(role, department=None):
    return [g for g in FIELD_GROUPS if can_view(role, g, department)]


def mask(value, keep=4):
    """Show the last `keep` characters; replace the rest with bullets.

    A short value is masked ENTIRELY rather than partially. Showing the last 4
    of a 5-character number discloses it, and a masking function that leaks on
    short input is worse than none because it is trusted.
    """
    if value is None:
        return ''
    text = str(value).strip()
    if not text:
        return ''
    if len(text) <= keep:
        return '•' * len(text)
    return '•' * (len(text) - keep) + text[-keep:]


def field_payload(role, group, value, department=None):
    """What the template should be given for one field.

    Returns None when the viewer may not see it — so the caller puts nothing in
    the context rather than a value the template is trusted to hide.
    """
    if not can_view(role, group, department):
        return None
    if is_masked(group):
        return {'display': mask(value), 'masked': True,
                'can_reveal': True, 'has_value': bool(value)}
    return {'display': '' if value is None else value, 'masked': False,
            'can_reveal': False, 'has_value': value not in (None, '')}
