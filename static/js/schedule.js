(function () {
    const form = document.getElementById('filter-form');
    const startInput = document.getElementById('start-date');
    const endInput = document.getElementById('end-date');
    const locationInput = document.getElementById('location-filter');
    const statusEl = document.getElementById('fetch-status');
    const resultsSection = document.getElementById('results-section');
    const resultsTbody = document.querySelector('#results-table tbody');
    const summary = document.getElementById('result-summary');

    function setStatus(msg, kind) {
        statusEl.textContent = msg;
        statusEl.className = 'status ' + (kind || 'info');
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Default range: today through +7 days.
    const today = new Date();
    const plus7 = new Date(today.getTime() + 7 * 86400000);
    function iso(d) {
        return d.getFullYear() + '-' +
            String(d.getMonth() + 1).padStart(2, '0') + '-' +
            String(d.getDate()).padStart(2, '0');
    }
    startInput.value = iso(today);
    endInput.value = iso(plus7);

    // Show date + time pieces from an ISO string like "2026-05-26T09:00:00-04:00".
    function splitIso(s) {
        if (!s) return { date: '', time: '' };
        const m = String(s).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
        return m ? { date: m[1], time: m[2] } : { date: s, time: '' };
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        setStatus('Fetching from Square...', 'info');
        resultsSection.classList.add('hidden');
        try {
            const res = await fetch('/api/schedule/fetch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    start_date: startInput.value,
                    end_date: endInput.value,
                    location_id: locationInput.value,
                }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                setStatus(data.error || 'Fetch failed', 'error');
                return;
            }

            summary.textContent =
                `(${data.count} total · ${data.app_count} from this app · ${data.external_count} external)`;

            resultsTbody.innerHTML = '';
            for (const r of data.shifts) {
                const start = splitIso(r.start_at);
                const end = splitIso(r.end_at);
                const tr = document.createElement('tr');
                if (r.source === 'External') tr.style.background = '#fff3cd';
                tr.innerHTML =
                    '<td>' + escapeHtml(start.date) + '</td>' +
                    '<td>' + escapeHtml(start.time) + '</td>' +
                    '<td>' + escapeHtml(end.time) + '</td>' +
                    '<td>' + escapeHtml(r.location_name) + '</td>' +
                    '<td>' + escapeHtml(r.job_title) + '</td>' +
                    '<td>' + escapeHtml(r.team_member_name) + '</td>' +
                    '<td><span class="badge ' + (r.source === 'App' ? 'completed' : 'failed') + '">' + r.source + '</span></td>' +
                    '<td><code>' + escapeHtml(r.square_shift_id || '') + '</code></td>';
                resultsTbody.appendChild(tr);
            }

            resultsSection.classList.remove('hidden');
            setStatus(`Loaded ${data.count} shifts.`, 'success');
        } catch (err) {
            setStatus('Fetch failed: ' + err.message, 'error');
        }
    });
})();
