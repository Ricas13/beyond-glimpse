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
            item.classList.toggle('active', current === entry.tab && !state.selected[entry.type]);
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
                item.classList.toggle('active',
                    current === library.entry.tab && state.selected[library.entry.type] === library.id);
                scroll.appendChild(item);
            }
        }
        updateTitle();
    }

    function updateTitle() {
        if (!pageTitle) return;
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

    function init() {
        buildTitle();
        buildDrawer();

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
