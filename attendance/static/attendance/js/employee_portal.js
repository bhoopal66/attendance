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

