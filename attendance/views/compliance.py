"""
Compliance Watchlist — who is out of compliance, and how urgently.

Read-only. Nothing on this page writes, so it can be opened by anyone with the
role without a confirmation dialog standing between them and the answer.
"""

import csv
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.shortcuts import render

from .. import compliance_access as access
from .. import services_compliance as svc
from .utils import section_required

logger = logging.getLogger('attendance')


@login_required
@user_passes_test(section_required('employees'), login_url='/report/')
def compliance_watchlist(request):
    data = svc.watchlist(request.user,
                         include_inactive=request.GET.get('inactive') == '1')
    data.update({
        'tier_labels': svc.TIER_LABELS,
        'document_rows': svc.DOCUMENT_ROWS,
        'role_label': access.ROLE_LABELS.get(data.get('role', ''), ''),
        'include_inactive': request.GET.get('inactive') == '1',
    })
    return render(request, 'attendance/compliance_watchlist.html', data)


@login_required
@user_passes_test(section_required('employees'), login_url='/report/')
def compliance_watchlist_csv(request):
    data = svc.watchlist(request.user,
                         include_inactive=request.GET.get('inactive') == '1')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="compliance_watchlist.csv"'
    writer = csv.writer(response)

    if not data['permitted']:
        # An empty file with a reason beats a file of blank columns that looks
        # like a clean bill of health.
        writer.writerow(['No compliance data is visible to your role.'])
        logger.warning('Compliance CSV refused: user=%s role=%s',
                       request.user.username, data.get('role') or 'none')
        return response

    header = ['Name', 'TCR', 'Type', 'Department', 'Worst status']
    if data['see_expiry']:
        for _t, label, _n in svc.DOCUMENT_ROWS:
            header += [f'{label} expiry', f'{label} status']
    header += ['Compliance review', 'Last reviewed']
    if data['see_probation']:
        header += ['Probation', 'Review due']
    writer.writerow(header)

    for r in data['rows']:
        row = [r['name'], r['tcr'], r['employee_type'], r['department'],
               r['worst_tier_label']]
        if data['see_expiry']:
            for cell in r['documents']:
                row += [cell['expiry'].isoformat() if cell['expiry'] else '',
                        cell['tier_label']]
        row += [r['review_state'],
                r['reviewed_at'].strftime('%Y-%m-%d') if r['reviewed_at'] else '']
        if data['see_probation']:
            row += [r['probation_state'],
                    r['probation_review_due'].isoformat() if r['probation_review_due'] else '']
        writer.writerow(row)

    logger.info('Compliance CSV exported by %s — %s row(s)',
                request.user.username, len(data['rows']))
    return response
