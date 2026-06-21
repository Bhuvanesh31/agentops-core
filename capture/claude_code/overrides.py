"""Load and match the cwd -> repository_id override map.

For sessions whose cwd has no resolvable git remote (moved/deleted folders,
local-only dirs), this map routes them to a real repository instead of the
catch-all. The same map is consumed by the capture adapter (identity
resolution) and the reclassify maintenance command.
"""

import tomllib
from pathlib import Path

DEFAULT_OVERRIDES_PATH = Path(__file__).with_name("cwd_overrides.toml")


def load_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Return the cwd -> repository_id table; missing file yields an empty map."""
    p = Path(path) if path is not None else DEFAULT_OVERRIDES_PATH
    if not p.exists():
        return {}
    with p.open("rb") as f:
        data = tomllib.load(f)
    return dict(data.get("overrides", {}))


def match_override[V](cwd: str, mapping: dict[str, V]) -> V | None:
    """Longest-prefix match of cwd against mapping keys; return the value or None.

    A key matches when cwd equals it or is a path-segment child of it
    (cwd == key or cwd starts with key + "/"). The longest matching key wins,
    so a more specific mapping overrides a broader one.
    """
    best_key: str | None = None
    for key in mapping:
        if cwd == key or cwd.startswith(key + "/"):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return mapping[best_key] if best_key is not None else None
