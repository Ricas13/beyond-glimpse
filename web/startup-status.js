// Beyond Glimpse startup status helper.
// v2 can browse already-committed SQLite pages while the lightweight bootstrap
// continues indexing later libraries.

(() => {
    const STATUS_URL = '/catalogue-status.json';
    const API_STATUS_URL = '/api/status';
    const POLL_MS = 3000;
    let readyReloaded = false;
    let partialLoaded = false;
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

    async function tryLoadPartialCatalogue() {
        if (!window.__beyondGlimpseCatalogueService || hasUsableCatalogue() || partialLoaded) return;
        try {
            const response = await fetch(API_STATUS_URL, { cache: 'no-store' });
            if (!response.ok) return;
            const status = await response.json();
            if ((status.movies || 0) + (status.tvShows || 0) <= 0) return;
            partialLoaded = true;
            if (typeof loadMedia === 'function') await loadMedia();
        } catch (_) {
            // The localhost API may still be starting; the next poll retries.
        }
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
                await tryLoadPartialCatalogue();
                setLoadingMessage('Catalogue is being indexed… Available items will appear automatically.');
                schedule();
                return;
            }

            if (state === 'failed') {
                setLoadingMessage('Catalogue preparation failed. Check the Beyond Glimpse container logs.', 'error');
                return;
            }

            if (state === 'ready' && !readyReloaded && !hasUsableCatalogue()) {
                readyReloaded = true;
                if (typeof loadMedia === 'function') await loadMedia();
            }
        } catch (_) {
            schedule();
        }
    }

    setTimeout(poll, 250);
    window.__beyondGlimpseStartupStatus = true;
})();
