// Beyond Glimpse library-aware browse controls.
// Keeps the main Movies/TV Shows tabs as "all" and adds an adjacent dropdown
// for selecting one Jellyfin library. Filtering stays server-side through the
// existing paginated catalogue API.

(() => {
    if (!window.__beyondGlimpseCatalogueService) return;

    const originalFetch = window.fetch.bind(window);
    const selected = { movie: '', tvshow: '' };
    const libraryLists = { movie: [], tvshow: [] };
    const controls = {};

    function normalizeType(value) {
        return value === 'tvshow' || value === 'tvshows' || value === 'series' ? 'tvshow' : 'movie';
    }

    function tabType(type) {
        return type === 'tvshow' ? 'tvshows' : 'movies';
    }

    function labelFor(type) {
        return type === 'tvshow' ? 'TV Shows' : 'Movies';
    }

    // Transparently add the selected library to v2 browse/genre API calls.
    window.fetch = function beyondGlimpseLibraryFetch(input, init) {
        try {
            const raw = typeof input === 'string' ? input : input && input.url;
            if (raw) {
                const url = new URL(raw, window.location.origin);
                if (url.origin === window.location.origin &&
                    (url.pathname === '/api/items' || url.pathname === '/api/genres')) {
                    const type = normalizeType(url.searchParams.get('type'));
                    const libraryId = selected[type];
                    if (libraryId) url.searchParams.set('library', libraryId);
                    else url.searchParams.delete('library');
                    if (typeof input === 'string') {
                        input = url.pathname + url.search + url.hash;
                    } else {
                        input = new Request(url.toString(), input);
                    }
                }
            }
        } catch (error) {
            console.warn('Beyond Glimpse library filter could not decorate request:', error);
        }
        return originalFetch(input, init);
    };

    function addStyles() {
        if (document.getElementById('bg-library-browse-style')) return;
        const style = document.createElement('style');
        style.id = 'bg-library-browse-style';
        style.textContent = `
            .bg-library-tab-wrap {
                position: relative;
                display: inline-flex;
                align-items: stretch;
                flex: 0 0 auto;
            }
            .bg-library-tab-wrap > .tab {
                border-radius: 20px 7px 7px 20px;
                margin-right: 2px;
            }
            .bg-library-arrow {
                border: 0;
                border-radius: 7px 20px 20px 7px;
                min-width: 30px;
                padding: 0 8px;
                background: var(--tab-bg, #333);
                color: var(--light-text, #fff);
                cursor: pointer;
                font: inherit;
                transition: background-color .2s ease, color .2s ease;
            }
            .bg-library-arrow:hover,
            .bg-library-arrow[aria-expanded="true"] {
                background: rgba(255,255,255,.14);
            }
            .bg-library-arrow.has-filter {
                background: var(--primary-color, #0ea5e9);
                color: #fff;
            }
            .bg-library-menu {
                position: absolute;
                top: calc(100% + 8px);
                left: 0;
                z-index: 1200;
                width: max-content;
                min-width: 250px;
                max-width: min(380px, 90vw);
                max-height: min(520px, 70vh);
                overflow-y: auto;
                display: none;
                padding: 7px;
                border: 1px solid rgba(255,255,255,.1);
                border-radius: 12px;
                background: var(--secondary-bg, #222);
                box-shadow: 0 14px 40px rgba(0,0,0,.45);
            }
            .bg-library-menu.open { display: block; }
            .bg-library-option {
                width: 100%;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
                padding: 9px 11px;
                border: 0;
                border-radius: 8px;
                background: transparent;
                color: var(--light-text, #fff);
                text-align: left;
                cursor: pointer;
                font: inherit;
                font-size: .9rem;
            }
            .bg-library-option:hover { background: rgba(255,255,255,.08); }
            .bg-library-option.active {
                background: var(--primary-light, rgba(14,165,233,.16));
                color: var(--primary-color, #38bdf8);
                font-weight: 650;
            }
            .bg-library-count {
                flex: 0 0 auto;
                opacity: .65;
                font-size: .8rem;
            }
            .bg-library-empty {
                padding: 10px 11px;
                color: var(--muted-text, #aaa);
                font-size: .85rem;
            }
            @media (max-width: 768px) {
                .bg-library-tab-wrap > .tab { padding-left: 13px; padding-right: 13px; }
                .bg-library-arrow { min-width: 28px; padding: 0 6px; }
                .bg-library-menu { position: fixed; left: 12px; right: 12px; top: auto; width: auto; max-width: none; }
            }
        `;
        document.head.appendChild(style);
    }

    function closeMenus(exceptType = '') {
        for (const [type, control] of Object.entries(controls)) {
            if (type === exceptType) continue;
            control.menu.classList.remove('open');
            control.arrow.setAttribute('aria-expanded', 'false');
        }
    }

    function option(type, id, name, count) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'bg-library-option';
        button.dataset.libraryId = id;
        button.classList.toggle('active', selected[type] === id);

        const text = document.createElement('span');
        text.textContent = name;
        const badge = document.createElement('span');
        badge.className = 'bg-library-count';
        badge.textContent = Number(count || 0).toLocaleString();
        button.append(text, badge);
        button.addEventListener('click', () => selectLibrary(type, id, name));
        return button;
    }

    function renderMenu(type) {
        const control = controls[type];
        if (!control) return;
        control.menu.replaceChildren();
        const list = libraryLists[type];
        const total = list.reduce((sum, entry) => sum + Number(entry.count || 0), 0);
        control.menu.appendChild(option(type, '', `All ${labelFor(type)}`, total));
        if (!list.length) {
            const empty = document.createElement('div');
            empty.className = 'bg-library-empty';
            empty.textContent = 'No libraries available.';
            control.menu.appendChild(empty);
            return;
        }
        for (const library of list) {
            control.menu.appendChild(option(type, library.id, library.name, library.count));
        }
    }

    function updateControl(type, activeName = '') {
        const control = controls[type];
        if (!control) return;
        const hasFilter = Boolean(selected[type]);
        control.arrow.classList.toggle('has-filter', hasFilter);
        control.arrow.title = hasFilter ? `${labelFor(type)} library: ${activeName}` : `Choose ${labelFor(type).toLowerCase()} library`;
        control.arrow.setAttribute('aria-label', control.arrow.title);
        renderMenu(type);
    }

    function selectLibrary(type, id, name) {
        selected[type] = id;
        updateControl(type, name);
        closeMenus();

        const targetTab = tabType(type);
        if (typeof switchTab === 'function') switchTab(targetTab);

        // Re-run v2 loading so both the item page and genre counts are scoped to
        // the selected library. This is local SQLite work; Jellyfin is untouched.
        setTimeout(() => {
            if (typeof loadMedia === 'function') loadMedia();
        }, 0);
    }

    async function loadLibraryList(type) {
        try {
            const response = await originalFetch(`/api/libraries?type=${encodeURIComponent(type)}`, { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            libraryLists[type] = Array.isArray(payload.libraries) ? payload.libraries : [];
        } catch (error) {
            console.warn(`Could not load ${type} libraries:`, error);
            libraryLists[type] = [];
        }
        renderMenu(type);
    }

    function decorate(type) {
        const tab = document.querySelector(`.tab[data-content="${tabType(type)}"]`);
        if (!tab || tab.closest('.bg-library-tab-wrap')) return;

        const wrapper = document.createElement('div');
        wrapper.className = 'bg-library-tab-wrap';
        tab.parentNode.insertBefore(wrapper, tab);
        wrapper.appendChild(tab);

        const arrow = document.createElement('button');
        arrow.type = 'button';
        arrow.className = 'bg-library-arrow';
        arrow.textContent = '▾';
        arrow.setAttribute('aria-haspopup', 'menu');
        arrow.setAttribute('aria-expanded', 'false');

        const menu = document.createElement('div');
        menu.className = 'bg-library-menu';
        menu.setAttribute('role', 'menu');
        wrapper.append(arrow, menu);
        controls[type] = { tab, arrow, menu };
        updateControl(type);

        arrow.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const opening = !menu.classList.contains('open');
            closeMenus(type);
            menu.classList.toggle('open', opening);
            arrow.setAttribute('aria-expanded', opening ? 'true' : 'false');
        });

        // Clicking the primary Movies/TV Shows pill means "All" for that type.
        tab.addEventListener('click', () => {
            if (!selected[type]) return;
            selected[type] = '';
            updateControl(type);
            closeMenus();
            setTimeout(() => {
                if (typeof loadMedia === 'function') loadMedia();
            }, 0);
        });
    }

    function init() {
        if (!String(document.title || '').toLowerCase().includes('jellyfin')) return;
        addStyles();
        decorate('movie');
        decorate('tvshow');
        loadLibraryList('movie');
        loadLibraryList('tvshow');

        document.addEventListener('click', event => {
            if (!event.target.closest('.bg-library-tab-wrap')) closeMenus();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') closeMenus();
        });
        window.__beyondGlimpseLibraryBrowse = true;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
