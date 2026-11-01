#!/usr/bin/env python3

"""Run the catalogue API with a present-episodes-only Jellyfin policy.

The public TV episode browser is an availability view, not an episode guide.
Jellyfin may expose virtual/missing episode placeholders depending on server
settings and metadata. This policy asks Jellyfin to exclude missing items and
also rejects virtual markers defensively before they can enter the local cache.
"""

import catalogue_service as service
from catalogue_core import get_meta, open_db, set_meta


EPISODE_VISIBILITY_POLICY = "present-only-v2"


def present_episode_from_api(item):
    """Return a public episode only when Jellyfin says it is actually present."""
    if is_placeholder(item):
        return None
    return service._present_only_original_episode_from_api(item)


def is_placeholder(item):
    """True for metadata-only (missing/unaired/virtual) items."""
    return (
        item.get("IsMissing") is True
        or item.get("IsVirtualItem") is True
        or str(item.get("LocationType") or "").strip().casefold() == "virtual"
    )


def present_season_from_api(item):
    """Hide placeholder seasons that Jellyfin generates for missing episodes."""
    if is_placeholder(item):
        return None
    return service._present_only_original_season_from_api(item)


def install_present_episode_policy():
    """Install the episode filter and server-side missing-item constraint once."""
    if getattr(service, "_present_only_policy_installed", False):
        return

    service._present_only_policy_installed = True
    service._present_only_original_episode_from_api = service.episode_from_api
    service._present_only_original_season_from_api = service.season_from_api
    original_get_json = service.JellyfinClient.get_json

    def get_json_present_only(client, path, params=None):
        effective = dict(params or {})
        if path == "/Items" and effective.get("IncludeItemTypes") == "Episode":
            # Jellyfin can generate virtual/missing episode entries from metadata.
            # IsMissing=false keeps the API response aligned with actual library
            # availability while preserving real STRM-backed episodes.
            effective["IsMissing"] = "false"
        if path == "/Items" and effective.get("IncludeItemTypes") in ("Episode", "Season"):
            # IsMissing does not cover unaired placeholders, and virtual seasons
            # are generated too. Virtual items are never real media, so exclude
            # them outright (STRM-backed items are not Virtual and stay visible).
            effective["ExcludeLocationTypes"] = "Virtual"
        return original_get_json(client, path, params=effective)

    service.JellyfinClient.get_json = get_json_present_only
    service.episode_from_api = present_episode_from_api
    service.season_from_api = present_season_from_api


def invalidate_old_episode_cache_once():
    """Drop pre-policy season/episode payloads once so placeholders cannot linger."""
    connection = open_db()
    try:
        service.ensure_tv_cache_schema(connection)
        current = get_meta(connection, "episode_visibility_policy", "")
        if current == EPISODE_VISIBILITY_POLICY:
            return False
        connection.execute("DELETE FROM season_episode_cache")
        connection.execute("DELETE FROM series_season_cache")
        set_meta(connection, "episode_visibility_policy", EPISODE_VISIBILITY_POLICY)
        connection.commit()
        return True
    finally:
        connection.close()


def main():
    install_present_episode_policy()
    if invalidate_old_episode_cache_once():
        print("Episode cache reset for present-only availability policy", flush=True)
    service.main()


if __name__ == "__main__":
    main()
