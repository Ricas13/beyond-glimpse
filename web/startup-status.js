// Beyond Glimpse first-start catalogue status helper.
// Keeps the last good catalogue visible on restarts and gives brand-new installs
// a friendly preparation state while the one-shot Supervisor sync is running.

(() => {
    const STATUS_URL = '/catalogue-status.json';
    const POLL_MS = 3000;
    let readyReloaded = false;
    let sawSyncing = false;
    let timer = null;

    function hasUsableCatalogue() {
        try {
            return (Array.isArray(moviesData) && moviesData.length > 0) ||
                (Array.isArray(tvShowsData) && tvShowsData.length > 0);
        } catch (_) {
            return false;
        }
    }

    function setLoadingMessage(message, className = '') {
        if (hasUsableCatalogue()) return;
        for (const selector of ['#movies-content .loading', '#tvshows-content .loading']) {
            const loading = document.querySelector(selector);
            if (!loading) continue;
            loading.style.display = '';
            loading.replaceChildren();
            const text = document.createElement('div');
            if (className) text.className = className;
            text.textContent = message;
            loading.appendChild(text);
        }
    }

    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(poll, POLL_MS);
    }

    async function poll() {
        try {
            const response = await fetch(STATUS_URL, { cache: 'no-store' });
            if (!response.ok) {
                schedule();
                return;
            }
            const status = await response.json();
            const state = status && status.state;

            if (state === 'starting' || state === 'syncing') {
                sawSyncing = true;
                setLoadingMessage('Catalogue is being prepared… This page will update automatically.');
                schedule();
                return;
            }

            if (state === 'failed') {
                setLoadingMessage('Catalogue preparation failed. Check the Beyond Glimpse container logs.', 'error');
                return;
            }

            // If this page observed the startup refresh, reload the compact indexes
            // once when it completes. Existing data stays visible until this point.
            if (state === 'ready' && sawSyncing && !readyReloaded) {
                readyReloaded = true;
                if (typeof loadMedia === 'function') {
                    await loadMedia();
                }
            }
        } catch (_) {
            schedule();
        }
    }

    setTimeout(poll, 250);
    window.__beyondGlimpseStartupStatus = true;
})();
