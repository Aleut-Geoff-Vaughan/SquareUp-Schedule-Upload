(function () {
    const addForm = document.getElementById('add-user-form');

    addForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;
        const is_admin = document.getElementById('is_admin').checked;
        if (!username || !password) return;

        const res = await fetch('/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add', username, password, is_admin })
        });
        const data = await res.json();
        if (data.success) {
            location.reload();
        } else {
            alert(data.error || 'Failed to add user');
        }
    });

    document.querySelectorAll('.delete-user-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this user?')) return;
            const id = btn.getAttribute('data-id');
            const res = await fetch('/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'delete', id: parseInt(id, 10) })
            });
            const data = await res.json();
            if (data.success) {
                location.reload();
            } else {
                alert(data.error || 'Failed to delete user');
            }
        });
    });
})();
