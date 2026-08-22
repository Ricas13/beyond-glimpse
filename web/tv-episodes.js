// Beyond Glimpse lazy TV season/episode browser.
// Loads season metadata only when a series modal is opened, then loads only the
// selected season's episodes. All text rendering is DOM/textContent based.

(() => {
    if (!window.__beyondGlimpseCatalogueService || window.__beyondGlimpseTvEpisodes) return;

    const originalOpenModal = typeof openModal === 'function' ? openModal : null;
    if (!originalOpenModal) return;

    const seasonPromises = new Map();
    const episodePromises = new Map();
    let modalGeneration = 0;

    function addStyles() {
        if (document.getElementById('bg-tv-episodes-style')) return;
        const style = document.createElement('style');
        style.id = 'bg-tv-episodes-style';
        style.textContent = `
            .bg-episode-browser {
                margin-top: 22px;
                padding-top: 18px;
                border-top: 1px solid rgba(255,255,255,.09);
            }
            .bg-episode-heading {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 12px;
            }
            .bg-episode-title {
                font-size: 1.08rem;
                font-weight: 700;
                color: var(--light-text, #fff);
            }
            .bg-season-tabs {
                display: flex;
                gap: 7px;
                overflow-x: auto;
                scrollbar-width: thin;
                padding: 2px 2px 10px;
                margin-bottom: 4px;
            }
            .bg-season-tab {
                flex: 0 0 auto;
                border: 1px solid rgba(255,255,255,.11);
                border-radius: 999px;
                padding: 7px 12px;
                background: rgba(255,255,255,.055);
                color: var(--light-text, #fff);
                cursor: pointer;
                font: inherit;
                font-size: .84rem;
            }
            .bg-season-tab:hover { background: rgba(255,255,255,.1); }
            .bg-season-tab.active {
                border-color: transparent;
                background: var(--primary-color, #0ea5e9);
                color: #fff;
                font-weight: 700;
            }
            .bg-episode-status {
                padding: 14px 4px;
                color: var(--muted-text, #aaa);
                font-size: .9rem;
            }
            .bg-episode-table-wrap {
                overflow-x: auto;
                border: 1px solid rgba(255,255,255,.08);
                border-radius: 11px;
                background: rgba(0,0,0,.12);
            }
            .bg-episode-table {
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
                color: var(--light-text, #fff);
                font-size: .86rem;
            }
            .bg-episode-table th {
                padding: 9px 10px;
                text-align: left;
                color: var(--muted-text, #aaa);
                font-size: .75rem;
                font-weight: 650;
                text-transform: uppercase;
                letter-spacing: .035em;
                border-bottom: 1px solid rgba(255,255,255,.08);
            }
            .bg-episode-table td {
                padding: 10px;
                vertical-align: top;
                border-bottom: 1px solid rgba(255,255,255,.055);
            }
            .bg-episode-table tr:last-child td { border-bottom: 0; }
            .bg-episode-number { width: 54px; color: var(--muted-text, #aaa); }
            .bg-episode-date { width: 112px; color: var(--muted-text, #aaa); white-space: nowrap; }
            .bg-episode-runtime { width: 76px; color: var(--muted-text, #aaa); white-space: nowrap; }
            .bg-episode-name {
                font-weight: 650;
                line-height: 1.3;
            }
            .bg-episode-overview {
                margin-top: 4px;
                color: var(--muted-text, #aaa);
                font-size: .78rem;
                line-height: 1.35;
                display: -webkit-box;
                -webkit-line-clamp: 2;
                -webkit-box-orient: vertical;
                overflow: hidden;
            }
            @media (max-width: 680px) {
                .bg-episode-date,
                .bg-episode-runtime { display: none; }
                .bg-episode-number { width: 44px; }
                .bg-episode-table { font-size: .82rem; }
                .bg-episode-table th,
                .bg-episode-table td { padding: 9px 8px; }
            }
        `;
        document.head.appendChild(style);
    }

    function ensureSection() {
        const modalBody = document.querySelector('.modal-body');
        if (!modalBody) return null;
        let section = modalBody.querySelector('.bg-episode-browser');
        if (section) return section;

        section = document.createElement('section');
        section.className = 'bg-episode-browser';

        const heading = document.createElement('div');
        heading.className = 'bg-episode-heading';
        const title = document.createElement('div');
        title.className = 'bg-episode-title';
        title.textContent = 'Episodes';
        heading.appendChild(title);

        const tabs = document.createElement('div');
        tabs.className = 'bg-season-tabs';
        tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', 'Seasons');

        const content = document.createElement('div');
        content.className = 'bg-episode-content';

        section.append(heading, tabs, content);
        modalBody.appendChild(section);
        return section;
    }

    function hideSection() {
        const section = document.querySelector('.modal-body .bg-episode-browser');
        if (section) section.remove();
    }

    function status(container, message) {
        container.replaceChildren();
        const line = document.createElement('div');
        line.className = 'bg-episode-status';
        line.textContent = message;
        container.appendChild(line);
    }

    function formatAirDate(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function formatRuntime(milliseconds) {
        const ms = Number(milliseconds || 0);
        if (!ms) return '';
        const minutes = Math.round(ms / 60000);
        return `${minutes} min`;
    }

    function loadSeasons(seriesId) {
        if (!seasonPromises.has(seriesId)) {
            seasonPromises.set(seriesId, fetch(`/api/item/${encodeURIComponent(seriesId)}/seasons`, { cache: 'no-store' })
                .then(response => {
                    if (!response.ok) throw new Error(`seasons HTTP ${response.status}`);
                    return response.json();
                })
                .catch(error => {
                    seasonPromises.delete(seriesId);
                    throw error;
                }));
        }
        return seasonPromises.get(seriesId);
    }

    function loadEpisodes(seriesId, seasonId) {
        const key = `${seriesId}:${seasonId}`;
        if (!episodePromises.has(key)) {
            const url = `/api/item/${encodeURIComponent(seriesId)}/episodes?seasonId=${encodeURIComponent(seasonId)}`;
            episodePromises.set(key, fetch(url, { cache: 'no-store' })
                .then(response => {
                    if (!response.ok) throw new Error(`episodes HTTP ${response.status}`);
                    return response.json();
                })
                .catch(error => {
                    episodePromises.delete(key);
                    throw error;
                }));
        }
        return episodePromises.get(key);
    }

    function renderEpisodes(container, episodes) {
        container.replaceChildren();
        if (!episodes.length) {
            status(container, 'No episodes found for this season.');
            return;
        }

        const wrap = document.createElement('div');
        wrap.className = 'bg-episode-table-wrap';
        const table = document.createElement('table');
        table.className = 'bg-episode-table';

        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        for (const [label, className] of [
            ['#', 'bg-episode-number'],
            ['Episode', ''],
            ['Air date', 'bg-episode-date'],
            ['Runtime', 'bg-episode-runtime'],
        ]) {
            const th = document.createElement('th');
            th.textContent = label;
            if (className) th.className = className;
            headRow.appendChild(th);
        }
        thead.appendChild(headRow);

        const tbody = document.createElement('tbody');
        for (const episode of episodes) {
            const row = document.createElement('tr');

            const number = document.createElement('td');
            number.className = 'bg-episode-number';
            number.textContent = episode.episodeNumber == null ? '—' : String(episode.episodeNumber);

            const main = document.createElement('td');
            const name = document.createElement('div');
            name.className = 'bg-episode-name';
            name.textContent = episode.name || `Episode ${episode.episodeNumber || ''}`.trim();
            main.appendChild(name);
            if (episode.overview) {
                const overview = document.createElement('div');
                overview.className = 'bg-episode-overview';
                overview.textContent = episode.overview;
                main.appendChild(overview);
            }

            const airDate = document.createElement('td');
            airDate.className = 'bg-episode-date';
            airDate.textContent = formatAirDate(episode.airDate);

            const runtime = document.createElement('td');
            runtime.className = 'bg-episode-runtime';
            runtime.textContent = formatRuntime(episode.runtime);

            row.append(number, main, airDate, runtime);
            tbody.appendChild(row);
        }

        table.append(thead, tbody);
        wrap.appendChild(table);
        container.appendChild(wrap);
    }

    async function selectSeason(seriesId, season, tabs, content, generation) {
        for (const button of tabs.querySelectorAll('.bg-season-tab')) {
            button.classList.toggle('active', button.dataset.seasonId === season.id);
            button.setAttribute('aria-selected', button.dataset.seasonId === season.id ? 'true' : 'false');
        }
        status(content, `Loading ${season.name || 'season'}…`);
        try {
            const payload = await loadEpisodes(seriesId, season.id);
            if (generation !== modalGeneration) return;
            renderEpisodes(content, Array.isArray(payload.episodes) ? payload.episodes : []);
        } catch (error) {
            if (generation !== modalGeneration) return;
            console.warn('Could not load TV episodes:', error);
            status(content, 'Episodes are temporarily unavailable.');
        }
    }

    async function renderSeriesBrowser(seriesId, generation) {
        addStyles();
        const section = ensureSection();
        if (!section) return;
        const tabs = section.querySelector('.bg-season-tabs');
        const content = section.querySelector('.bg-episode-content');
        tabs.replaceChildren();
        status(content, 'Loading seasons…');

        try {
            const payload = await loadSeasons(seriesId);
            if (generation !== modalGeneration) return;
            const seasons = Array.isArray(payload.seasons) ? payload.seasons : [];
            if (!seasons.length) {
                status(content, 'No seasons found for this series.');
                return;
            }

            for (const season of seasons) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'bg-season-tab';
                button.dataset.seasonId = season.id;
                button.textContent = season.name || (season.indexNumber == null ? 'Season' : `Season ${season.indexNumber}`);
                button.setAttribute('role', 'tab');
                button.setAttribute('aria-selected', 'false');
                button.addEventListener('click', () => selectSeason(seriesId, season, tabs, content, generation));
                tabs.appendChild(button);
            }

            const initial = seasons.find(season => Number(season.indexNumber) === 1) ||
                seasons.find(season => Number(season.indexNumber) > 0) ||
                seasons[0];
            await selectSeason(seriesId, initial, tabs, content, generation);
        } catch (error) {
            if (generation !== modalGeneration) return;
            console.warn('Could not load TV seasons:', error);
            status(content, 'Season information is temporarily unavailable.');
        }
    }

    openModal = async function beyondGlimpseOpenModalWithEpisodes(item, type) {
        const generation = ++modalGeneration;
        await originalOpenModal(item, type);
        if (generation !== modalGeneration) return;
        if (type !== 'tvshows' || !item || !item.id) {
            hideSection();
            return;
        }
        await renderSeriesBrowser(String(item.id), generation);
    };

    window.__beyondGlimpseTvEpisodes = true;
})();
