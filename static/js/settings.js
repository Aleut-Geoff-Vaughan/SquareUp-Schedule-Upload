(function () {
    const config = window.SETTINGS_RESOURCE;
    if (!config) return;

    const addForm = document.getElementById('add-form');

    addForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('name').value.trim();
        const squareId = document.getElementById('square_id').value.trim();
        if (!name || !squareId) return;

        const body = { action: 'add', name };
        body[config.idField] = squareId;

        const tzEl = document.getElementById('timezone');
        if (tzEl) body.timezone = tzEl.value;

        const res = await fetch(config.endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (data.success) {
            location.reload();
        } else {
            alert(data.error || 'Failed to add');
        }
    });

    document.querySelectorAll('.delete-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this item?')) return;
            const id = btn.getAttribute('data-id');
            const res = await fetch(config.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'delete', id: parseInt(id, 10) })
            });
            const data = await res.json();
            if (data.success) {
                location.reload();
            } else {
                alert(data.error || 'Failed to delete');
            }
        });
    });
})();
