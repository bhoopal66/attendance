/* Report page JavaScript - Static version */

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
    // Get config from data attributes
    const config = window.reportConfig || {};
    const baseUrl = config.downloadEmployeeReportUrl || '/report/download/employee/';
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
// Edit Modal Functions
// ============================================

function openEditModal(employeeId, employeeName, day, firstIn, lastOut) {
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
                last_out: document.getElementById('editLastOut').value || null
            };

            fetch(config.updateAttendanceUrl || '/api/update-attendance/', {
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

// Request Approval Modal functions are defined in base.js
// Do not duplicate them here to avoid overriding the instant approval logic

// ============================================
// Utility Functions
// ============================================

function getCsrfToken() {
    const name = 'csrftoken';
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
