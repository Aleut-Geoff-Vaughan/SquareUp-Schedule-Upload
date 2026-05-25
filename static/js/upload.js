(function () {
    const uploadForm = document.getElementById('upload-form');
    const uploadStatus = document.getElementById('upload-status');
    const verifySection = document.getElementById('verify-section');
    const verifySummary = document.getElementById('verify-summary');
    const verifyTableBody = document.querySelector('#verify-table tbody');
    const approveBtn = document.getElementById('approve-btn');
    const resultsSection = document.getElementById('results-section');
    const resultsSummary = document.getElementById('results-summary');
    const resultsTableBody = document.querySelector('#results-table tbody');

    function setStatus(msg, kind) {
        uploadStatus.textContent = msg;
        uploadStatus.className = 'status ' + (kind || 'info');
    }

    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById('file');
        if (!fileInput.files.length) return;

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);

        setStatus('Uploading...', 'info');

        try {
            const res = await fetch('/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (!res.ok || data.error) {
                setStatus(data.error || 'Upload failed', 'error');
                return;
            }
            setStatus(data.message, 'success');
            await loadVerifyPreview();
        } catch (err) {
            setStatus('Upload failed: ' + err.message, 'error');
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
            '<p><strong>' + data.total_rows + '</strong> rows total · ' +
            '<strong style="color:#155724">' + data.valid_rows + '</strong> valid · ' +
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
            '<p><strong>' + data.total_processed + '</strong> processed · ' +
            '<strong style="color:#155724">' + data.success_count + '</strong> succeeded · ' +
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

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // If a previous flow (AI convert, etc.) staged rows and redirected here, auto-show verify.
    if (new URLSearchParams(window.location.search).get('staged') === '1') {
        setStatus('Loading staged rows for verification...', 'info');
        loadVerifyPreview();
    }
})();
