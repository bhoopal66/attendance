"""Create default approval chains so the transaction layer is usable.

    manage.py seed_approval_chains            # report only
    manage.py seed_approval_chains --apply

Without at least one chain, every transaction refuses to submit — deliberately,
so a missing configuration can never become a silent auto-approval. This writes
sensible defaults built from the roles that EXIST today (manager, hr_admin,
exec_director), not from the nine the specification lists.

The chains are created with no company, which makes them the fallback for every
entity. Add a company-specific chain in the admin when one entity needs a
different route; it overrides this one for that entity only.

Never modifies a chain that already exists — your edits win over these defaults.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

DEFAULTS = {
    'assignment_change': (
        'Promotion, transfer and manager change',
        [(1, 'manager', 'Reporting manager'),
         (2, 'hr_admin', 'HR'),
         (3, 'exec_director', 'Director')],
    ),
    'salary_revision': (
        'Salary revision',
        [(1, 'manager', 'Reporting manager'),
         (2, 'hr_admin', 'HR'),
         (3, 'exec_director', 'Director')],
    ),
    'status_change': (
        'Employment status change',
        [(1, 'manager', 'Reporting manager'),
         (2, 'hr_admin', 'HR')],
    ),
}


class Command(BaseCommand):
    help = 'Create default approval chains for the HR transactions.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        from attendance.models import ApprovalChain, ApprovalChainStep

        w = self.stdout.write
        apply_it = opts['apply']
        create, keep = [], []

        for rtype, (desc, steps) in DEFAULTS.items():
            existing = ApprovalChain.objects.filter(request_type=rtype,
                                                    company__isnull=True).first()
            (keep if existing else create).append((rtype, desc, steps, existing))

        w('')
        w('APPROVAL CHAINS')
        w('-' * 70)
        for rtype, desc, steps, existing in keep:
            have = list(existing.steps.order_by('sequence').values_list('sequence', 'role_required'))
            w(f'  {rtype:<20}EXISTS — left alone')
            w(f'  {"":<20}{" -> ".join(r for _, r in have) or "NO STEPS (will refuse to submit)"}')
        for rtype, desc, steps, _ in create:
            w(f'  {rtype:<20}CREATE  {desc}')
            w(f'  {"":<20}{" -> ".join(r for _, r, _ in steps)}')

        w('')
        w(f'  to create: {len(create)}   already configured: {len(keep)}')

        if not apply_it:
            w('')
            w(self.style.WARNING('DRY RUN — nothing was written.'))
            return

        with transaction.atomic():
            for rtype, desc, steps, _ in create:
                chain = ApprovalChain.objects.create(
                    request_type=rtype, company=None, description=desc, is_active=True)
                for seq, role, label in steps:
                    ApprovalChainStep.objects.create(
                        chain=chain, sequence=seq, role_required=role, label=label)
        w(self.style.SUCCESS(f'CREATED {len(create)} chain(s).'))
        w('')
        w('Each step waits on a user whose Business Role matches. If nobody holds')
        w('a role, requests will queue at that step rather than skip it.')
