#!/usr/bin/env python3

import argparse
from pathlib import Path


START_MARKER = "        // Initialize on page load\n        loadMedia();"
START_REPLACEMENT = """        // Initialize after the Beyond Glimpse runtime has replaced the upstream hot paths.
        window.__startBeyondGlimpseMedia = () => {
            if (window.__beyondGlimpseMediaStarted) return;
            window.__beyondGlimpseMediaStarted = true;
            loadMedia();
        };
        window.addEventListener('beyond-glimpse:ready', window.__startBeyondGlimpseMedia, { once: true });
        setTimeout(window.__startBeyondGlimpseMedia, 3000);"""

# Upstream Glimpse's root-page loadMedia() uses generic data/movies.json paths.
# Beyond Glimpse themes the root document title with the selected primary server
# at container startup (for example "Beyond Glimpse - Jellyfin"). The v2 runtime
# captures String(loadMedia) before replacing it, so add a harmless source hint
# based on that runtime title. This preserves Plex/Emby compatibility while making
# root-path Jellyfin detection deterministic without exposing any credentials.
SERVER_HINT_SCRIPT = r'''    <script>
    (() => {
        window.__beyondGlimpseServerHintShim = true;
        if (typeof window.loadMedia !== 'function') return;
        const title = String(document.title || '').toLowerCase();
        const type = ['jellyfin', 'plex', 'emby'].find(candidate => title.includes(candidate));
        if (!type) return;
        const original = window.loadMedia;
        const source = Function.prototype.toString.call(original);
        try {
            Object.defineProperty(original, 'toString', {
                configurable: true,
                value: () => `${source}\n/* data/${type}/movies.json */`
            });
        } catch (_) {
            // The v2 runtime still has its normal path/static fallbacks.
        }
    })();
    </script>'''
SERVER_HINT_MARKER = '__beyondGlimpseServerHintShim'

SCRIPT_TAG = '    <script src="/large-library.js?v=4"></script>'
OLD_SCRIPT_TAGS = (
    '    <script src="/large-library.js?v=1"></script>',
    '    <script src="/large-library.js?v=2"></script>',
    '    <script src="/large-library.js?v=3"></script>',
)
STARTUP_STATUS_TAG = '    <script src="/startup-status.js?v=2"></script>'
OLD_STARTUP_TAG = '    <script src="/startup-status.js?v=1"></script>'


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False

    if START_MARKER in source:
        source = source.replace(START_MARKER, START_REPLACEMENT, 1)
        changed = True
    elif START_REPLACEMENT not in source:
        raise RuntimeError("Could not find Glimpse loadMedia initialization marker")

    if SCRIPT_TAG not in source:
        replaced = False
        for old in OLD_SCRIPT_TAGS:
            if old in source:
                source = source.replace(old, SCRIPT_TAG, 1)
                changed = True
                replaced = True
                break
        if not replaced:
            if "</body>" not in source:
                raise RuntimeError("Could not find </body> in Glimpse index")
            source = source.replace("</body>", f"{SCRIPT_TAG}\n</body>", 1)
            changed = True

    if SERVER_HINT_MARKER not in source:
        if SCRIPT_TAG not in source:
            raise RuntimeError("Could not find Beyond Glimpse runtime script tag")
        source = source.replace(SCRIPT_TAG, f"{SERVER_HINT_SCRIPT}\n{SCRIPT_TAG}", 1)
        changed = True

    if OLD_STARTUP_TAG in source:
        source = source.replace(OLD_STARTUP_TAG, STARTUP_STATUS_TAG, 1)
        changed = True
    elif STARTUP_STATUS_TAG not in source:
        if SCRIPT_TAG not in source:
            raise RuntimeError("Could not find Beyond Glimpse runtime script tag")
        source = source.replace(SCRIPT_TAG, f"{SCRIPT_TAG}\n{STARTUP_STATUS_TAG}", 1)
        changed = True

    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Glimpse web UI for Beyond Glimpse production runtime")
    parser.add_argument("path", nargs="?", default="/app/web/index.html")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
