// Beyond Glimpse large-library renderer and public metadata hardening.
// Loaded after the upstream inline application so we can replace its expensive
// and metadata-unsafe hot paths without rewriting the original UI in one step.

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
            if (btn.dataset.sort === currentSortMethod) {
                btn.classList.add('active');
            } else if (!btn.classList.contains('genre-button')) {
                btn.classList.remove('active');
            }
        });

        const currentSearchTerm = document.querySelector('.search-input').value.toLowerCase();
        filterAndSortMedia(currentSearchTerm);
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
        const drawerContent = document.querySelector('.genre-drawer-content');
        drawerContent.replaceChildren(createGenreItem('all', 0, currentGenre === 'all'));

        Object.entries(genres).forEach(([genre, count]) => {
            drawerContent.appendChild(createGenreItem(genre, count, currentGenre === genre));
        });

        document.querySelector('.genre-drawer-title').textContent =
            `${type === 'movies' ? 'Movie' : 'TV Show'} Genres`;

        document.querySelectorAll('.genre-drawer-content .genre-item').forEach(item => {
            item.addEventListener('click', () => setGenreFilter(item.dataset.genre));
        });
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

    function appendEmptyMessage(container, message) {
        const empty = document.createElement('div');
        empty.textContent = message;
        container.appendChild(empty);
    }

    function safeOpenModal(item, type) {
        const posterPath = type === 'movies'
            ? `data/posters/movies/${item.id}.jpg`
            : `data/posters/tvshows/${item.id}.jpg`;
        const posterContainer = document.querySelector('.modal-poster');
        posterContainer.replaceChildren();

        const posterImg = document.createElement('img');
        posterImg.src = posterPath;
        posterImg.alt = item.title || '';
        posterContainer.appendChild(posterImg);
        posterImg.onerror = function () {
            this.remove();
            safeTextPlaceholder(posterContainer, item.title, true);
        };

        const backdropPath = type === 'movies'
            ? `data/backdrops/movies/${item.id}.jpg`
            : `data/backdrops/tvshows/${item.id}.jpg`;
        const backdropElement = document.querySelector('.modal-backdrop');
        backdropElement.replaceChildren();
        backdropElement.style.backgroundImage = `url("${backdropPath}")`;

        const testImage = new Image();
        testImage.onerror = function () {
            backdropElement.style.backgroundImage = 'none';
            const backdropPlaceholder = document.createElement('div');
            backdropPlaceholder.className = 'backdrop-text-placeholder';
            backdropPlaceholder.textContent = type === 'movies' ? 'Movie' : 'TV Show';
            backdropElement.replaceChildren(backdropPlaceholder);
        };
        testImage.src = backdropPath;

        document.querySelector('.modal-title').textContent = item.title || '';
        document.querySelector('.modal-year').textContent = item.year || '';

        const ratingElem = document.getElementById('modal-rating');
        const durationElem = document.getElementById('modal-duration');
        if (item.contentRating) {
            ratingElem.textContent = item.contentRating;
            ratingElem.style.display = 'block';
        } else {
            ratingElem.style.display = 'none';
        }

        let seasonsElem = document.getElementById('modal-seasons');
        let episodesElem = document.getElementById('modal-episodes');

        if (type === 'movies' && item.duration) {
            durationElem.textContent = `${Math.floor(item.duration / 60000)} min`;
            durationElem.style.display = 'block';
            if (seasonsElem) seasonsElem.style.display = 'none';
            if (episodesElem) episodesElem.style.display = 'none';
        } else if (type === 'tvshows') {
            if (!seasonsElem) {
                seasonsElem = document.createElement('div');
                seasonsElem.className = 'metadata-item';
                seasonsElem.id = 'modal-seasons';
                durationElem.parentNode.insertBefore(seasonsElem, durationElem);
            }
            if (!episodesElem) {
                episodesElem = document.createElement('div');
                episodesElem.className = 'metadata-item';
                episodesElem.id = 'modal-episodes';
                seasonsElem.parentNode.insertBefore(episodesElem, seasonsElem.nextSibling);
            }
            durationElem.style.display = 'none';
            seasonsElem.textContent = item.childCount
                ? `${item.childCount} ${item.childCount === 1 ? 'season' : 'seasons'}`
                : '';
            seasonsElem.style.display = item.childCount ? 'block' : 'none';
            episodesElem.textContent = item.leafCount
                ? `${item.leafCount} ${item.leafCount === 1 ? 'episode' : 'episodes'}`
                : '';
            episodesElem.style.display = item.leafCount ? 'block' : 'none';
        } else {
            durationElem.style.display = 'none';
            if (seasonsElem) seasonsElem.style.display = 'none';
            if (episodesElem) episodesElem.style.display = 'none';
        }

        document.getElementById('modal-summary').textContent = item.summary || 'No summary available.';

        const genresContainer = document.getElementById('modal-genres');
        genresContainer.replaceChildren();
        if (item.genres && item.genres.length > 0) {
            item.genres.forEach(genre => {
                const genreElement = document.createElement('div');
                genreElement.className = 'genre-tag';
                genreElement.textContent = genre;
                genreElement.addEventListener('click', () => {
                    setGenreFilter(genre);
                    closeModal();
                });
                genresContainer.appendChild(genreElement);
            });
        } else {
            appendEmptyMessage(genresContainer, 'No genres available');
        }

        const castContainer = document.getElementById('modal-cast');
        castContainer.replaceChildren();
        if (item.actors && item.actors.length > 0) {
            item.actors.forEach(actor => {
                const actorElement = document.createElement('div');
                actorElement.className = 'cast-item';
                const name = document.createElement('div');
                name.className = 'cast-name';
                name.textContent = actor.name || '';
                const role = document.createElement('div');
                role.className = 'cast-role';
                role.textContent = actor.role || '';
                actorElement.append(name, role);
                castContainer.appendChild(actorElement);
            });
        } else {
            appendEmptyMessage(castContainer, 'No cast information available');
        }

        const dateAdded = item.addedAt ? formatDate(item.addedAt) : '';
        let dateAddedElem = document.getElementById('modal-added-date');
        if (!dateAddedElem) {
            const dateSection = document.createElement('div');
            dateSection.className = 'modal-section date-section';
            const heading = document.createElement('div');
            heading.className = 'modal-section-title';
            heading.textContent = 'Date Added';
            dateAddedElem = document.createElement('div');
            dateAddedElem.id = 'modal-added-date';
            dateSection.append(heading, dateAddedElem);
            document.querySelector('.modal-body').appendChild(dateSection);
        }
        dateAddedElem.textContent = dateAdded;

        const modalBody = document.querySelector('.modal-body');
        modalBody.scrollTop = 0;
        document.body.style.overflow = 'hidden';
        modalOverlay.classList.add('active');
        requestAnimationFrame(() => {
            modalBody.scrollTop = 0;
        });
    }

    // Replace upstream paths that either scale poorly or interpolate media metadata
    // through innerHTML. Existing listeners resolve these bindings when invoked.
    createTextPlaceholder = (container, title) => safeTextPlaceholder(container, title);
    setGenreFilter = safeSetGenreFilter;
    updateGenreDropdown = safeUpdateGenreDropdown;
    updateGenreDrawer = safeUpdateGenreDrawer;
    displayMedia = largeLibraryDisplayMedia;
    filterAndSortMedia = largeLibraryFilterAndSortMedia;
    openModal = safeOpenModal;

    window.__beyondGlimpseLargeLibraryReady = true;
    window.__beyondGlimpseMetadataSafe = true;
    window.dispatchEvent(new Event('beyond-glimpse:ready'));
})();
