// Beyond Glimpse: Jellyfin-style interface helpers.
// Adds the docked navigation drawer, the top-bar page title, and two details-page
// touches (poster-derived backdrop, initials avatars for cast). All styling lives
// in jellyfin-ui.css and is scoped to html.jf-ui. Text is always set with
// textContent; nothing from the API is parsed as HTML.

(() => {
    // The entrypoint marks only Jellyfin pages; a custom Plex/Emby title
    // containing "Jellyfin" must never activate this theme.
    if (!document.documentElement.classList.contains('jf-ui') ||
        window.__beyondGlimpseJellyfinUi) return;
    window.__beyondGlimpseJellyfinUi = true;

    const TYPES = [
        { type: 'movie', tab: 'movies', label: 'Movies', icon: 'jf-ico-movie' },
        { type: 'tvshow', tab: 'tvshows', label: 'TV Shows', icon: 'jf-ico-tv' }
    ];

    let drawer = null;
    let pageTitle = null;
    let liveAvailable = false;
    let livePanel = null;
    const live = { open: false, query: '', offset: 0, hasMore: false, loading: false, total: 0, generation: 0, observer: null, controller: null, progressTimer: null };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function activeTab() {
        const active = document.querySelector('.header .tab.active') || document.querySelector('.tab.active');
        return active && active.dataset.content === 'tvshows' ? 'tvshows' : 'movies';
    }

    function libraryState() {
        return window.__beyondGlimpseLibraryState || { selected: {}, libraryLists: {} };
    }

    function brandName() {
        const heading = document.querySelector('.header h1');
        return (heading && heading.textContent.trim()) || 'Beyond Glimpse';
    }

    function navItem(iconClass, label, count, onClick, key) {
        const button = el('button', 'jf-nav-item');
        button.type = 'button';
        button.dataset.nav = key;
        button.append(el('span', `jf-ico ${iconClass}`), el('span', 'jf-nav-label', label));
        if (count != null) button.append(el('span', 'jf-nav-count', Number(count).toLocaleString()));
        button.addEventListener('click', onClick);
        return button;
    }

    function showTab(tabName) {
        const tab = document.querySelector(`.header .tab[data-content="${tabName}"]`);
        if (tab) tab.click();
    }

    function buildDrawer() {
        drawer = el('nav', 'jf-drawer');
        drawer.setAttribute('aria-label', 'Main navigation');

        const brand = el('div', 'jf-drawer-brand');
        const logo = document.querySelector('.header .logo-icon img');
        if (logo) {
            const image = document.createElement('img');
            image.src = logo.getAttribute('src');
            image.alt = '';
            brand.appendChild(image);
        }
        brand.appendChild(el('span', '', brandName()));

        const scroll = el('div', 'jf-drawer-scroll');
        drawer.append(brand, scroll);
        document.body.appendChild(drawer);
        renderDrawer();
    }

    function renderDrawer() {
        if (!drawer) return;
        const scroll = drawer.querySelector('.jf-drawer-scroll');
        const state = libraryState();
        const current = activeTab();
        scroll.replaceChildren();

        for (const entry of TYPES) {
            const list = state.libraryLists[entry.type] || [];
            const total = list.reduce((sum, library) => sum + Number(library.count || 0), 0);
            const item = navItem(entry.icon, `All ${entry.label}`, list.length ? total : null,
                () => showTab(entry.tab), `all-${entry.tab}`);
            item.classList.toggle('active', !live.open && current === entry.tab && !state.selected[entry.type]);
            scroll.appendChild(item);
        }

        const libraries = TYPES.flatMap(entry =>
            (state.libraryLists[entry.type] || []).map(library => ({ ...library, entry })));
        if (libraries.length) {
            scroll.appendChild(el('div', 'jf-nav-heading', 'Libraries'));
            for (const library of libraries) {
                const item = navItem(library.entry.icon, library.name, library.count, () => {
                    if (typeof window.__beyondGlimpseSelectLibrary === 'function') {
                        window.__beyondGlimpseSelectLibrary(library.entry.type, library.id, library.name);
                    }
                }, `lib-${library.id}`);
                item.classList.toggle('active', !live.open &&
                    current === library.entry.tab && state.selected[library.entry.type] === library.id);
                scroll.appendChild(item);
            }
        }
        if (liveAvailable) {
            scroll.appendChild(el('div', 'jf-nav-heading', 'Live TV'));
            const item = navItem('jf-ico-live', 'Channels', null, openLive, 'live');
            item.classList.toggle('active', live.open);
            scroll.appendChild(item);
        }
        updateTitle();
    }

    function updateTitle() {
        if (!pageTitle) return;
        if (live.open) { pageTitle.textContent = 'Live TV'; return; }
        const state = libraryState();
        const current = TYPES.find(entry => entry.tab === activeTab()) || TYPES[0];
        const selectedId = state.selected[current.type];
        const library = (state.libraryLists[current.type] || []).find(item => item.id === selectedId);
        pageTitle.textContent = library ? library.name : current.label;
    }

    function buildTitle() {
        const content = document.querySelector('.header-content');
        if (!content) return;
        pageTitle = el('div', 'jf-page-title');
        content.insertBefore(pageTitle, content.firstChild);
    }

    // Use the poster as a blurred stand-in backdrop once the details page opens.
    function syncBackdrop() {
        const overlay = document.querySelector('.modal-overlay');
        if (!overlay || !overlay.classList.contains('active')) return;
        const poster = overlay.querySelector('.modal-poster img');
        const backdrop = overlay.querySelector('.modal-backdrop');
        if (!poster || !backdrop || !poster.getAttribute('src')) return;
        const src = poster.getAttribute('src');
        if (!src.startsWith('/poster/') && !src.startsWith('data/') && !src.startsWith('/data/')) return;
        backdrop.style.backgroundImage = `url("${src.replace(/"/g, '%22')}")`;
    }

    function decorateCast() {
        const cast = document.getElementById('modal-cast');
        if (!cast) return;
        for (const item of cast.querySelectorAll('.cast-item')) {
            if (item.querySelector('.jf-avatar')) continue;
            const name = (item.querySelector('.cast-name')?.textContent || '').trim();
            const initials = name.split(/\s+/).filter(Boolean).slice(0, 2)
                .map(part => Array.from(part)[0].toUpperCase()).join('');
            item.insertBefore(el('div', 'jf-avatar', initials || '?'), item.firstChild);
        }
    }

    // Refresh while indexing, even if the grand total has not changed (e.g.
    // items moved between libraries, or a previous list request failed).
    // Schedule only after this invocation completes so requests never overlap.
    async function pollCounts() {
        let settled = false;
        const controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch('/api/status', { cache: 'no-store', signal: controller.signal });
            if (response.ok) {
                const status = await response.json();
                let librariesLoaded = true;
                if (typeof window.__beyondGlimpseReloadLibraries === 'function') {
                    const results = await window.__beyondGlimpseReloadLibraries();
                    librariesLoaded = Array.isArray(results) && results.every(Boolean);
                }
                // Do not permanently stop after a failed final library request.
                settled = Boolean(status.bootstrapComplete) && status.state !== 'bootstrap' && librariesLoaded;
            }
        } catch (_) { /* the API may still be starting; retry later */ }
        finally { clearTimeout(deadline); }
        if (!settled) setTimeout(pollCounts, 8000);
    }


    // ---------------------------------------------------------------- Live TV
    // Browse-only list of Jellyfin Live TV channels with what is on now. The
    // section stays hidden unless /api/channels reports channels are available.
    const PAGE = 60;

    function formatClock(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '' :
            date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    function programProgress(now) {
        const start = Date.parse(now.start);
        const end = Date.parse(now.end);
        if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return null;
        return Math.max(0, Math.min(1, (Date.now() - start) / (end - start)));
    }

    function channelCard(channel) {
        const card = el('div', 'jf-channel');
        const logo = el('div', 'jf-channel-logo');
        const fallback = el('span', '', (channel.name || '?').trim().slice(0, 2).toUpperCase());
        logo.appendChild(fallback);
        if (typeof channel.logo === 'string' &&
            /^\/channel-logo\/[0-9A-Za-z-]+\/[0-9A-Za-z._~-]+\.png$/.test(channel.logo)) {
            const image = document.createElement('img');
            image.loading = 'lazy';
            image.alt = '';
            image.addEventListener('load', () => fallback.remove());
            image.addEventListener('error', () => image.remove());
            image.src = channel.logo;
            logo.appendChild(image);
        }
        const info = el('div', 'jf-channel-info');
        const head = el('div', 'jf-channel-head');
        if (channel.number) head.appendChild(el('span', 'jf-channel-number', channel.number));
        head.appendChild(el('span', 'jf-channel-name', channel.name));
        info.appendChild(head);
        if (channel.now) {
            const title = channel.now.episode ? `${channel.now.title} · ${channel.now.episode}` : channel.now.title;
            info.appendChild(el('div', 'jf-channel-now', title));
            const start = formatClock(channel.now.start);
            const end = formatClock(channel.now.end);
            if (start && end) info.appendChild(el('div', 'jf-channel-time', `${start} – ${end}`));
            const progress = programProgress(channel.now);
            if (progress !== null) {
                const bar = el('div', 'jf-channel-bar');
                const fill = el('i');
                fill.style.width = `${Math.round(progress * 100)}%`;
                bar.appendChild(fill);
                bar.dataset.start = channel.now.start;
                bar.dataset.end = channel.now.end;
                info.appendChild(bar);
            }
        } else {
            info.appendChild(el('div', 'jf-channel-now jf-dim', 'No program information'));
        }
        card.append(logo, info);
        return card;
    }

    function buildLivePanel() {
        livePanel = el('section', 'jf-live');
        livePanel.hidden = true;
        livePanel.setAttribute('aria-label', 'Live TV channels');

        const bar = el('div', 'jf-live-bar');
        const heading = el('div', 'jf-live-title', 'Live TV');
        const count = el('div', 'jf-live-count');
        const search = document.createElement('input');
        search.type = 'search';
        search.className = 'jf-live-search';
        search.placeholder = 'Search channels';
        search.setAttribute('aria-label', 'Search channels');
        let timer = null;
        search.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => { live.query = search.value.trim(); resetLive(); }, 180);
        });
        bar.append(heading, count, search);

        const note = el('div', 'jf-live-note', 'Channel guide only: playback is not available in this app.');
        const grid = el('div', 'jf-live-grid');
        const empty = el('div', 'jf-live-empty');
        empty.hidden = true;
        const sentinel = el('div', 'jf-live-sentinel');
        livePanel.append(bar, note, grid, empty, sentinel);
        document.body.appendChild(livePanel);
        livePanel._parts = { count, grid, empty, sentinel, search };
    }

    async function loadLivePage(generation) {
        if (live.loading || !live.hasMore || generation !== live.generation) return;
        live.loading = true;
        const controller = new AbortController();
        live.controller = controller;
        const { grid, count, empty } = livePanel._parts;
        try {
            const params = new URLSearchParams({ limit: String(PAGE), offset: String(live.offset) });
            if (live.query) params.set('q', live.query);
            const response = await fetch(`/api/channels?${params}`, { cache: 'no-store', signal: controller.signal });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            if (generation !== live.generation || !live.open) return;
            const channels = Array.isArray(payload.channels) ? payload.channels : [];
            const fragment = document.createDocumentFragment();
            channels.forEach(channel => fragment.appendChild(channelCard(channel)));
            grid.appendChild(fragment);
            live.offset += channels.length;
            live.hasMore = Boolean(payload.hasMore) && channels.length > 0;
            live.total = Number(payload.total || 0);
            count.textContent = `${live.total.toLocaleString()} ${live.total === 1 ? 'channel' : 'channels'}`;
            empty.hidden = live.total > 0;
            empty.textContent = live.query ? `No channels match “${live.query}”.` : 'No channels are available.';
        } catch (error) {
            if (generation !== live.generation || !live.open || error.name === 'AbortError') return;
            console.warn('Could not load Live TV channels:', error);
            live.hasMore = false;
            empty.hidden = false;
            empty.textContent = 'Channels are temporarily unavailable.';
        } finally {
            if (generation === live.generation) {
                live.loading = false;
                live.controller = null;
            }
        }
    }

    function resetLive() {
        if (live.controller) live.controller.abort();
        live.controller = null;
        live.generation += 1;
        live.offset = 0;
        live.hasMore = true;
        live.loading = false;
        livePanel._parts.grid.replaceChildren();
        livePanel._parts.empty.hidden = true;
        loadLivePage(live.generation);
    }

    function refreshProgress() {
        if (!live.open || !livePanel) return;
        for (const bar of livePanel.querySelectorAll('.jf-channel-bar')) {
            const progress = programProgress({ start: bar.dataset.start, end: bar.dataset.end });
            if (progress !== null && bar.firstChild) bar.firstChild.style.width = `${Math.round(progress * 100)}%`;
        }
    }

    function openLive() {
        if (!liveAvailable || live.open) return;
        if (!livePanel) buildLivePanel();
        live.open = true;
        live.query = '';
        livePanel._parts.search.value = '';
        livePanel.hidden = false;
        document.documentElement.classList.add('jf-live-open');
        document.querySelectorAll('.jf-livetab').forEach(node => node.classList.add('active'));
        if (!live.observer) {
            live.observer = new IntersectionObserver(entries => {
                if (live.open && entries.some(entry => entry.isIntersecting)) loadLivePage(live.generation);
            }, { rootMargin: '600px 0px' });
            live.observer.observe(livePanel._parts.sentinel);
        }
        resetLive();
        live.progressTimer = setInterval(refreshProgress, 30000);
        renderDrawer();
    }

    function closeLive() {
        if (!live.open) return;
        live.open = false;
        live.generation += 1;
        if (live.controller) live.controller.abort();
        live.controller = null;
        live.loading = false;
        if (live.observer) {
            live.observer.disconnect();
            live.observer = null;
        }
        clearInterval(live.progressTimer);
        live.progressTimer = null;
        livePanel.hidden = true;
        document.documentElement.classList.remove('jf-live-open');
        document.querySelectorAll('.jf-livetab').forEach(node => node.classList.remove('active'));
        renderDrawer();
    }

    function addLiveEntryPoints() {
        // Header tab (mid-width screens) and mobile-menu entry; the drawer has its own.
        const headerTabs = document.querySelector('.header .tabs');
        if (headerTabs) {
            const tab = el('button', 'jf-livetab', 'Live TV');
            tab.type = 'button';
            tab.addEventListener('click', openLive);
            headerTabs.insertBefore(tab, headerTabs.querySelector('.sort-toggle'));
        }
        const mobileTabs = document.querySelector('.mobile-menu .tabs');
        if (mobileTabs) {
            const tab = el('button', 'jf-livetab', 'Live TV');
            tab.type = 'button';
            tab.addEventListener('click', () => {
                openLive();
                document.querySelector('.mobile-menu')?.classList.remove('open');
            });
            mobileTabs.appendChild(tab);
        }
    }

    async function probeLive() {
        try {
            const response = await fetch('/api/channels?limit=1', { cache: 'no-store' });
            if (response.ok) {
                const payload = await response.json();
                liveAvailable = Boolean(payload.available) && Number(payload.total || 0) > 0;
            }
        } catch (_) { /* Jellyfin or catalogue API may still be starting */ }
        if (liveAvailable) {
            document.documentElement.classList.add('jf-live-available');
            renderDrawer();
        } else {
            // A concurrent cold-cache refresh may initially return an empty list.
            setTimeout(probeLive, 60000);
        }
    }

    function init() {
        buildTitle();
        buildDrawer();
        addLiveEntryPoints();
        probeLive();
        pollCounts();

        // Picking any library/tab leaves Live TV; Escape closes it too.
        document.addEventListener('click', event => {
            if (!live.open) return;
            if (event.target.closest('.header .tab, .mobile-menu .tab, .jf-nav-item:not([data-nav="live"])')) closeLive();
        }, true);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && live.open && !document.querySelector('.modal-overlay.active')) closeLive();
        });
        window.addEventListener('bg:library-state', renderDrawer);
        const tabs = document.querySelector('.header .tabs');
        if (tabs) {
            new MutationObserver(renderDrawer).observe(tabs, {
                subtree: true, attributes: true, attributeFilter: ['class']
            });
        }

        const overlay = document.querySelector('.modal-overlay');
        if (overlay) {
            new MutationObserver(syncBackdrop).observe(overlay, { attributes: true, attributeFilter: ['class'] });
        }
        const cast = document.getElementById('modal-cast');
        if (cast) new MutationObserver(decorateCast).observe(cast, { childList: true });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
