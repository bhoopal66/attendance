/* Attendance Report page JavaScript — unified in-house + remote report */

// ============================================
// Live Search (client-side, no page reload)
// ============================================

document.addEventListener('DOMContentLoaded', function () {
    const searchInput = document.getElementById('reportSearchInput');
    if (!searchInput) return;

    const rows = document.querySelectorAll('.emp-row[data-name]');
    const emptyState = document.getElementById('liveSearchEmpty');
    const table = document.querySelector('.emp-table');

    function applyLiveSearch() {
        const term = searchInput.value.trim().toLowerCase();
        let visibleCount = 0;

        rows.forEach(function (row) {
            const match = !term || row.dataset.name.indexOf(term) !== -1;
            row.style.display = match ? '' : 'none';
            if (match) visibleCount++;
        });

        const noMatches = term && visibleCount === 0;
        if (table) table.style.display = noMatches ? 'none' : '';
        if (emptyState) emptyState.style.display = noMatches ? 'flex' : 'none';
    }

    searchInput.addEventListener('input', applyLiveSearch);

    // Filtering is live now — don't let Enter trigger a full page reload
    searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') e.preventDefault();
    });

    applyLiveSearch();
});

// ============================================
// Download Functions
// ============================================

function toggleDownloadMenu() {
    const menu = document.getElementById('downloadMenu');
    menu.classList.toggle('show');
}

// Close dropdown when clicking outside
document.addEventListener('click', function (e) {
    if (!e.target.closest('.download-dropdown')) {
        const menu = document.getElementById('downloadMenu');
        if (menu) menu.classList.remove('show');
    }
});

function downloadEmployeeReport(employeeId, employeeName) {
    const config = window.reportConfig || {};
    const baseUrl = config.downloadEmployeeReportUrl || '/report/download/employee/0/';
    const month = config.selectedMonth;
    const year = config.selectedYear;

    const url = baseUrl.replace('/0/', '/' + employeeId + '/') + '?month=' + month + '&year=' + year;
    const filename = employeeName.replace(/\s+/g, '_') + '_Attendance_' + year + '_' + month + '.xlsx';

    fetch(url)
        .then(response => response.blob())
        .then(blob => {
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);
        })
        .catch(error => console.error('Download failed:', error));
}

function downloadReport() {
    const config = window.reportConfig || {};
    const baseUrl = config.downloadReportUrl || '/report/download/';
    const month = config.selectedMonth;
    const year = config.selectedYear;
    const showInactive = config.showInactive ? '&show_inactive=1' : '';

    const url = baseUrl + '?month=' + month + '&year=' + year + showInactive;
    const filename = 'Attendance_Report_' + year + '_' + month + '.xlsx';

    fetch(url)
        .then(response => response.blob())
        .then(blob => {
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);
        })
        .catch(error => console.error('Download failed:', error));
}

function downloadRemoteEmployeeReport(employeeId, employeeName) {
    const config = window.reportConfig || {};
    const baseUrl = config.downloadRemoteEmployeeReportUrl || '/report/remote/download/employee/0/';
    const month = config.selectedMonth;
    const year = config.selectedYear;

    const url = baseUrl.replace('/0/', '/' + employeeId + '/') + '?month=' + month + '&year=' + year;
    const filename = employeeName.replace(/\s+/g, '_') + '_Remote_Stats_' + year + '_' + month + '.xlsx';

    fetch(url)
        .then(r => r.blob())
        .then(blob => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
        })
        .catch(err => console.error('Download failed:', err));
}

function downloadRemoteReport() {
    const config = window.reportConfig || {};
    const baseUrl = config.downloadRemoteReportUrl || '/report/remote/download/';
    const month = config.selectedMonth;
    const year = config.selectedYear;
    const showInactive = config.showInactive ? '&show_inactive=1' : '';

    const url = baseUrl + '?month=' + month + '&year=' + year + showInactive;
    const filename = 'Remote_Attendance_Report_' + year + '_' + month + '.xlsx';

    fetch(url)
        .then(r => r.blob())
        .then(blob => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
        })
        .catch(err => console.error('Download failed:', err));
}

// ============================================
// Calendar Day Classification
// ============================================

document.addEventListener('DOMContentLoaded', function () {
    const config = window.reportConfig || {};
    const year = config.selectedYear;
    const month = config.selectedMonth;

    document.querySelectorAll('.calendar-day.has-day.no-record').forEach(function (dayEl) {
        if (dayEl.classList.contains('future-day')) return;

        const dayNum = parseInt(dayEl.querySelector('.day-number').textContent);
        const date = new Date(year, month - 1, dayNum);

        if (date.getDay() === 0) { // Sunday
            dayEl.classList.add('sunday');
            const noWorkEl = dayEl.querySelector('.no-work');
            if (noWorkEl) noWorkEl.textContent = 'Holiday';
        }
    });
});

// ============================================
// Edit Modal Functions (in-house)
// ============================================

