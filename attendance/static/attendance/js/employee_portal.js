/* Employee Portal JavaScript */

// ====================================
// Early Leave Request Form
// ====================================

document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('earlyLeaveForm');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();

            const formData = new FormData(form);
            const submitBtn = form.querySelector('button[type="submit"]');
            const resultDiv = document.getElementById('submitResult');

            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';

            fetch('/portal/submit-request/', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken()
                },
                body: formData
            })
                .then(response => response.json())
                .then(data => {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Submit Request';

                    if (data.success) {
                        resultDiv.className = 'submit-result success';
                        resultDiv.textContent = data.message;
                        resultDiv.style.display = 'block';
                        form.reset();
                    } else {
                        resultDiv.className = 'submit-result error';
                        resultDiv.textContent = data.error || 'Failed to submit';
                        resultDiv.style.display = 'block';
                    }
                })
                .catch(error => {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Submit Request';
                    resultDiv.className = 'submit-result error';
                    resultDiv.textContent = 'Network error. Please try again.';
                    resultDiv.style.display = 'block';
                });
        });
    }
});

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


// ====================================
// Early Leave Modal Functions
// ====================================

function openEarlyLeaveModal() {
    document.getElementById('earlyLeaveModal').classList.add('show');
}

function closeEarlyLeaveModal() {
    document.getElementById('earlyLeaveModal').classList.remove('show');
    document.getElementById('earlyLeaveForm').reset();
    document.getElementById('formMessage').textContent = '';
    document.getElementById('formMessage').className = 'form-message';
}

async function submitEarlyLeave(event) {
    event.preventDefault();
    const form = document.getElementById('earlyLeaveForm');
    const formData = new FormData(form);
    const messageEl = document.getElementById('formMessage');

    try {
        const response = await fetch('/portal/early-leave-request/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken()
            },
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            messageEl.textContent = data.message;
            messageEl.className = 'form-message success';
            form.reset();
            setTimeout(() => closeEarlyLeaveModal(), 1500);
        } else {
            messageEl.textContent = data.error;
            messageEl.className = 'form-message error';
        }
    } catch (err) {
        messageEl.textContent = 'Network error. Please try again.';
        messageEl.className = 'form-message error';
    }
}


// ====================================
// Leave Request Modal Functions
// ====================================

function openLeaveModal() {
    document.getElementById('leaveModal').classList.add('show');
    // Set minimum date to today

}

function closeLeaveModal() {
    document.getElementById('leaveModal').classList.remove('show');
    document.getElementById('leaveForm').reset();
    document.getElementById('leaveFormMessage').textContent = '';
    document.getElementById('leaveFormMessage').className = 'form-message';
    document.getElementById('documentGroup').style.display = 'none';
    document.getElementById('daysCount').textContent = '0';
}

function toggleDocumentField() {
    const leaveType = document.getElementById('leaveType').value;
    const docGroup = document.getElementById('documentGroup');
    const docInput = document.getElementById('leaveDocument');
    const docLabel = docGroup.querySelector('label');

    if (leaveType === 'medical') {
        // Medical leave - document is required
        docGroup.style.display = 'block';
        docInput.required = true;
        docLabel.textContent = 'Supporting Document *';
    } else if (leaveType === 'sick') {
        // Sick leave - document is optional
        docGroup.style.display = 'block';
        docInput.required = false;
        docLabel.textContent = 'Supporting Document (Optional)';
    } else {
        // Other leave types - no document needed
        docGroup.style.display = 'none';
        docInput.required = false;
        docInput.value = '';
    }
}

function calculateDays() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;

    if (startDate && endDate) {
        const start = new Date(startDate);
        const end = new Date(endDate);
        const diffTime = end - start;
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
        document.getElementById('daysCount').textContent = diffDays > 0 ? diffDays : 0;
    } else {
        document.getElementById('daysCount').textContent = '0';
    }
}

async function submitLeaveRequest(event) {
    event.preventDefault();
    const form = document.getElementById('leaveForm');
    const formData = new FormData(form);
    const messageEl = document.getElementById('leaveFormMessage');
    const submitBtn = form.querySelector('button[type="submit"]');

    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';

    try {
        const response = await fetch('/portal/leave-request/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken()
            },
            body: formData
        });

        const data = await response.json();

        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit Request';

        if (data.success) {
            messageEl.textContent = data.message;
            messageEl.className = 'form-message success';
            form.reset();
            document.getElementById('documentGroup').style.display = 'none';
            document.getElementById('daysCount').textContent = '0';
            setTimeout(() => closeLeaveModal(), 1500);
        } else {
            messageEl.textContent = data.error;
            messageEl.className = 'form-message error';
        }
    } catch (err) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit Request';
        messageEl.textContent = 'Network error. Please try again.';
        messageEl.className = 'form-message error';
    }
}

// Close modals on outside click
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', function (e) {
            if (e.target === this) {
                this.classList.remove('show');
            }
        });
    });
});


// ====================================
// My Requests - Tab Switching and Real-time Updates
// ====================================

