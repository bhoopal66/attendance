"""Add a Business Role selector to User Management.

    python 2026-08-17_apply_role_selector.py            # show the diff, change nothing
    python 2026-08-17_apply_role_selector.py --apply    # write it

THE BUG
-------
The compliance module (migration 0051) added `UserProfile.role`, and
`update_user()` in attendance/views/user_management.py already accepts and logs
it. The User Management page *displays* the resulting role badge. It has no
field to SET it.

So the compliance block on every employee profile says:

    "No compliance data is visible to you. Ask IT Admin to assign your role
     in User Management."

and no IT Admin can, because the control does not exist. Even a superuser sees
nothing. This adds the missing control. No backend change is needed.

WHY IT SHOWS A DIFF FIRST
-------------------------
This patch was written without access to the file — the device bridge was
offline. Every anchor is therefore matched two ways (exact, then
whitespace-tolerant) and the script REFUSES rather than guessing if an anchor
is missing or ambiguous. Read the diff, then re-run with --apply.
"""
import argparse
import difflib
import io
import os
import re
import sys

TARGET = os.path.join('attendance', 'templates', 'attendance', 'user_management.html')

MARKER = 'id="userRole"'

# The role list mirrors UserProfile.ROLE_CHOICES in attendance/models.py.
# Kept as literal markup rather than a {% for %} over a context variable,
# because the view does not currently pass the choices to the template and
# adding that would mean touching the view as well.
FIELD_HTML = '''            <div class="form-group">
                <label for="userRole">Business Role</label>
                <select id="userRole" style="width:100%;padding:8px 10px;border:1px solid var(--border-color, #d1d5db);border-radius:6px;">
                    <option value="">&mdash; none &mdash;</option>
                    <option value="hr_admin">HR Admin</option>
                    <option value="exec_director">Executive Director</option>
                    <option value="manager">Manager</option>
                    <option value="it">IT</option>
                </select>
                <small style="display:block;margin-top:4px;color:#6b7280;">
                    Governs who can see identity numbers, bank details and other
                    compliance fields on an employee profile. Separate from page
                    access &mdash; being able to open a page does not by itself
                    grant sight of those fields. A change here is written to the
                    audit log.
                </small>
            </div>

'''

EDITS = [
    # 1. the field itself, immediately above the Cancel / Save buttons
    dict(
        name='modal field',
        exact='            <div class="um-modal-actions">',
        regex=r'^(\s*)<div class="um-modal-actions">',
        insert='before',
        payload=FIELD_HTML,
    ),
    # 2. carry the role out of the table row's data attributes
    dict(
        name='row reader',
        exact="            sections_restricted: row.dataset.sectionsRestricted === '1',",
        regex=r"^(\s*)sections_restricted:\s*row\.dataset\.sectionsRestricted === '1',",
        insert='after',
        payload="            role: row.dataset.role || '',\n",
    ),
    # 3. populate the select when the edit modal opens
    dict(
        name='modal populate',
        exact="        document.getElementById('userSectionsRestricted').checked = u.sections_restricted;",
        regex=r"^(\s*)document\.getElementById\('userSectionsRestricted'\)\.checked = u\.sections_restricted;",
        insert='after',
        payload="        document.getElementById('userRole').value = u.role || '';\n",
    ),
    # 4. send it. update_user() already reads data['role'].
    dict(
        name='payload',
        exact="            sections_restricted: document.getElementById('userSectionsRestricted').checked,",
        regex=r"^(\s*)sections_restricted:\s*document\.getElementById\('userSectionsRestricted'\)\.checked,",
        insert='after',
        payload="            role: document.getElementById('userRole').value,\n",
    ),
]


def locate(lines, edit):
    """Index of the anchor line, or None. Exact match first, then tolerant."""
    for i, line in enumerate(lines):
        if line.rstrip('\n') == edit['exact']:
            return i, 'exact'
    hits = [i for i, line in enumerate(lines) if re.match(edit['regex'], line)]
    if len(hits) == 1:
        return hits[0], 'regex'
    if len(hits) > 1:
        return None, 'ambiguous (%d matches)' % len(hits)
    return None, 'not found'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write the change; without it, only the diff is shown')
    opts = ap.parse_args()

    if not os.path.exists(TARGET):
        print('ERROR: run this from the repo root — %s not found here.' % TARGET)
        return 1

    raw = open(TARGET, 'rb').read()
    crlf = b'\r\n' in raw
    original = raw.decode('utf-8').replace('\r\n', '\n')

    if MARKER in original:
        print('Already applied — the Business Role selector is already in this file.')
        print('Nothing changed. This is the expected result of a second run.')
        return 0

    lines = original.split('\n')
    lines = [l + '\n' for l in lines[:-1]] + [lines[-1]]

    plan = []
    for edit in EDITS:
        idx, how = locate(lines, edit)
        if idx is None:
            print('ERROR: anchor for "%s" %s.' % (edit['name'], how))
            print('       Looking for: %s' % edit['exact'].strip())
            print('The file is not in the state this patch expects. NOTHING CHANGED.')
            print('Send me the file and I will re-cut the patch against it.')
            return 1
        plan.append((idx, edit, how))
        print('  found %-16s at line %-5d (%s)' % (edit['name'], idx + 1, how))

    # apply bottom-up so earlier indexes stay valid
    new_lines = list(lines)
    for idx, edit, _ in sorted(plan, key=lambda p: -p[0]):
        at = idx if edit['insert'] == 'before' else idx + 1
        new_lines[at:at] = [edit['payload']]

    updated = ''.join(new_lines)

    print('')
    diff = difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                                fromfile=TARGET, tofile=TARGET + ' (patched)', n=2)
    sys.stdout.writelines(diff)
    print('')

    if not opts.apply:
        print('DRY RUN — nothing was written. Re-run with --apply if the diff above is right.')
        return 0

    out = updated.replace('\n', '\r\n') if crlf else updated
    with io.open(TARGET, 'wb') as fh:
        fh.write(out.encode('utf-8'))
    print('PATCHED %s  (line endings preserved: %s)' % (TARGET, 'CRLF' if crlf else 'LF'))
    print('')
    print('No migration and no backend change — update_user() already accepts role.')
    print('Restart the service, open User Management, edit your own user, set')
    print('Business Role = HR Admin, save, then reload an employee profile.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
