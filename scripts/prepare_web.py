#!/usr/bin/env python3

import argparse
from pathlib import Path


START_MARKER = "        // Initialize on page load\n        loadMedia();"
START_REPLACEMENT = """        // Initialize after the large-library renderer has replaced the upstream hot paths.
        window.__startBeyondGlimpseMedia = () => {
            if (window.__beyondGlimpseMediaStarted) return;
            window.__beyondGlimpseMediaStarted = true;
            loadMedia();
        };
        window.addEventListener('beyond-glimpse:ready', window.__startBeyondGlimpseMedia, { once: true });
        setTimeout(window.__startBeyondGlimpseMedia, 3000);"""
SCRIPT_TAG = '    <script src="/large-library.js?v=1"></script>'


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False

    if START_MARKER in source:
        source = source.replace(START_MARKER, START_REPLACEMENT, 1)
        changed = True
    elif START_REPLACEMENT not in source:
        raise RuntimeError("Could not find Glimpse loadMedia initialization marker")

    if SCRIPT_TAG not in source:
        if "</body>" not in source:
            raise RuntimeError("Could not find </body> in Glimpse index")
        source = source.replace("</body>", f"{SCRIPT_TAG}\n</body>", 1)
        changed = True

    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Glimpse web UI for Beyond Glimpse large-library rendering")
    parser.add_argument("path", nargs="?", default="/app/web/index.html")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
