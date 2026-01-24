/* Base JavaScript - Global functionality across all pages */

// ============================================
// Requests Dropdown Toggle
// ============================================

function toggleRequestsDropdown() {
    const dropdown = document.getElementById('requestsDropdown');
    dropdown.classList.toggle('show');
}

// Close dropdown when clicking outside
document.addEventListener('click', function (e) {
    if (!e.target.closest('.sidebar-dropdown') && !e.target.closest('.nav-dropdown')) {
        const dropdown = document.getElementById('requestsDropdown');
        if (dropdown) dropdown.classList.remove('show');
    }
});

// ============================================
// Real-time Pending Count and Dropdown Updates
// ============================================

function updatePendingCount() {
    const badge = document.getElementById('pendingBadge');
    const dropdownContent = document.getElementById('requestsDropdown');

    if (!badge) return; // Not on a page with the badge

    fetch('/api/pending-requests/')
        .then(response => response.json())
        .then(data => {
            const count = data.count;
            const requests = data.requests;

            // Update badge count
            badge.textContent = count;
            if (count > 0) {
                badge.style.display = 'flex';
            } else {
                badge.style.display = 'none';
            }

            // Update dropdown content dynamically
            if (dropdownContent) {
                updateDropdownContent(requests, count);
            }
        })
        .catch(error => {
            console.log('Error fetching pending requests:', error);
        });
}

function updateDropdownContent(requests, count) {
    const dropdownContent = document.getElementById('requestsDropdown');
    if (!dropdownContent) return;

    if (count > 0) {
        let html = `
            <div class="dropdown-header">
                <span>Pending Requests</span>
                <span class="dropdown-badge">${count}</span>
            </div>
            <div class="dropdown-content">
        `;

        requests.forEach(req => {
            html += `
                <div class="dropdown-request">
                    <div class="dropdown-request-header">
                        <strong>${req.employee_name}</strong>
                        <span class="dropdown-date">${req.request_date}</span>
                    </div>
                    <div class="dropdown-request-details">
                        <span>📍 ${req.destination}</span>
                        <span>👤 ${req.customer_name}</span>
                    </div>
                    <div class="dropdown-request-info">
                        <span>🕐 ${req.leaving_time}</span>
                        ${req.return_time ? `<span>→ ${req.return_time}</span>` : ''}
                    </div>
                    <div class="dropdown-request-actions">
                        <button class="btn-sm-approve" onclick="openApprovalModal(${req.id})">✓ Approve</button>
                        <button class="btn-sm-decline" onclick="declineRequest(${req.id})">✗ Decline</button>
                    </div>
                </div>
            `;
        });

        html += '</div>';
        dropdownContent.innerHTML = html;
    } else {
        dropdownContent.innerHTML = `
            <div class="dropdown-empty">
                <span>✓ No pending requests</span>
            </div>
        `;
    }
}

// Poll for updates every 30 seconds
document.addEventListener('DOMContentLoaded', function () {
    // Initial check after 2 seconds
    setTimeout(updatePendingCount, 2000);

    // Then poll every 30 seconds
    setInterval(updatePendingCount, 30000);
});

// ============================================
// Request Approval Modal Functions
// ============================================

