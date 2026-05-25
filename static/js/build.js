(function () {
    const rows = [];
    const tzMap = window.LOCATION_TIMEZONES || {};

    const form = document.getElementById('shift-form');
    const tbody = document.getElementById('staged-rows');
    const rowCount = document.getElementById('row-count');
    const submitBtn = document.getElementById('submit-btn');
    const submitStatus = document.getElementById('submit-status');

    const verifySection = document.getElementById('verify-section');
    const verifySummary = document.getElementById('verify-summary');
    const verifyTableBody = document.querySelector('#verify-table tbody');
    const approveBtn = document.getElementById('approve-btn');
    const resultsSection = document.getElementById('results-section');
    const resultsSummary = document.getElementById('results-summary');
    const resultsTableBody = document.querySelector('#results-table tbody');

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function render() {
        tbody.innerHTML = '';
        rows.forEach((r, idx) => {
            const tr = document.createElement('tr');
            tr.innerHTML =
                '<td>' + (idx + 1) + '</td>' +
                '<td>' + escapeHtml(r.location_name) + '</td>' +
                '<td>' + escapeHtml(r.employee_name || '(open)') + '</td>' +
                '<td>' + escapeHtml(r.job_title) + '</td>' +
                '<td>' + escapeHtml(r.shift_date) + '</td>' +
                '<td>' + escapeHtml(r.start_time) + '</td>' +
                '<td>' + escapeHtml(r.end_time) + '</td>' +
                '<td>' + escapeHtml(r.timezone_offset || '(location)') + '</td>' +
                '<td><button type="button" class="btn danger" data-idx="' + idx + '">Remove</button></td>';
            tbody.appendChild(tr);
        });
        rowCount.textContent = String(rows.length);
        submitBtn.disabled = rows.length === 0;
        tbody.querySelectorAll('button[data-idx]').forEach((btn) => {
            btn.addEventListener('click', () => {
                rows.splice(parseInt(btn.getAttribute('data-idx'), 10), 1);
                render();
            });
        });
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const location = document.getElementById('f-location').value.trim();
        const job = document.getElementById('f-job').value.trim();
        const employee = document.getElementById('f-employee').value.trim();
        const date = document.getElementById('f-date').value;
        const start = document.getElementById('f-start').value;
        const end = document.getElementById('f-end').value;

        if (!location || !job || !date || !start || !end) return;

        rows.push({
            location_name: location,
            employee_name: employee,
            job_title: job,
            shift_date: date,
            start_time: start,
            end_time: end,
            timezone_offset: tzMap[location] || '',
        });
        render();

        // Keep location/job/date for fast multi-entry; clear the per-shift fields.
        document.getElementById('f-employee').value = '';
        document.getElementById('f-start').value = '';
        document.getElementById('f-end').value = '';
        document.getElementById('f-employee').focus();
    });

    function setStatus(msg, kind) {
        submitStatus.textContent = msg;
        submitStatus.className = 'status ' + (kind || 'info');
    }

    submitBtn.addEventListener('click', async () => {
        if (!rows.length) return;
        submitBtn.disabled = true;
        setStatus('Submitting ' + rows.length + ' rows...', 'info');
        try {
            const res = await fetch('/upload/build', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rows })
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                setStatus(data.error || 'Submit failed', 'error');
                submitBtn.disabled = false;
                return;
            }
            setStatus(data.message, 'success');
            await loadVerifyPreview();
        } catch (err) {
            setStatus('Submit failed: ' + err.message, 'error');
            submitBtn.disabled = false;
        }
    });

    async function loadVerifyPreview() {
        const res = await fetch('/api/verify-preview');
        const data = await res.json();
        if (data.error) {
            setStatus(data.error, 'error');
            return;
        }

        verifySummary.innerHTML =
            '<p><strong>' + data.total_rows + '</strong> rows total &middot; ' +
            '<strong style="color:#155724">' + data.valid_rows + '</strong> valid &middot; ' +
            '<strong style="color:#721c24">' + data.invalid_rows + '</strong> invalid</p>';

        verifyTableBody.innerHTML = '';
        for (const row of data.rows) {
            const tr = document.createElement('tr');
            if (!row.is_valid) tr.className = 'invalid';
            tr.innerHTML =
                '<td>' + row.row_number + '</td>' +
                '<td>' + escapeHtml(row.employee_name || '') + '</td>' +
                '<td>' + escapeHtml(row.job_title || '') + '</td>' +
                '<td>' + escapeHtml(row.location_name || '') + '</td>' +
                '<td>' + escapeHtml(row.shift_date || '') + '</td>' +
                '<td>' + escapeHtml(row.start_time || '') + '</td>' +
                '<td>' + escapeHtml(row.end_time || '') + '</td>' +
                '<td>' + (row.is_valid ? '<span class="badge completed">Valid</span>' : '<span class="badge failed">Invalid</span>') + '</td>' +
                '<td>' + escapeHtml((row.errors || []).join('; ')) + '</td>';
            verifyTableBody.appendChild(tr);
        }

        verifySection.classList.remove('hidden');
        approveBtn.disabled = data.valid_rows === 0;
    }

    approveBtn.addEventListener('click', async () => {
        if (!confirm('Send approved shifts to Square? This cannot be undone.')) return;
        approveBtn.disabled = true;
        approveBtn.textContent = 'Processing...';
        try {
            const res = await fetch('/api/process-schedules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approve: true })
            });
            const data = await res.json();
            if (data.error) {
                setStatus(data.error, 'error');
                approveBtn.disabled = false;
                approveBtn.textContent = 'Approve & Send to Square';
                return;
            }
            renderResults(data);
        } catch (err) {
            setStatus('Processing failed: ' + err.message, 'error');
            approveBtn.disabled = false;
            approveBtn.textContent = 'Approve & Send to Square';
        }
    });

    function renderResults(data) {
        resultsSummary.innerHTML =
            '<p>Upload ID: <strong>' + data.upload_id + '</strong></p>' +
            '<p><strong>' + data.total_processed + '</strong> processed &middot; ' +
            '<strong style="color:#155724">' + data.success_count + '</strong> succeeded &middot; ' +
            '<strong style="color:#721c24">' + data.error_count + '</strong> failed</p>';

        resultsTableBody.innerHTML = '';
        for (const r of data.results) {
            const tr = document.createElement('tr');
            tr.innerHTML =
                '<td>' + r.row + '</td>' +
                '<td>' + (r.status === 'SUCCESS' ? '<span class="badge completed">OK</span>' : '<span class="badge failed">ERR</span>') + '</td>' +
                '<td>' + (r.shift_id ? '<code>' + escapeHtml(r.shift_id) + '</code>' : '-') + '</td>' +
                '<td>' + escapeHtml(r.message || '') + '</td>';
            resultsTableBody.appendChild(tr);
        }

        resultsSection.classList.remove('hidden');
        verifySection.classList.add('hidden');
    }

    render();
})();
