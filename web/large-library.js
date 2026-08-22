// Beyond Glimpse v2 catalogue runtime.
// Jellyfin uses a SQLite-backed paginated API; Plex/Emby retain the inherited
// static JSON compatibility path.

(() => {
    const PAGE_SIZE = window.innerWidth < 768 ? 48 : 96;
    const SEARCH_DEBOUNCE_MS = 180;
    const PRELOAD_MARGIN = '1200px 0px';
    const originalLoadSource = typeof loadMedia === 'function' ? String(loadMedia) : '';

    let serverType = null;
    let dataBase = null;
    let loadMoreObserver = null;
    let filterTimer = null;
    let queryGeneration = 0;
    let apiLoading = false;
    let apiHasMore = false;
    let apiNextOffset = 0;
    let apiSearch = '';
    let apiActiveType = 'movies';

    function hintedServerType() {
        for (const type of ['jellyfin', 'plex', 'emby']) {
            if (originalLoadSource.includes(`data/${type}/movies.json`) ||
                originalLoadSource.includes(`data/${type}/tvshows.json`)) return type;
        }
        const path = window.location.pathname.toLowerCase();
        if (path.startsWith('/jellyfin/')) return 'jellyfin';
        if (path.startsWith('/plex/')) return 'plex';
        if (path.startsWith('/emby/')) return 'emby';
        return null;
    }

    function isApiMode() {
        return serverType === 'jellyfin';
    }

    function apiMediaType(type) {
        return type === 'tvshows' ? 'tvshow' : 'movie';
    }

    function activeTabType() {
        const active = document.querySelector('.tab.active');
        return active && active.dataset.content === 'tvshows' ? 'tvshows' : 'movies';
    }

    function sortQuery() {
        const method = String(currentSortMethod || 'title').toLowerCase();
        if (method.includes('date') || method.includes('added') || method.includes('recent')) {
            return { sort: 'added', order: method.includes('asc') ? 'asc' : 'desc' };
        }
        if (method.includes('year') || method.includes('release')) {
            return { sort: 'year', order: method.includes('asc') ? 'asc' : 'desc' };
        }
        return { sort: 'title', order: method.includes('desc') ? 'desc' : 'asc' };
    }

    async function loadStaticIndexes(base, type) {
        const [moviesResponse, tvResponse] = await Promise.all([
            fetch(`${base}/movies.json`, { cache: 'no-cache' }),
            fetch(`${base}/tvshows.json`, { cache: 'no-cache' })
        ]);
        if (!moviesResponse.ok || !tvResponse.ok) throw new Error(`catalogue unavailable at ${base}`);
        const [movies, tvshows] = await Promise.all([moviesResponse.json(), tvResponse.json()]);
        if (!Array.isArray(movies) || !Array.isArray(tvshows)) throw new Error('invalid catalogue');
        dataBase = base;
        serverType = type;
        return { movies, tvshows };
    }

    async function resolveStaticIndexes() {
        const hinted = hintedServerType();
        if (hinted && hinted !== 'jellyfin') return loadStaticIndexes(`/data/${hinted}`, hinted);
        let lastError;
        for (const type of ['plex', 'emby']) {
            try { return await loadStaticIndexes(`/data/${type}`, type); }
            catch (error) { lastError = error; }
        }
        throw lastError || new Error('no compatible catalogue found');
    }

    function posterUrl(item, type) {
        if (isApiMode()) {
            return item.posterTag ? `/poster/${encodeURIComponent(item.id)}/${encodeURIComponent(item.posterTag)}.jpg` : null;
        }
        const plural = type === 'movies' ? 'movies' : 'tvshows';
        return `${dataBase}/posters/${plural}/${encodeURIComponent(item.id)}.jpg`;
    }

    function legacyBackdropUrl(item, type) {
        if (isApiMode()) return null;
        const plural = type === 'movies' ? 'movies' : 'tvshows';
        return `${dataBase}/backdrops/${plural}/${encodeURIComponent(item.id)}.jpg`;
    }

    async function loadDetails(item) {
        if (!isApiMode()) return item;
        const response = await fetch(`/api/item/${encodeURIComponent(item.id)}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`detail request failed: ${response.status}`);
        return response.json();
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

    function setNoResultsMessage(content, type, searchTerm) {
        const messageElem = content.querySelector('.no-results-message');
        const helpElem = content.querySelector('.no-results-help');
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
            message = `No ${label} are available yet.`;
            help = isApiMode() ? 'The catalogue may still be indexing.' : '';
        }
        if (messageElem) messageElem.textContent = message;
        if (helpElem) helpElem.textContent = help;
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
        if (button.id === 'mobile-genre-button') icon.textContent = `🏷️ ${genre}`;
        else {
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
        const input = document.querySelector('.search-input');
        filterAndSortMedia(input ? input.value : '');
        if (typeof closeGenreDrawer === 'function') closeGenreDrawer();
    }

    function safeUpdateGenreDropdown(type) {
        const genres = allGenres[type] || {};
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
        const genres = allGenres[type] || {};
        const drawer = document.querySelector('.genre-drawer-content');
        if (!drawer) return;
        drawer.replaceChildren(createGenreItem('all', 0, currentGenre === 'all'));
        Object.entries(genres).forEach(([genre, count]) => {
            drawer.appendChild(createGenreItem(genre, count, currentGenre === genre));
        });
        const title = document.querySelector('.genre-drawer-title');
        if (title) title.textContent = `${type === 'movies' ? 'Movie' : 'TV Show'} Genres`;
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
        } else safeTextPlaceholder(posterContainer, item.title || '');

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

    function disconnectObserver() {
        if (loadMoreObserver) loadMoreObserver.disconnect();
        loadMoreObserver = null;
    }

    function contentParts(type) {
        const content = document.querySelector(`#${type}-content`);
        return {
            content,
            loading: content && content.querySelector('.loading'),
            grid: content && content.querySelector('.media-grid'),
            noResults: content && content.querySelector('.no-results')
        };
    }

    function setLoading(type, message = '') {
        const { loading, grid, noResults } = contentParts(type);
        if (grid) grid.style.display = 'none';
        if (noResults) noResults.classList.remove('active');
        if (!loading) return;
        loading.style.display = '';
        if (message) {
            const text = document.createElement('div');
            text.textContent = message;
            loading.replaceChildren(text);
        }
    }

    function currentDataArray(type) {
        return type === 'movies' ? moviesData : tvShowsData;
    }

    function replaceCurrentData(type, values) {
        if (type === 'movies') moviesData = values;
        else tvShowsData = values;
    }

    function appendApiItems(type, items) {
        const data = currentDataArray(type);
        data.push(...items);
        const { loading, grid, noResults } = contentParts(type);
        if (!grid) return;
        if (loading) loading.style.display = 'none';
        if (items.length || data.length) {
            if (noResults) noResults.classList.remove('active');
            grid.style.display = 'grid';
            const fragment = document.createDocumentFragment();
            items.forEach((item, index) => fragment.appendChild(createMediaCard(item, type, index)));
            const sentinel = grid.querySelector('.large-library-sentinel');
            if (sentinel) grid.insertBefore(fragment, sentinel);
            else grid.appendChild(fragment);
        }
    }

    function ensureApiSentinel(type, generation) {
        disconnectObserver();
        const { grid } = contentParts(type);
        if (!grid || !apiHasMore || generation !== queryGeneration) return;
        let sentinel = grid.querySelector('.large-library-sentinel');
        if (!sentinel) {
            sentinel = document.createElement('div');
            sentinel.className = 'large-library-sentinel';
            sentinel.setAttribute('aria-hidden', 'true');
            sentinel.style.cssText = 'grid-column:1/-1;height:1px;pointer-events:none;';
            grid.appendChild(sentinel);
        }
        loadMoreObserver = new IntersectionObserver(entries => {
            if (entries.some(entry => entry.isIntersecting)) loadApiPage(generation);
        }, { rootMargin: PRELOAD_MARGIN, threshold: 0.01 });
        loadMoreObserver.observe(sentinel);
    }

    async function loadApiPage(generation) {
        if (apiLoading || !apiHasMore || generation !== queryGeneration) return;
        apiLoading = true;
        const type = apiActiveType;
        const sort = sortQuery();
        const params = new URLSearchParams({
            type: apiMediaType(type),
            limit: String(PAGE_SIZE),
            offset: String(apiNextOffset),
            sort: sort.sort,
            order: sort.order
        });
        if (apiSearch) params.set('q', apiSearch);
        if (currentGenre && currentGenre !== 'all') params.set('genre', currentGenre);
        try {
            const response = await fetch(`/api/items?${params}`, { cache: 'no-store' });
            if (!response.ok) throw new Error(`catalogue API returned ${response.status}`);
            const payload = await response.json();
            if (generation !== queryGeneration) return;
            const items = Array.isArray(payload.items) ? payload.items : [];
            appendApiItems(type, items);
            apiHasMore = Boolean(payload.hasMore);
            apiNextOffset = payload.nextOffset == null ? apiNextOffset + items.length : payload.nextOffset;
            const { grid, noResults } = contentParts(type);
            const sentinel = grid && grid.querySelector('.large-library-sentinel');
            if (sentinel) sentinel.remove();
            if (!currentDataArray(type).length) {
                setNoResultsMessage(contentParts(type).content, type, apiSearch);
                if (noResults) noResults.classList.add('active');
                if (grid) grid.style.display = 'none';
            }
            ensureApiSentinel(type, generation);
        } catch (error) {
            console.error('Catalogue API page failed:', error);
            if (generation === queryGeneration && !currentDataArray(type).length) {
                setLoading(type, 'Catalogue service is starting…');
            }
        } finally {
            apiLoading = false;
        }
    }

    async function resetApiQuery(searchTerm) {
        queryGeneration += 1;
        const generation = queryGeneration;
        disconnectObserver();
        apiLoading = false;
        apiHasMore = true;
        apiNextOffset = 0;
        apiSearch = String(searchTerm || '').trim();
        apiActiveType = activeTabType();
        replaceCurrentData(apiActiveType, []);
        const { grid } = contentParts(apiActiveType);
        if (grid) grid.replaceChildren();
        setLoading(apiActiveType, 'Loading catalogue…');
        await loadApiPage(generation);
    }

    async function loadApiGenres() {
        const entries = await Promise.all(['movies', 'tvshows'].map(async type => {
            const response = await fetch(`/api/genres?type=${apiMediaType(type)}`, { cache: 'no-store' });
            if (!response.ok) return [type, {}];
            const payload = await response.json();
            const values = {};
            for (const genre of payload.genres || []) values[genre.name] = genre.count;
            return [type, values];
        }));
        for (const [type, genres] of entries) allGenres[type] = genres;
    }

    function staticDisplayMedia(data, type) {
        const { loading, grid, noResults, content } = contentParts(type);
        disconnectObserver();
        if (loading) loading.style.display = 'none';
        if (!grid) return;
        grid.replaceChildren();
        if (!data.length) {
            setNoResultsMessage(content, type, document.querySelector('.search-input')?.value || '');
            if (noResults) noResults.classList.add('active');
            grid.style.display = 'none';
            return;
        }
        if (noResults) noResults.classList.remove('active');
        grid.style.display = 'grid';
        let index = 0;
        const appendBatch = () => {
            const fragment = document.createDocumentFragment();
            const end = Math.min(index + PAGE_SIZE, data.length);
            for (; index < end; index += 1) fragment.appendChild(createMediaCard(data[index], type, index));
            grid.appendChild(fragment);
            if (index >= data.length) return;
            const sentinel = document.createElement('div');
            sentinel.className = 'large-library-sentinel';
            sentinel.style.cssText = 'grid-column:1/-1;height:1px;';
            grid.appendChild(sentinel);
            loadMoreObserver = new IntersectionObserver(entries => {
                if (!entries.some(entry => entry.isIntersecting)) return;
                disconnectObserver();
                sentinel.remove();
                appendBatch();
            }, { rootMargin: PRELOAD_MARGIN, threshold: 0.01 });
            loadMoreObserver.observe(sentinel);
        };
        appendBatch();
    }

    function runStaticFilter(searchTerm) {
        const type = activeTabType();
        const data = type === 'movies' ? moviesData : tvShowsData;
        const normalized = String(searchTerm || '').trim().toLowerCase();
        let filtered = data;
        if (normalized) filtered = filtered.filter(item => String(item.title || '').toLowerCase().includes(normalized));
        if (currentGenre !== 'all') filtered = filtered.filter(item => item.genres && item.genres.includes(currentGenre));
        staticDisplayMedia(sortMedia(filtered, currentSortMethod), type);
    }

    function v2FilterAndSortMedia(searchTerm) {
        clearTimeout(filterTimer);
        const execute = () => isApiMode() ? resetApiQuery(searchTerm) : runStaticFilter(searchTerm);
        const input = document.querySelector('.search-input');
        const typing = input && document.activeElement === input && Boolean(searchTerm);
        if (!typing) return execute();
        filterTimer = setTimeout(execute, SEARCH_DEBOUNCE_MS);
    }

    function appendEmptyMessage(container, message) {
        const empty = document.createElement('div');
        empty.textContent = message;
        container.appendChild(empty);
    }

    async function v2OpenModal(indexItem, type) {
        let item = indexItem;
        try { item = await loadDetails(indexItem); }
        catch (error) { console.warn('Could not load lazy item details:', error); }

        const posterContainer = document.querySelector('.modal-poster');
        posterContainer.replaceChildren();
        const pUrl = posterUrl(indexItem, type);
        if (pUrl) {
            const poster = document.createElement('img');
            poster.src = pUrl;
            poster.alt = item.title || '';
            poster.onerror = function () { this.remove(); safeTextPlaceholder(posterContainer, item.title, true); };
            posterContainer.appendChild(poster);
        } else safeTextPlaceholder(posterContainer, item.title, true);

        const backdrop = document.querySelector('.modal-backdrop');
        backdrop.replaceChildren();
        backdrop.style.backgroundImage = 'none';
        const bUrl = legacyBackdropUrl(item, type);
        if (bUrl) backdrop.style.backgroundImage = `url("${bUrl}")`;
        else {
            const fallback = document.createElement('div');
            fallback.className = 'backdrop-text-placeholder';
            fallback.textContent = type === 'movies' ? 'Movie' : 'TV Show';
            backdrop.appendChild(fallback);
        }

        document.querySelector('.modal-title').textContent = item.title || '';
        document.querySelector('.modal-year').textContent = item.year || '';
        const rating = document.getElementById('modal-rating');
        const duration = document.getElementById('modal-duration');
        if (item.contentRating) { rating.textContent = item.contentRating; rating.style.display = 'block'; }
        else rating.style.display = 'none';

        let seasons = document.getElementById('modal-seasons');
        let episodes = document.getElementById('modal-episodes');
        if (type === 'movies' && item.duration) {
            duration.textContent = `${Math.floor(item.duration / 60000)} min`;
            duration.style.display = 'block';
            if (seasons) seasons.style.display = 'none';
            if (episodes) episodes.style.display = 'none';
        } else if (type === 'tvshows') {
            if (!seasons) {
                seasons = document.createElement('div'); seasons.className = 'metadata-item'; seasons.id = 'modal-seasons';
                duration.parentNode.insertBefore(seasons, duration);
            }
            if (!episodes) {
                episodes = document.createElement('div'); episodes.className = 'metadata-item'; episodes.id = 'modal-episodes';
                seasons.parentNode.insertBefore(episodes, seasons.nextSibling);
            }
            duration.style.display = 'none';
            seasons.textContent = item.childCount ? `${item.childCount} ${item.childCount === 1 ? 'season' : 'seasons'}` : '';
            seasons.style.display = item.childCount ? 'block' : 'none';
            episodes.textContent = item.leafCount ? `${item.leafCount} ${item.leafCount === 1 ? 'episode' : 'episodes'}` : '';
            episodes.style.display = item.leafCount ? 'block' : 'none';
        } else duration.style.display = 'none';

        document.getElementById('modal-summary').textContent = item.summary || 'No summary available.';
        const genres = document.getElementById('modal-genres');
        genres.replaceChildren();
        if (item.genres && item.genres.length) item.genres.forEach(genre => {
            const tag = document.createElement('div');
            tag.className = 'genre-tag'; tag.textContent = genre;
            tag.addEventListener('click', () => { setGenreFilter(genre); closeModal(); });
            genres.appendChild(tag);
        }); else appendEmptyMessage(genres, 'No genres available');

        const cast = document.getElementById('modal-cast');
        cast.replaceChildren();
        if (item.actors && item.actors.length) item.actors.forEach(actor => {
            const row = document.createElement('div'); row.className = 'cast-item';
            const name = document.createElement('div'); name.className = 'cast-name'; name.textContent = actor.name || '';
            const role = document.createElement('div'); role.className = 'cast-role'; role.textContent = actor.role || '';
            row.append(name, role); cast.appendChild(row);
        }); else appendEmptyMessage(cast, 'No cast information available');

        let dateAdded = document.getElementById('modal-added-date');
        if (!dateAdded) {
            const section = document.createElement('div'); section.className = 'modal-section date-section';
            const heading = document.createElement('div'); heading.className = 'modal-section-title'; heading.textContent = 'Date Added';
            dateAdded = document.createElement('div'); dateAdded.id = 'modal-added-date';
            section.append(heading, dateAdded); document.querySelector('.modal-body').appendChild(section);
        }
        dateAdded.textContent = item.addedAt ? formatDate(item.addedAt) : '';
        const modalBody = document.querySelector('.modal-body');
        modalBody.scrollTop = 0; document.body.style.overflow = 'hidden'; modalOverlay.classList.add('active');
    }

    async function v2LoadMedia() {
        try {
            const hinted = hintedServerType();
            if (hinted === 'jellyfin') {
                serverType = 'jellyfin';
                dataBase = '/api';
                moviesData = [];
                tvShowsData = [];
                await loadApiGenres();
                updateGenreUI(activeTabType());
                await resetApiQuery(document.querySelector('.search-input')?.value || '');
                return;
            }
            const indexes = await resolveStaticIndexes();
            moviesData = indexes.movies;
            tvShowsData = indexes.tvshows;
            allGenres.movies = extractGenres(moviesData, 'movies');
            allGenres.tvshows = extractGenres(tvShowsData, 'tvshows');
            updateGenreUI(activeTabType());
            runStaticFilter('');
        } catch (error) {
            console.error('Error loading catalogue:', error);
            setLoading(activeTabType(), 'Catalogue service is starting…');
        }
    }

    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            if (!isApiMode()) return;
            setTimeout(() => {
                currentGenre = 'all';
                updateGenreUI(activeTabType());
                resetApiQuery(document.querySelector('.search-input')?.value || '');
            }, 0);
        });
    });

    createTextPlaceholder = (container, title) => safeTextPlaceholder(container, title);
    setGenreFilter = safeSetGenreFilter;
    updateGenreDropdown = safeUpdateGenreDropdown;
    updateGenreDrawer = safeUpdateGenreDrawer;
    displayMedia = staticDisplayMedia;
    filterAndSortMedia = v2FilterAndSortMedia;
    openModal = v2OpenModal;
    loadMedia = v2LoadMedia;

    window.__beyondGlimpseLargeLibraryReady = true;
    window.__beyondGlimpseMetadataSafe = true;
    window.__beyondGlimpseUltraLight = true;
    window.__beyondGlimpseCatalogueService = true;
    window.dispatchEvent(new Event('beyond-glimpse:ready'));
})();
