// Beyond Glimpse large-library renderer.
// Loaded after the upstream inline application so we can replace its expensive
// all-at-once DOM rendering without rewriting the original UI in one step.

(() => {
    const DEFAULT_BATCH_SIZE = 96;
    const MOBILE_BATCH_SIZE = 48;
    const SEARCH_DEBOUNCE_MS = 140;
    const PRELOAD_MARGIN = '1200px 0px';

    let renderGeneration = 0;
    let loadMoreObserver = null;
    let filterTimer = null;

    function batchSize() {
        return window.innerWidth < 768 ? MOBILE_BATCH_SIZE : DEFAULT_BATCH_SIZE;
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

        // textContent deliberately avoids interpreting metadata/search input as HTML.
        messageElem.textContent = message;
        helpElem.textContent = help;
    }

    function createMediaCard(item, type, index) {
        const mediaItem = document.createElement('div');
        mediaItem.className = 'media-item';
        mediaItem.dataset.id = item.id;
        mediaItem.dataset.type = type;
        mediaItem.style.opacity = '0';
        mediaItem.style.transition = `opacity 0.25s ease ${Math.min(index, 12) * 0.015}s, transform 0.3s ease`;

        const posterContainer = document.createElement('div');
        posterContainer.className = 'poster-container';

        const placeholder = document.createElement('div');
        placeholder.className = 'poster-placeholder';
        const spinner = document.createElement('div');
        spinner.className = 'loading-spinner';
        placeholder.appendChild(spinner);

        const image = document.createElement('img');
        image.className = 'poster';
        image.alt = item.title || '';
        image.dataset.src = type === 'movies'
            ? `data/posters/movies/${item.id}.jpg`
            : `data/posters/tvshows/${item.id}.jpg`;

        posterContainer.append(placeholder, image);

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

        imageObserver.observe(image);
        mediaItem.addEventListener('click', () => openModal(item, type));

        requestAnimationFrame(() => {
            mediaItem.style.opacity = '1';
        });

        return mediaItem;
    }

    function disconnectLoadMoreObserver() {
        if (loadMoreObserver) {
            loadMoreObserver.disconnect();
            loadMoreObserver = null;
        }
    }

    function renderInBatches(data, type, grid, generation) {
        let nextIndex = 0;
        const sentinel = document.createElement('div');
        sentinel.className = 'large-library-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        sentinel.style.cssText = 'grid-column:1/-1;height:1px;pointer-events:none;';

        function appendBatch() {
            if (generation !== renderGeneration) {
                disconnectLoadMoreObserver();
                return;
            }

            const end = Math.min(nextIndex + batchSize(), data.length);
            const fragment = document.createDocumentFragment();
            for (let index = nextIndex; index < end; index += 1) {
                fragment.appendChild(createMediaCard(data[index], type, index - nextIndex));
            }
            nextIndex = end;

            if (sentinel.parentNode === grid) {
                grid.insertBefore(fragment, sentinel);
            } else {
                grid.appendChild(fragment);
            }

            if (nextIndex >= data.length) {
                disconnectLoadMoreObserver();
                sentinel.remove();
                return;
            }

            if (!sentinel.parentNode) {
                grid.appendChild(sentinel);
            }
        }

        loadMoreObserver = new IntersectionObserver((entries) => {
            if (entries.some(entry => entry.isIntersecting)) {
                appendBatch();
            }
        }, { rootMargin: PRELOAD_MARGIN, threshold: 0.01 });

        appendBatch();
        if (sentinel.parentNode) {
            loadMoreObserver.observe(sentinel);
        }
    }

    function largeLibraryDisplayMedia(data, type) {
        renderGeneration += 1;
        const generation = renderGeneration;
        disconnectLoadMoreObserver();

        const contentDiv = document.querySelector(`#${type}-content`);
        const loadingDiv = contentDiv.querySelector('.loading');
        const grid = contentDiv.querySelector('.media-grid');
        const noResultsDiv = contentDiv.querySelector('.no-results');

        loadingDiv.style.display = 'none';
        grid.replaceChildren();

        if (data.length === 0) {
            const searchTerm = document.querySelector('.search-input').value.trim();
            setNoResultsMessage(contentDiv, type, searchTerm);
            noResultsDiv.classList.add('active');
            grid.style.display = 'none';
            document.querySelectorAll('.genre-menu').forEach(menu => menu.classList.remove('show'));
            return;
        }

        noResultsDiv.classList.remove('active');
        grid.style.display = 'grid';
        renderInBatches(data, type, grid, generation);
    }

    function runFilterAndSort(searchTerm) {
        const activeTab = document.querySelector('.tab.active').dataset.content;
        const data = activeTab === 'movies' ? moviesData : tvShowsData;
        const normalizedSearch = (searchTerm || '').trim().toLowerCase();

        let filtered = data;
        if (normalizedSearch) {
            filtered = filtered.filter(item => {
                if (!item.__beyondSearchTitle) {
                    item.__beyondSearchTitle = (item.title || '').toLowerCase();
                }
                return item.__beyondSearchTitle.includes(normalizedSearch);
            });
        }

        if (currentGenre !== 'all') {
            filtered = filtered.filter(item => item.genres && item.genres.includes(currentGenre));
        }

        largeLibraryDisplayMedia(sortMedia(filtered, currentSortMethod), activeTab);
    }

    function largeLibraryFilterAndSortMedia(searchTerm) {
        clearTimeout(filterTimer);

        const searchInput = document.querySelector('.search-input');
        const typing = searchInput && document.activeElement === searchInput && Boolean(searchTerm);
        if (!typing) {
            runFilterAndSort(searchTerm);
            return;
        }

        filterTimer = setTimeout(() => runFilterAndSort(searchTerm), SEARCH_DEBOUNCE_MS);
    }

    // Replace the upstream hot paths. Existing tab/sort/search handlers resolve
    // these function bindings when they run, so no duplicate listeners are needed.
    displayMedia = largeLibraryDisplayMedia;
    filterAndSortMedia = largeLibraryFilterAndSortMedia;

    window.__beyondGlimpseLargeLibraryReady = true;
    window.dispatchEvent(new Event('beyond-glimpse:ready'));
})();
