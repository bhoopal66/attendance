"""Add BaseEmployee.labour_jurisdiction to attendance/models.py.

Run from the repo root:

    python 2026-08-17_apply_labour_jurisdiction.py

Safe to run twice — it detects its own marker and stops. It edits ONE file and
prints the lines it inserted so the change can be read before committing.

Written as a script rather than applied directly because the device bridge was
offline when this was built. If the bridge is back, the same edit can be made in
place instead; the result is identical.
"""
import io
import os
import sys

TARGET = os.path.join('attendance', 'models.py')

MARKER = 'labour_jurisdiction = models.CharField('

ANCHOR = '    visa_provider = models.CharField('

BLOCK = '''    LABOUR_JURISDICTION_CHOICES = [
        ('', '— not recorded —'),
        ('mohre', 'MOHRE — UAE mainland'),
        ('uae_own_visa', 'UAE — own / spouse visa'),
        ('offshore', 'Non-UAE / offshore'),
        ('other', 'Other'),
    ]
    labour_jurisdiction = models.CharField(
        max_length=20, choices=LABOUR_JURISDICTION_CHOICES, blank=True,
        default='', db_index=True,
        help_text="Which employment regime this person actually falls under. "
                  "NOT a formality: MOHRE staff are covered by UAE labour law "
                  "(Article 29 leave pay, gratuity, WPS); offshore staff working "
                  "from India or Nepal are not covered by any of it and are "
                  "governed by their contract instead. "
                  "Deliberately BLANK by default rather than defaulting to "
                  "MOHRE — a wrong jurisdiction stamped on the whole workforce "
                  "is worse than an empty one, because it looks like a decision "
                  "somebody made. Statutory rules are NOT yet gated on this "
                  "field (bhoopal, 17 Aug 2026): it records the truth now so "
                  "gating can be switched on later without re-deriving who is "
                  "who, one employee at a time, from memory.",
    )

'''


def main():
    if not os.path.exists(TARGET):
        print('ERROR: run this from the repo root — %s not found here.' % TARGET)
        return 1

    raw = open(TARGET, 'rb').read()
    crlf = b'\r\n' in raw
    src = raw.decode('utf-8').replace('\r\n', '\n')

    if MARKER in src:
        print('Already applied — labour_jurisdiction is already on BaseEmployee.')
        print('Nothing changed. This is the expected result of a second run.')
        return 0

    hits = src.count(ANCHOR)
    if hits != 1:
        print('ERROR: expected exactly one "%s", found %d.' % (ANCHOR.strip(), hits))
        print('The file is not in the state this patch expects. Nothing changed.')
        return 1

    src = src.replace(ANCHOR, BLOCK + ANCHOR, 1)
    out = src.replace('\n', '\r\n') if crlf else src
    with io.open(TARGET, 'wb') as fh:
        fh.write(out.encode('utf-8'))

    print('PATCHED %s  (line endings preserved: %s)' % (TARGET, 'CRLF' if crlf else 'LF'))
    print('Inserted immediately before visa_provider on BaseEmployee:')
    for line in BLOCK.rstrip('\n').split('\n')[:9]:
        print('   ' + line)
    print('   ... (help_text continues)')
    print('')
    print('Next:')
    print('  1. copy 0055_labour_jurisdiction.py into attendance/migrations/')
    print('  2. copy set_labour_jurisdiction.py into attendance/management/commands/')
    print('  3. python manage.py makemigrations --check --dry-run attendance'
          '   # must say "No changes detected"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
