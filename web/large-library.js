// Beyond Glimpse ultra-light large-library runtime.
// Keeps the inherited UI but replaces its expensive data/rendering hot paths.

(() => {
    const DEFAULT_BATCH_SIZE = 96;
    const MOBILE_BATCH_SIZE = 48;
    const SEARCH_DEBOUNCE_MS = 140;
    const PRELOAD_MARGIN = '1200px 0px';
    const originalLoadSource = typeof loadMedia === 'function' ? String(loadMedia) : '';

    let renderGeneration = 0;
    let loadMoreObserver = null;
    let filterTimer = null;
    let dataBase = null;
    let serverType = null;
    const detailShardCache = new Map();

    function batchSize() {
        return window.innerWidth < 768 ? MOBILE_BATCH_SIZE : DEFAULT_BATCH_SIZE;
    }

    function hintedServerType() {
        for (const type of ['jellyfin', 'plex', 'emby']) {
            if (originalLoadSource.includes(`data/${type}/movies.json`) ||
                originalLoadSource.includes(`data/${type}/tvshows.json`)) {
                return type;
            }
        }
        const path = window.location.pathname.toLowerCase();
        if (path.startsWith('/jellyfin/')) return 'jellyfin';
        if (path.startsWith('/plex/')) return 'plex';
        if (path.startsWith('/emby/')) return 'emby';
        return null;
    }

    async function loadIndexesFrom(base, type) {
        const [moviesResponse, tvResponse] = await Promise.all([
            fetch(`${base}/movies.json`, { cache: 'no-cache' }),
            fetch(`${base}/tvshows.json`, { cache: 'no-cache' })
        ]);
        if (!moviesResponse.ok || !tvResponse.ok) {
            throw new Error(`catalogue unavailable at ${base}`);
        }
        const [movies, tvshows] = await Promise.all([
            moviesResponse.json(),
            tvResponse.json()
        ]);
        if (!Array.isArray(movies) || !Array.isArray(tvshows)) {
            throw new Error(`invalid catalogue at ${base}`);
        }
        dataBase = base;
        serverType = type;
        return { movies, tvshows };
    }

    async function resolveIndexes() {
        const hinted = hintedServerType();
        if (hinted) {
            return loadIndexesFrom(`/data/${hinted}`, hinted);
        }

        let lastError = null;
        for (const type of ['jellyfin', 'plex', 'emby']) {
            try {
                return await loadIndexesFrom(`/data/${type}`, type);
            } catch (error) {
                lastError = error;
            }
        }
        throw lastError || new Error('no configured catalogue found');
    }

    function shardKey(itemId) {
        const value = String(itemId || '').toLowerCase();
        return /^[0-9a-f]{2}/.test(value) ? value.slice(0, 2) : 'zz';
    }

    async function loadDetails(item, type) {
        if (serverType !== 'jellyfin') return item;
        const shard = shardKey(item.id);
        const plural = type === 'movies' ? 'movies' : 'tvshows';
        const cacheKey = `${plural}:${shard}`;

        if (!detailShardCache.has(cacheKey)) {
            detailShardCache.set(cacheKey, (async () => {
                const response = await fetch(`${dataBase}/details/${plural}/${shard}.json`, { cache: 'no-cache' });
                if (!response.ok) return {};
                const payload = await response.json();
                return payload && typeof payload === 'object' ? payload : {};
            })());
        }

        const shardData = await detailShardCache.get(cacheKey);
        return Object.assign({}, item, shardData[item.id] || {});
    }

    function posterUrl(item, type) {
        if (serverType === 'jellyfin') {
            if (!item.posterTag) return null;
            return `/poster/${encodeURIComponent(item.id)}/${encodeURIComponent(item.posterTag)}.jpg`;
        }
        const plural = type === 'movies' ? 'movies' : 'tvshows';
        return `${dataBase}/posters/${plural}/${encodeURIComponent(item.id)}.jpg`;
    }

    function legacyBackdropUrl(item, type) {
        if (serverType === 'jellyfin') return null;
        const plural = type === 'movies' ? 'movies' : 'tvshows';
        return `${dataBase}/backdrops/${plural}/${encodeURIComponent(item.id)}.jpg`;
    }

    function setNoResultsMessage(contentDiv, type, searchTerm) {
        const messageElem = contentDiv.querySelector('.no-results-message');
        const helpElem = contentDiv.querySelector('.no-results-help');
        const label = type === 'movies' ? 'Movies' : 'TV Shows';
        let message;
        let help;

        if (currentGenre !== 'all' && searchTerm) {
            message = `No ${label} in the “${currentGenre}” genre match “${searchTerm}”.`;
            help = 'Try a different search or clear the genre filter.';
        } else if (currentGenre !== 'all') {
            message = `No ${label} were found in the “${currentGenre}” genre.`;
            help = 'Try selecting a different genre.';
        } else if (searchTerm) {
            message = `No ${label} match “${searchTerm}”.`;
            help = 'Try a different search.';
        } else {
            message = `No ${label} are available.`;
            help = '';
        }
        messageElem.textContent = message;
        helpElem.textContent = help;
    }

    function safeTextPlaceholder(container, title, large = false) {
        const placeholder = document.createElement('div');
        placeholder.className = large ? 'text-placeholder large' : 'text-placeholder';
        const name = document.createElement('div');
        name.className = 'media-name';
        name.textContent = title || '';
        placeholder.appendChild(name);
        container.appendChild(placeholder);
        return placeholder;
    }

    function renderGenreButton(button, genre) {
        const icon = document.createElement('span');
        icon.className = 'sort-icon';
        if (!genre || genre === 'all') {
            icon.textContent = '🏷️ Genre';
            button.replaceChildren(icon);
            button.classList.remove('active');
            return;
        }
        if (button.id === 'mobile-genre-button') {
            icon.textContent = `🏷️ ${genre}`;
        } else {
            icon.append('🏷️ ');
            const selected = document.createElement('span');
            selected.className = 'selected-genre';
            selected.textContent = genre;
            icon.appendChild(selected);
        }
        button.replaceChildren(icon);
        button.classList.add('active');
    }

    function createGenreItem(genre, count, active) {
        const item = document.createElement('div');
        item.className = `genre-item ${active ? 'active' : ''}`.trim();
        item.dataset.genre = genre;
        item.append(genre === 'all' ? 'All Genres' : genre);
        if (genre !== 'all') {
            const badge = document.createElement('span');
            badge.className = 'genre-badge';
            badge.textContent = String(count);
            item.append(' ', badge);
        }
        return item;
    }

    function safeSetGenreFilter(genre) {
        currentGenre = genre;
        document.querySelectorAll('.genre-item').forEach(item => {
            item.classList.toggle('active', item.dataset.genre === genre);
        });
        document.querySelectorAll('.genre-button').forEach(button => renderGenreButton(button, genre));
        document.body.classList.toggle('sort-by-genre', genre !== 'all');
        document.querySelectorAll('.sort-button').forEach(btn => {
            if (btn.dataset.sort === currentSortMethod) btn.classList.add('active');
            else if (!btn.classList.contains('genre-button')) btn.classList.remove('active');
        });
        const term = document.querySelector('.search-input').value.toLowerCase();
        filterAndSortMedia(term);
        closeGenreDrawer();
    }

    function safeUpdateGenreDropdown(type) {
        const genres = allGenres[type];
        document.querySelectorAll('.genre-menu').forEach(dropdown => {
            dropdown.replaceChildren(createGenreItem('all', 0, currentGenre === 'all'));
            Object.entries(genres).forEach(([genre, count]) => {
                dropdown.appendChild(createGenreItem(genre, count, currentGenre === genre));
            });
        });
        document.querySelectorAll('.genre-menu .genre-item').forEach(item => {
            item.addEventListener('click', () => {
                setGenreFilter(item.dataset.genre);
                document.querySelectorAll('.genre-menu').forEach(menu => menu.classList.remove('show'));
                window.scrollTo({ top: 0, behavior: 'smooth' });
            });
        });
        document.querySelectorAll('.genre-button').forEach(button => renderGenreButton(button, currentGenre));
    }

    function safeUpdateGenreDrawer(type) {
        const genres = allGenres[type];
        const drawer = document.querySelector('.genre-drawer-content');
        drawer.replaceChildren(createGenreItem('all', 0, currentGenre === 'all'));
        Object.entries(genres).forEach(([genre, count]) => {
            drawer.appendChild(createGenreItem(genre, count, currentGenre === genre));
        });
        document.querySelector('.genre-drawer-title').textContent =
            `${type === 'movies' ? 'Movie' : 'TV Show'} Genres`;
        drawer.querySelectorAll('.genre-item').forEach(item => {
            item.addEventListener('click', () => setGenreFilter(item.dataset.genre));
        });
    }

    function createMediaCard(item, type, index) {
        const mediaItem = document.createElement('div');
        mediaItem.className = 'media-item';
        mediaItem.dataset.id = item.id;
        mediaItem.dataset.type = type;
        mediaItem.style.opacity = '0';
        mediaItem.style.transition = `opacity 0.2s ease ${Math.min(index, 10) * 0.012}s, transform 0.3s ease`;

        const posterContainer = document.createElement('div');
        posterContainer.className = 'poster-container';
        const url = posterUrl(item, type);
        if (url) {
            const placeholder = document.createElement('div');
            placeholder.className = 'poster-placeholder';
            const spinner = document.createElement('div');
            spinner.className = 'loading-spinner';
            placeholder.appendChild(spinner);
            const image = document.createElement('img');
            image.className = 'poster';
            image.alt = item.title || '';
            image.dataset.src = url;
            posterContainer.append(placeholder, image);
            imageObserver.observe(image);
        } else {
            safeTextPlaceholder(posterContainer, item.title || '');
        }

        const info = document.createElement('div');
        info.className = 'media-info';
        const title = document.createElement('div');
        title.className = 'media-title';
        title.textContent = item.title || '';
        const year = document.createElement('div');
        year.className = 'media-year';
        year.textContent = item.year || '';
        const added = document.createElement('div');
        added.className = 'media-added';
        added.textContent = item.addedAt ? `Added: ${formatDate(item.addedAt)}` : '';
        info.append(title, year, added);
        mediaItem.append(posterContainer, info);
        mediaItem.addEventListener('click', () => openModal(item, type));
        requestAnimationFrame(() => { mediaItem.style.opacity = '1'; });
        return mediaItem;
    }

    function disconnectLoadMoreObserver() {
        if (loadMoreObserver) loadMoreObserver.disconnect();
        loadMoreObserver = null;
    }

    function renderInBatches(data, type, grid, generation) {
        let nextIndex = 0;
        const sentinel = document.createElement('div');
        sentinel.className = 'large-library-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        sentinel.style.cssText = 'grid-column:1/-1;height:1px;pointer-events:none;';

        function appendBatch() {
            if (generation !== renderGeneration) return disconnectLoadMoreObserver();
            const end = Math.min(nextIndex + batchSize(), data.length);
            const fragment = document.createDocumentFragment();
            for (let i = nextIndex; i < end; i += 1) {
                fragment.appendChild(createMediaCard(data[i], type, i - nextIndex));
            }
            nextIndex = end;
            if (sentinel.parentNode === grid) grid.insertBefore(fragment, sentinel);
            else grid.appendChild(fragment);
            if (nextIndex >= data.length) {
                disconnectLoadMoreObserver();
                sentinel.remove();
                return;
            }
            if (!sentinel.parentNode) grid.appendChild(sentinel);
        }

        loadMoreObserver = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) appendBatch();
        }, { rootMargin: PRELOAD_MARGIN, threshold: 0.01 });
        appendBatch();
        if (sentinel.parentNode) loadMoreObserver.observe(sentinel);
    }

    function ultraDisplayMedia(data, type) {
        renderGeneration += 1;
        const generation = renderGeneration;
        disconnectLoadMoreObserver();
        const content = document.querySelector(`#${type}-content`);
        const loading = content.querySelector('.loading');
        const grid = content.querySelector('.media-grid');
        const noResults = content.querySelector('.no-results');
        loading.style.display = 'none';
        grid.replaceChildren();

        if (!data.length) {
            setNoResultsMessage(content, type, document.querySelector('.search-input').value.trim());
            noResults.classList.add('active');
            grid.style.display = 'none';
            return;
        }
        noResults.classList.remove('active');
        grid.style.display = 'grid';
        renderInBatches(data, type, grid, generation);
    }

    function runFilterAndSort(searchTerm) {
        const activeTab = document.querySelector('.tab.active').dataset.content;
        const data = activeTab === 'movies' ? moviesData : tvShowsData;
        const normalized = (searchTerm || '').trim().toLowerCase();
        let filtered = data;
        if (normalized) {
            filtered = filtered.filter(item => {
                if (!item.__beyondSearchTitle) item.__beyondSearchTitle = (item.title || '').toLowerCase();
                return item.__beyondSearchTitle.includes(normalized);
            });
        }
        if (currentGenre !== 'all') {
            filtered = filtered.filter(item => item.genres && item.genres.includes(currentGenre));
        }
        ultraDisplayMedia(sortMedia(filtered, currentSortMethod), activeTab);
    }

    function ultraFilterAndSortMedia(searchTerm) {
        clearTimeout(filterTimer);
        const input = document.querySelector('.search-input');
        const typing = input && document.activeElement === input && Boolean(searchTerm);
        if (!typing) return runFilterAndSort(searchTerm);
        filterTimer = setTimeout(() => runFilterAndSort(searchTerm), SEARCH_DEBOUNCE_MS);
    }

    function appendEmptyMessage(container, message) {
        const empty = document.createElement('div');
        empty.textContent = message;
        container.appendChild(empty);
    }

    async function ultraOpenModal(indexItem, type) {
        let item = indexItem;
        try {
            item = await loadDetails(indexItem, type);
        } catch (error) {
            console.warn('Could not load detail shard:', error);
        }

        const posterContainer = document.querySelector('.modal-poster');
        posterContainer.replaceChildren();
        const pUrl = posterUrl(indexItem, type);
        if (pUrl) {
            const poster = document.createElement('img');
            poster.src = pUrl;
            poster.alt = item.title || '';
            poster.onerror = function () {
                this.remove();
                safeTextPlaceholder(posterContainer, item.title, true);
            };
            posterContainer.appendChild(poster);
        } else {
            safeTextPlaceholder(posterContainer, item.title, true);
        }

        const backdrop = document.querySelector('.modal-backdrop');
        backdrop.replaceChildren();
        backdrop.style.backgroundImage = 'none';
        const bUrl = legacyBackdropUrl(item, type);
        if (bUrl) {
            const test = new Image();
            test.onload = () => { backdrop.style.backgroundImage = `url("${bUrl}")`; };
            test.onerror = () => {
                const fallback = document.createElement('div');
                fallback.className = 'backdrop-text-placeholder';
                fallback.textContent = type === 'movies' ? 'Movie' : 'TV Show';
                backdrop.replaceChildren(fallback);
            };
            test.src = bUrl;
        } else {
            const fallback = document.createElement('div');
            fallback.className = 'backdrop-text-placeholder';
            fallback.textContent = type === 'movies' ? 'Movie' : 'TV Show';
            backdrop.appendChild(fallback);
        }

        document.querySelector('.modal-title').textContent = item.title || '';
        document.querySelector('.modal-year').textContent = item.year || '';
        const rating = document.getElementById('modal-rating');
        const duration = document.getElementById('modal-duration');
        if (item.contentRating) {
            rating.textContent = item.contentRating;
            rating.style.display = 'block';
        } else rating.style.display = 'none';

        let seasons = document.getElementById('modal-seasons');
        let episodes = document.getElementById('modal-episodes');
        if (type === 'movies' && item.duration) {
            duration.textContent = `${Math.floor(item.duration / 60000)} min`;
            duration.style.display = 'block';
            if (seasons) seasons.style.display = 'none';
            if (episodes) episodes.style.display = 'none';
        } else if (type === 'tvshows') {
            if (!seasons) {
                seasons = document.createElement('div');
                seasons.className = 'metadata-item';
                seasons.id = 'modal-seasons';
                duration.parentNode.insertBefore(seasons, duration);
            }
            if (!episodes) {
                episodes = document.createElement('div');
                episodes.className = 'metadata-item';
                episodes.id = 'modal-episodes';
                seasons.parentNode.insertBefore(episodes, seasons.nextSibling);
            }
            duration.style.display = 'none';
            seasons.textContent = item.childCount ? `${item.childCount} ${item.childCount === 1 ? 'season' : 'seasons'}` : '';
            seasons.style.display = item.childCount ? 'block' : 'none';
            episodes.textContent = item.leafCount ? `${item.leafCount} ${item.leafCount === 1 ? 'episode' : 'episodes'}` : '';
            episodes.style.display = item.leafCount ? 'block' : 'none';
        } else {
            duration.style.display = 'none';
            if (seasons) seasons.style.display = 'none';
            if (episodes) episodes.style.display = 'none';
        }

        document.getElementById('modal-summary').textContent = item.summary || 'No summary available.';
        const genres = document.getElementById('modal-genres');
        genres.replaceChildren();
        if (item.genres && item.genres.length) {
            item.genres.forEach(genre => {
                const tag = document.createElement('div');
                tag.className = 'genre-tag';
                tag.textContent = genre;
                tag.addEventListener('click', () => { setGenreFilter(genre); closeModal(); });
                genres.appendChild(tag);
            });
        } else appendEmptyMessage(genres, 'No genres available');

        const cast = document.getElementById('modal-cast');
        cast.replaceChildren();
        if (item.actors && item.actors.length) {
            item.actors.forEach(actor => {
                const row = document.createElement('div');
                row.className = 'cast-item';
                const name = document.createElement('div');
                name.className = 'cast-name';
                name.textContent = actor.name || '';
                const role = document.createElement('div');
                role.className = 'cast-role';
                role.textContent = actor.role || '';
                row.append(name, role);
                cast.appendChild(row);
            });
        } else appendEmptyMessage(cast, 'No cast information available');

        let dateAdded = document.getElementById('modal-added-date');
        if (!dateAdded) {
            const section = document.createElement('div');
            section.className = 'modal-section date-section';
            const heading = document.createElement('div');
            heading.className = 'modal-section-title';
            heading.textContent = 'Date Added';
            dateAdded = document.createElement('div');
            dateAdded.id = 'modal-added-date';
            section.append(heading, dateAdded);
            document.querySelector('.modal-body').appendChild(section);
        }
        dateAdded.textContent = item.addedAt ? formatDate(item.addedAt) : '';

        const modalBody = document.querySelector('.modal-body');
        modalBody.scrollTop = 0;
        document.body.style.overflow = 'hidden';
        modalOverlay.classList.add('active');
        requestAnimationFrame(() => { modalBody.scrollTop = 0; });
    }

    async function ultraLoadMedia() {
        try {
            const indexes = await resolveIndexes();
            moviesData = indexes.movies;
            tvShowsData = indexes.tvshows;
            allGenres.movies = extractGenres(moviesData, 'movies');
            allGenres.tvshows = extractGenres(tvShowsData, 'tvshows');
            updateGenreUI('movies');
            filterAndSortMedia('');
        } catch (error) {
            console.error('Error loading media indexes:', error);
            for (const selector of ['#movies-content .loading', '#tvshows-content .loading']) {
                const loading = document.querySelector(selector);
                loading.replaceChildren();
                const message = document.createElement('div');
                message.className = 'error';
                message.textContent = 'Failed to load media data. Please try again later.';
                loading.appendChild(message);
            }
        }
    }

    createTextPlaceholder = (container, title) => safeTextPlaceholder(container, title);
    setGenreFilter = safeSetGenreFilter;
    updateGenreDropdown = safeUpdateGenreDropdown;
    updateGenreDrawer = safeUpdateGenreDrawer;
    displayMedia = ultraDisplayMedia;
    filterAndSortMedia = ultraFilterAndSortMedia;
    openModal = ultraOpenModal;
    loadMedia = ultraLoadMedia;

    window.__beyondGlimpseLargeLibraryReady = true;
    window.__beyondGlimpseMetadataSafe = true;
    window.__beyondGlimpseUltraLight = true;
    window.dispatchEvent(new Event('beyond-glimpse:ready'));
})();
