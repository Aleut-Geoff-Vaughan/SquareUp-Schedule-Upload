(function () {
    const container = document.getElementById('env-switch');
    if (!container) return;

    const status = document.getElementById('env-switch-status');

    container.querySelectorAll('button[data-env]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const target = btn.getAttribute('data-env');
            const confirmMsg = target === 'production'
                ? 'Switch to PRODUCTION? Real shifts will be created on your live Square account.'
                : 'Switch to Sandbox (test)?';
            if (!confirm(confirmMsg)) return;

            btn.disabled = true;
            status.textContent = 'Switching...';

            try {
                const res = await fetch('/api/settings/environment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ environment: target })
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    status.textContent = 'Failed: ' + (data.error || res.statusText);
                    btn.disabled = false;
                    return;
                }
                status.textContent = 'Switched to ' + data.environment + '. Reloading...';
                location.reload();
            } catch (err) {
                status.textContent = 'Failed: ' + err.message;
                btn.disabled = false;
            }
        });
    });
})();
