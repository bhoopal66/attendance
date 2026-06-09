/* Remote Report page JavaScript */

// ====================================
// Download Functions
// ====================================

function toggleDownloadMenu() {
    const menu = document.getElementById('downloadMenu');
    menu.classList.toggle('show');
}

document.addEventListener('click', function(e) {
    if (!e.target.closest('.download-dropdown')) {
        const menu = document.getElementById('downloadMenu');
        if (menu) menu.classList.remove('show');
    }
});

function downloadRemoteEmployeeReport(employeeId, employeeName) {
    const config = window.remoteReportConfig || {};
    const baseUrl = config.downloadEmployeeReportUrl || '/report/remote/download/employee/0/';
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
    const config = window.remoteReportConfig || {};
    const baseUrl = config.downloadReportUrl || '/report/remote/download/';
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

// ====================================
// Remote Edit Modal
// ====================================

function openRemoteEditModal(employeeId, employeeName, day, talkMinutes, answeredCalls) {
    const config = window.remoteReportConfig || {};
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
        const config = window.remoteReportConfig || {};
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
    const editModal = document.getElementById('remoteEditModal');
    if (editModal) {
        editModal.addEventListener('click', function(e) {
            if (e.target === this) closeRemoteEditModal();
        });
    }

    // Esc closes edit modal (if open), otherwise calendar modal
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        if (document.getElementById('remoteEditModal') &&
            document.getElementById('remoteEditModal').classList.contains('show')) {
            closeRemoteEditModal();
            e.preventDefault();
        }
    });
});

function getCsrfToken() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1].trim() : '';
}
