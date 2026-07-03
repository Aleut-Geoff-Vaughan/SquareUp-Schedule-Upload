// Automatically attaches the CSRF token to same-origin, state-changing fetch
// requests so the individual page scripts don't each have to. Reads the token
// from the <meta name="csrf-token"> tag rendered by base.html.
(function () {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const token = meta ? meta.getAttribute('content') : null;
    if (!token || !window.fetch) return;

    const SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;
    const originalFetch = window.fetch.bind(window);

    window.fetch = function (input, init) {
        init = init || {};
        const method = (init.method || (typeof input !== 'string' && input && input.method) || 'GET');

        // Only add for same-origin requests.
        let url = typeof input === 'string' ? input : (input && input.url) || '';
        let sameOrigin = true;
        try {
            sameOrigin = new URL(url, window.location.origin).origin === window.location.origin;
        } catch (e) {
            sameOrigin = true;
        }

        if (!SAFE.test(method) && sameOrigin) {
            const headers = new Headers(init.headers || (typeof input !== 'string' && input && input.headers) || {});
            if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
            init.headers = headers;
        }
        return originalFetch(input, init);
    };
})();