function openApprovalModal(requestId) {
    fetch(`/request/${requestId}/data/`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                alert('Error: ' + (data.error || 'Failed to load request data'));
                return;
            }

            document.getElementById('currentRequestId').value = requestId;
            document.getElementById('approvalEmployeeName').textContent = data.employee_name;
            document.getElementById('approvalRequestDate').textContent = data.request_date;
            document.getElementById('approvalDestination').textContent = data.destination;
            document.getElementById('approvalCustomer').textContent = data.customer_name;

            if (data.reason) {
                document.getElementById('approvalReason').textContent = data.reason;
                document.getElementById('approvalReasonRow').style.display = 'flex';
            } else {
                document.getElementById('approvalReasonRow').style.display = 'none';
            }

            const noDataWarning = document.getElementById('noDataWarning');
            const timeEditSection = document.getElementById('timeEditSection');
            const remoteNotice = document.getElementById('remoteNotice');
            const approveBtn = document.getElementById('approveBtn');

            if (data.employee_type === 'remote') {
                noDataWarning.style.display = 'none';
                timeEditSection.style.display = 'none';
                remoteNotice.style.display = 'flex';
                approveBtn.disabled = false;  // Allow instant approval for remote too
            } else {
                remoteNotice.style.display = 'none';

                // Always show the time edit section and enable approval
                // Instant approval is now supported even without biometric data
                timeEditSection.style.display = 'block';
                approveBtn.disabled = false;

                if (data.has_data) {
                    noDataWarning.style.display = 'none';
                    document.getElementById('originalFirstIn').textContent = data.first_in || '--:--';
                    document.getElementById('originalLastOut').textContent = data.last_out || '--:--';
                    document.getElementById('newFirstIn').value = data.first_in || '';

                    // Pre-fill last out: use the later of return_time or last_out
                    let newLastOutValue = data.last_out || '';
                    if (data.return_time && data.last_out) {
                        newLastOutValue = (data.return_time > data.last_out) ? data.return_time : data.last_out;
                    } else if (data.return_time && !data.last_out) {
                        newLastOutValue = data.return_time;
                    }
                    document.getElementById('newLastOut').value = newLastOutValue;
                } else {
                    // No biometric data - still allow approval, no warning needed
                    noDataWarning.style.display = 'none';
                    document.getElementById('originalFirstIn').textContent = '--:--';
                    document.getElementById('originalLastOut').textContent = '--:--';
                    document.getElementById('newFirstIn').value = data.leaving_time || '';
                    document.getElementById('newLastOut').value = data.return_time || '';
                }
            }

            document.getElementById('approvalMessage').style.display = 'none';
            document.getElementById('approvalModal').classList.add('show');

            // Close the requests dropdown
            const dropdown = document.getElementById('requestsDropdown');
            if (dropdown) dropdown.classList.remove('show');
        })
        .catch(error => {
            console.error('Error fetching request data:', error);
            alert('Network error. Please try again.');
        });
}

function closeApprovalModal() {
    document.getElementById('approvalModal').classList.remove('show');
}

function submitApproval() {
    const requestId = document.getElementById('currentRequestId').value;
    const approveBtn = document.getElementById('approveBtn');
    const msgEl = document.getElementById('approvalMessage');

    const newFirstIn = document.getElementById('newFirstIn')?.value || '';
    const newLastOut = document.getElementById('newLastOut')?.value || '';

    approveBtn.disabled = true;
    approveBtn.textContent = 'Approving...';

    const formData = new FormData();
    formData.append('new_first_in', newFirstIn);
    formData.append('new_last_out', newLastOut);

    fetch(`/request/${requestId}/approve/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() },
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            approveBtn.disabled = false;
            approveBtn.textContent = '✓ Approve & Update';

            if (data.success) {
                msgEl.className = 'approval-message success';
                msgEl.textContent = '✓ ' + data.message;
                msgEl.style.display = 'block';
                setTimeout(() => { window.location.reload(); }, 1000);
            } else {
                msgEl.className = 'approval-message error';
                msgEl.textContent = '✗ ' + (data.error || 'Failed to approve');
                msgEl.style.display = 'block';
            }
        })
        .catch(error => {
            approveBtn.disabled = false;
            approveBtn.textContent = '✓ Approve & Update';
            msgEl.className = 'approval-message error';
            msgEl.textContent = '✗ Network error. Please try again.';
            msgEl.style.display = 'block';
        });
}

function declineRequest(requestId) {
    if (!confirm('Are you sure you want to decline this request?')) {
        return;
    }

    // Close the requests dropdown
    const dropdown = document.getElementById('requestsDropdown');
    if (dropdown) dropdown.classList.remove('show');

    fetch(`/request/${requestId}/decline/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() }
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Error: ' + (data.error || 'Failed to decline'));
            }
        })
        .catch(error => {
            alert('Network error. Please try again.');
        });
}

// Close approval modal when clicking outside
document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('approvalModal')?.addEventListener('click', function (e) {
        if (e.target === this) {
            closeApprovalModal();
        }
    });
});

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