// Pagination state
let onDutyOffset = 0;
let leaveOffset = 0;
let onDutyHasMore = false;
let leaveHasMore = false;
const ITEMS_PER_PAGE = 5;

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    document.getElementById(tabName + '-tab').classList.add('active');
}

function updateMyRequests(append = false) {
    const url = `/portal/api/my-requests/?on_duty_offset=${append ? onDutyOffset : 0}&leave_offset=${append ? leaveOffset : 0}&limit=${ITEMS_PER_PAGE}`;

    fetch(url)
        .then(response => response.json())
        .then(data => {
            if (!append) {
                // Reset offsets on fresh load
                onDutyOffset = 0;
                leaveOffset = 0;
            }

            renderOnDutyRequests(data.on_duty, append);
            renderLeaveRequests(data.leave, append);

            onDutyHasMore = data.on_duty_has_more;
            leaveHasMore = data.leave_has_more;
        })
        .catch(error => {
            console.log('Error fetching my requests:', error);
        });
}

function loadMoreOnDuty() {
    onDutyOffset += ITEMS_PER_PAGE;
    fetch(`/portal/api/my-requests/?on_duty_offset=${onDutyOffset}&leave_offset=0&limit=${ITEMS_PER_PAGE}`)
        .then(response => response.json())
        .then(data => {
            renderOnDutyRequests(data.on_duty, true);
            onDutyHasMore = data.on_duty_has_more;
        });
}

function loadMoreLeave() {
    leaveOffset += ITEMS_PER_PAGE;
    fetch(`/portal/api/my-requests/?on_duty_offset=0&leave_offset=${leaveOffset}&limit=${ITEMS_PER_PAGE}`)
        .then(response => response.json())
        .then(data => {
            renderLeaveRequests(data.leave, true);
            leaveHasMore = data.leave_has_more;
        });
}

function renderOnDutyRequests(requests, append = false) {
    const container = document.getElementById('onduty-requests');
    if (!container) return;

    if (!append && requests.length === 0) {
        container.innerHTML = '<div class="empty-requests">📋 No on-duty requests yet</div>';
        return;
    }

    let html = '';
    requests.forEach(req => {
        html += `
            <div class="request-card status-${req.status}">
                <div class="request-header">
                    <span class="request-date">📅 ${req.request_date}</span>
                    <span class="status-badge-display ${req.status}">${req.status}</span>
                </div>
                <div class="request-details">
                    <span>📍 ${req.destination}</span>
                    <span>👤 ${req.customer_name}</span>
                    <span>🕐 ${req.leaving_time}${req.return_time ? ' → ' + req.return_time : ''}</span>
                </div>
            </div>
        `;
    });

    if (append) {
        // Remove existing load more button before appending
        const existingBtn = container.querySelector('.load-more-btn');
        if (existingBtn) existingBtn.remove();
        container.insertAdjacentHTML('beforeend', html);
    } else {
        container.innerHTML = html;
    }

    // Add load more button if there are more
    if (onDutyHasMore) {
        container.insertAdjacentHTML('beforeend', `
            <button class="load-more-btn" onclick="loadMoreOnDuty()">Load More...</button>
        `);
    }
}

function renderLeaveRequests(requests, append = false) {
    const container = document.getElementById('leave-requests');
    if (!container) return;

    if (!append && requests.length === 0) {
        container.innerHTML = '<div class="empty-requests">📋 No leave requests yet</div>';
        return;
    }

    let html = '';
    requests.forEach(req => {
        html += `
            <div class="request-card status-${req.status}">
                <div class="request-header">
                    <span class="request-date">${req.leave_type}</span>
                    <span class="status-badge-display ${req.status}">${req.status}</span>
                </div>
                <div class="request-details">
                    <span>📅 ${req.start_date} to ${req.end_date}</span>
                    <span>📊 ${req.requested_days} day${req.requested_days > 1 ? 's' : ''}</span>
                    ${req.approved_days ? `<span>✅ ${req.approved_days} approved</span>` : ''}
                </div>
                <div class="request-reason">${req.reason}</div>
                ${req.admin_notes ? `<div class="admin-notes">❌ ${req.admin_notes}</div>` : ''}
            </div>
        `;
    });

    if (append) {
        // Remove existing load more button before appending
        const existingBtn = container.querySelector('.load-more-btn');
        if (existingBtn) existingBtn.remove();
        container.insertAdjacentHTML('beforeend', html);
    } else {
        container.innerHTML = html;
    }

    // Add load more button if there are more
    if (leaveHasMore) {
        container.insertAdjacentHTML('beforeend', `
            <button class="load-more-btn" onclick="loadMoreLeave()">Load More...</button>
        `);
    }
}

// Poll for updates - real-time status changes (refresh first 5 items)
document.addEventListener('DOMContentLoaded', function () {
    // Initial load after 1 second
    setTimeout(updateMyRequests, 1000);

    // Then poll every 30 seconds for real-time updates (only latest items)
    setInterval(updateMyRequests, 30000);
});
