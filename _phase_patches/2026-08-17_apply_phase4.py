"""Phase 4 — the HR transactions (§80).

    python 2026-08-17_apply_phase4.py            # show the diff, change nothing
    python 2026-08-17_apply_phase4.py --apply

    attendance/models.py   add revision_type / approved_by / approved_at to
                           SalaryStructure

That is the only model change. Everything else in Phase 4 is new files:
services_transactions.py and the seed_approval_chains command.

Requires Phase 3.
"""
import argparse
import difflib
import io
import os
import re
import sys

MODELS = os.path.join('attendance', 'models.py')
FIELDS = '    revision_type = models.CharField(\n        max_length=30, blank=True, default=\'\',\n        help_text="Annual increment, promotion, market adjustment, correction, "\n                  "other. Free text rather than choices so a new category does "\n                  "not need a migration; the transaction layer supplies it.")\n    approved_by = models.CharField(\n        max_length=150, blank=True, default=\'\',\n        help_text="Who signed it off. Written by the approval engine, not by "\n                  "hand — a name typed into a box is not an approval.")\n    approved_at = models.DateTimeField(null=True, blank=True)\n'
ANCHOR_EXACT = '    revision_reason = models.TextField('
ANCHOR_REGEX = r'^\s*revision_reason = models\.TextField\('


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    opts = ap.parse_args()

    if not os.path.exists(MODELS):
        print('ERROR: run this from the repo root — %s not found.' % MODELS)
        return 1

    raw = open(MODELS, 'rb').read()
    crlf = b'\r\n' in raw
    src = raw.decode('utf-8').replace('\r\n', '\n')

    if 'revision_type = models.CharField(' in src:
        print('Already applied — SalaryStructure already has revision_type.')
        return 0
    if 'class ApprovalRequest(models.Model)' not in src:
        print('ERROR: Phase 3 is not applied — run 2026-08-17_apply_phase3.py first.')
        return 1

    hits = src.count(ANCHOR_EXACT)
    if hits == 1:
        updated = src.replace(ANCHOR_EXACT, FIELDS + ANCHOR_EXACT, 1)
    else:
        lines = src.split('\n')
        idx = [i for i, l in enumerate(lines) if re.match(ANCHOR_REGEX, l)]
        if len(idx) != 1:
            print('ERROR: expected one revision_reason anchor on SalaryStructure, found %d.' % len(idx))
            print('NOTHING CHANGED.')
            return 1
        lines[idx[0]:idx[0]] = FIELDS.rstrip('\n').split('\n')
        updated = '\n'.join(lines)

    print('')
    sys.stdout.writelines(difflib.unified_diff(
        src.splitlines(True), updated.splitlines(True),
        fromfile=MODELS, tofile=MODELS + ' (patched)', n=2))

    if not opts.apply:
        print('')
        print('DRY RUN — nothing was written. Re-run with --apply if the diff is right.')
        return 0

    out = updated.replace('\n', '\r\n') if crlf else updated
    with io.open(MODELS, 'wb') as fh:
        fh.write(out.encode('utf-8'))
    print('PATCHED %s (%s)' % (MODELS, 'CRLF' if crlf else 'LF'))
    print('')
    print('Next:')
    print('  copy 0059_salary_revision_approval.py -> attendance/migrations/')
    print('  copy services_transactions.py         -> attendance/')
    print('  copy seed_approval_chains.py          -> attendance/management/commands/')
    print('  python manage.py makemigrations --check --dry-run attendance')
    print('  python manage.py migrate attendance')
    print('  python manage.py seed_approval_chains        # report first')
    return 0


if __name__ == '__main__':
    sys.exit(main())
