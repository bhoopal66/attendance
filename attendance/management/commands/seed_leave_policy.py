"""Write the statutory leave rules into the database instead of the code.

    manage.py seed_leave_policy            # report only
    manage.py seed_leave_policy --apply

§29 forbids hard-coding these. Today they live as module constants in
`services_leave_earnings.py` (ACCRUAL_MIN_MONTHS, ACCRUAL_SHORT_DAYS_PER_MONTH,
ACCRUAL_FULL_DAYS_PER_YEAR, POLICY_LEAVE_PCT). This creates the equivalent as
data, sourced and dated, so a future change to UAE rules is a new version row
rather than a code deploy.

TWO POLICIES, BECAUSE NOT EVERYONE IS COVERED
---------------------------------------------
    MOHRE      6-month minimum, 2 days/month, 30 days/year, paid at 50%
    offshore   the same numbers, but sourced to company policy rather than to
               Federal Decree-Law 33/2021 — because that law does not apply to
               staff working from India or Nepal, and citing it against them
               would be wrong in a way nobody would notice later

The numbers are identical today, which is exactly why the SOURCE matters: it is
the only thing distinguishing a statutory obligation from a company practice
that can be changed at will.

Nothing reads these yet — the engine still uses its constants. This lands the
data so the switchover is a separate, verifiable step.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

POLICIES = [
    dict(name='UAE statutory annual leave (MOHRE)',
         jurisdiction='mohre',
         source='Federal Decree-Law 33/2021, Art. 29'),
    dict(name='Annual leave — offshore staff',
         jurisdiction='offshore',
         source='Company policy, bhoopal 17 Aug 2026 (D7/D8)'),
    dict(name='Annual leave — UAE own/spouse visa',
         jurisdiction='uae_own_visa',
         source='Company policy, bhoopal 17 Aug 2026 (D7/D8)'),
]

VERSION = dict(
    effective_from='2026-01-01',
    min_months_for_entitlement=6,
    short_service_days_per_month=2,
    full_days_per_year=30,
    accrual_basis='monthly',
    pay_percentage=50,
    divisor_basis='period_days',
    encashment_allowed=True,
    encashment_basis='gross',
)


class Command(BaseCommand):
    help = 'Create leave policies and their first version from the current constants.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        import datetime
        from attendance.models import LeavePolicy, LeavePolicyVersion

        w = self.stdout.write
        apply_it = opts['apply']
        create, keep = [], []
        for spec in POLICIES:
            existing = LeavePolicy.objects.filter(
                name=spec['name'], labour_jurisdiction=spec['jurisdiction']).first()
            (keep if existing else create).append((spec, existing))

        w('')
        w('LEAVE POLICIES')
        w('-' * 78)
        for spec, existing in keep:
            w(f"  EXISTS   {spec['jurisdiction']:<16}{spec['name']}")
            w(f"           versions: {existing.versions.count()} — left alone")
        for spec, _ in create:
            w(f"  CREATE   {spec['jurisdiction']:<16}{spec['name']}")
            w(f"           source: {spec['source']}")

        w('')
        w('VERSION VALUES (identical across all three — the SOURCE is what differs)')
        w('-' * 78)
        for k, v in VERSION.items():
            w(f'  {k:<34}{v}')

        w('')
        w(f'  to create: {len(create)}   already present: {len(keep)}')
        w('')
        w('  NOTE: nothing reads these yet. services_leave_earnings still uses its')
        w('        module constants. This lands the data; the switchover is separate.')

        if not apply_it:
            w('')
            w(self.style.WARNING('DRY RUN — nothing was written.'))
            return

        eff = datetime.date.fromisoformat(VERSION['effective_from'])
        fields = {k: v for k, v in VERSION.items() if k != 'effective_from'}
        with transaction.atomic():
            for spec, _ in create:
                pol = LeavePolicy.objects.create(
                    name=spec['name'], leave_type_code='annual',
                    labour_jurisdiction=spec['jurisdiction'],
                    is_active=True, notes=spec['source'])
                LeavePolicyVersion.objects.create(
                    policy=pol, effective_from=eff,
                    source_reference=spec['source'], **fields)
        w(self.style.SUCCESS(f'CREATED {len(create)} policy(ies) with a first version.'))