function openEditModal(employeeId, employeeName, day, firstIn, lastOut, isWfh, isPaidLeave) {
    const modal = document.getElementById('editModal');
    if (!modal) return;

    const config = window.reportConfig || {};
    const selectedYear = config.selectedYear;
    const selectedMonth = config.selectedMonth;

    // Format date as YYYY-MM-DD
    const dateStr = `${selectedYear}-${String(selectedMonth).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const displayDate = new Date(selectedYear, selectedMonth - 1, day).toLocaleDateString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric'
    });

    document.getElementById('modalEmployeeName').textContent = employeeName;
    document.getElementById('modalDate').textContent = displayDate;
    document.getElementById('editEmployeeId').value = employeeId;
    document.getElementById('editDate').value = dateStr;
    document.getElementById('editFirstIn').value = firstIn || '';
    document.getElementById('editLastOut').value = lastOut || '';
    document.getElementById('editWfh').checked = isWfh || false;
    document.getElementById('editPaidLeave').checked = isPaidLeave || false;

    // Clear any previous messages
    const msgEl = document.getElementById('modalMessage');
    msgEl.style.display = 'none';
    msgEl.className = 'modal-message';

    modal.classList.add('show');
}

function closeEditModal() {
    const modal = document.getElementById('editModal');
    if (modal) {
        modal.classList.remove('show');
    }
}

// Handle edit form submission
document.addEventListener('DOMContentLoaded', function () {
    const editForm = document.getElementById('editAttendanceForm');
    if (editForm) {
        editForm.addEventListener('submit', function (e) {
            e.preventDefault();

            const saveBtn = document.getElementById('saveBtn');
            const msgEl = document.getElementById('modalMessage');
            const config = window.reportConfig || {};

            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';

            const formData = {
                employee_id: document.getElementById('editEmployeeId').value,
                date: document.getElementById('editDate').value,
                first_in: document.getElementById('editFirstIn').value || null,
                last_out: document.getElementById('editLastOut').value || null,
                is_work_from_home: document.getElementById('editWfh').checked,
                is_paid_leave: document.getElementById('editPaidLeave').checked
            };

            fetch(config.updateAttendanceUrl || '/api/attendance/update/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify(formData)
            })
                .then(response => response.json())
                .then(data => {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save';

                    if (data.success) {
                        msgEl.className = 'modal-message success';
                        msgEl.textContent = '✓ ' + data.message;
                        msgEl.style.display = 'block';

                        setTimeout(() => { window.location.reload(); }, 1000);
                    } else {
                        msgEl.className = 'modal-message error';
                        msgEl.textContent = '✗ ' + (data.error || 'Failed to save');
                        msgEl.style.display = 'block';
                    }
                })
                .catch(error => {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save';
                    msgEl.className = 'modal-message error';
                    msgEl.textContent = '✗ Network error. Please try again.';
                    msgEl.style.display = 'block';
                });
        });
    }

    // Close modal when clicking outside
    document.getElementById('editModal')?.addEventListener('click', function (e) {
        if (e.target === this) {
            closeEditModal();
        }
    });
});

// ============================================
// Edit Modal Functions (remote)
// ============================================

function openRemoteEditModal(employeeId, employeeName, day, talkMinutes, answeredCalls) {
    const config = window.reportConfig || {};
    const year = config.selectedYear;
    const month = config.selectedMonth;

    const monthStr = String(month).padStart(2, '0');
    const dayStr = String(day).padStart(2, '0');
    const dateStr = year + '-' + monthStr + '-' + dayStr;

    const dateObj = new Date(year, month - 1, day);
    const displayDate = dateObj.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });

    document.getElementById('remoteModalEmployeeName').textContent = employeeName;
    document.getElementById('remoteModalDate').textContent = displayDate;
    document.getElementById('remoteEditEmployeeId').value = employeeId;
    document.getElementById('remoteEditDate').value = dateStr;
    document.getElementById('remoteEditTalkMinutes').value = talkMinutes || 0;
    document.getElementById('remoteEditAnsweredCalls').value = answeredCalls || 0;

    const msg = document.getElementById('remoteModalMessage');
    msg.style.display = 'none';
    msg.className = 'modal-message';

    document.getElementById('remoteEditModal').classList.add('show');
}

function closeRemoteEditModal() {
    document.getElementById('remoteEditModal').classList.remove('show');
}

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('remoteEditForm');
    if (!form) return;

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const config = window.reportConfig || {};
        const saveBtn = document.getElementById('remoteSaveBtn');
        const msg = document.getElementById('remoteModalMessage');

        const payload = {
            employee_id: parseInt(document.getElementById('remoteEditEmployeeId').value),
            date: document.getElementById('remoteEditDate').value,
            talk_minutes: parseInt(document.getElementById('remoteEditTalkMinutes').value) || 0,
            answered_calls: parseInt(document.getElementById('remoteEditAnsweredCalls').value) || 0,
        };

        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';

        fetch(config.updateRemoteAttendanceUrl || '/api/remote/attendance/update/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
            },
            body: JSON.stringify(payload),
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                msg.textContent = 'Saved! Reload to see updated calendar.';
                msg.className = 'modal-message success';
                msg.style.display = 'block';
                setTimeout(function() {
                    closeRemoteEditModal();
                    window.location.reload();
                }, 900);
            } else {
                msg.textContent = data.error || 'Failed to save.';
                msg.className = 'modal-message error';
                msg.style.display = 'block';
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Changes';
            }
        })
        .catch(function() {
            msg.textContent = 'Network error. Please try again.';
            msg.className = 'modal-message error';
            msg.style.display = 'block';
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Changes';
        });
    });

    // Close edit modal on backdrop click
    const remoteEditModalEl = document.getElementById('remoteEditModal');
    if (remoteEditModalEl) {
        remoteEditModalEl.addEventListener('click', function(e) {
            if (e.target === this) closeRemoteEditModal();
        });
    }

    // Esc closes whichever edit modal is open first, before falling through to the
    // calendar modal's own Esc handler
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        const remoteModal = document.getElementById('remoteEditModal');
        const inhouseModal = document.getElementById('editModal');
        if (remoteModal && remoteModal.classList.contains('show')) {
            closeRemoteEditModal();
            e.preventDefault();
        } else if (inhouseModal && inhouseModal.classList.contains('show')) {
            closeEditModal();
            e.preventDefault();
        }
    });
});

// Request Approval Modal functions are defined in base.js
// Do not duplicate them here to avoid overriding the instant approval logic

// ============================================
// Utility Functions
// ============================================

function getCsrfToken() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1].trim() : '';
}
