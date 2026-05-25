(function () {
    const ollamaForm = document.getElementById('ollama-form');
    const hostInput = document.getElementById('ollama-host');
    const modelInput = document.getElementById('ollama-model');
    const ollamaStatus = document.getElementById('ollama-status');
    const testBtn = document.getElementById('test-btn');

    const convertForm = document.getElementById('convert-form');
    const convertFile = document.getElementById('convert-file');
    const convertStatus = document.getElementById('convert-status');

    const previewSection = document.getElementById('preview-section');
    const csvPreview = document.getElementById('csv-preview');
    const downloadBtn = document.getElementById('download-btn');
    const stageBtn = document.getElementById('stage-btn');
    const stageStatus = document.getElementById('stage-status');

    function setStatus(el, msg, kind) {
        el.textContent = msg;
        el.className = 'status ' + (kind || 'info');
    }

    ollamaForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        setStatus(ollamaStatus, 'Saving...', 'info');
        const res = await fetch('/api/settings/ollama', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host: hostInput.value.trim(), model: modelInput.value.trim() }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
            setStatus(ollamaStatus, data.error || 'Save failed', 'error');
            return;
        }
        setStatus(ollamaStatus, 'Saved.', 'success');
    });

    testBtn.addEventListener('click', async () => {
        setStatus(ollamaStatus, 'Pinging Ollama...', 'info');
        // Save first so the test uses the values currently in the form.
        await fetch('/api/settings/ollama', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host: hostInput.value.trim(), model: modelInput.value.trim() }),
        });
        const res = await fetch('/api/ollama/test', { method: 'POST' });
        const data = await res.json();
        if (!data.success) {
            setStatus(ollamaStatus, data.error || 'Connection failed', 'error');
            return;
        }
        const models = (data.models || []).join(', ') || '(no models installed)';
        setStatus(ollamaStatus, `Connected to ${data.host}. Installed models: ${models}`, 'success');
    });

    convertForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!convertFile.files.length) return;
        const fd = new FormData();
        fd.append('file', convertFile.files[0]);

        setStatus(convertStatus, 'Sending to Ollama... this can take 10-60 seconds.', 'info');
        previewSection.classList.add('hidden');

        try {
            const res = await fetch('/admin/ai-convert/upload', { method: 'POST', body: fd });
            const data = await res.json();
            if (!res.ok || !data.success) {
                setStatus(convertStatus, data.error || 'Conversion failed', 'error');
                return;
            }
            setStatus(convertStatus, `Done. Model: ${data.model}. Review the output below.`, 'success');
            csvPreview.value = data.csv || '';
            previewSection.classList.remove('hidden');
            csvPreview.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch (err) {
            setStatus(convertStatus, 'Conversion failed: ' + err.message, 'error');
        }
    });

    downloadBtn.addEventListener('click', () => {
        const blob = new Blob([csvPreview.value], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'ai-converted-schedule.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });

    stageBtn.addEventListener('click', async () => {
        if (!csvPreview.value.trim()) {
            setStatus(stageStatus, 'Nothing to stage.', 'error');
            return;
        }
        setStatus(stageStatus, 'Staging...', 'info');
        try {
            const res = await fetch('/admin/ai-convert/stage', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ csv: csvPreview.value }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                setStatus(stageStatus, data.error || 'Stage failed', 'error');
                return;
            }
            setStatus(stageStatus, `${data.message} Redirecting to Verify...`, 'success');
            setTimeout(() => { window.location.href = data.redirect || '/upload'; }, 800);
        } catch (err) {
            setStatus(stageStatus, 'Stage failed: ' + err.message, 'error');
        }
    });
})();
