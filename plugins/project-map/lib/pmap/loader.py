"""Loading maps the same way everywhere (CLI, MCP server, web UI): never serve a stale map silently."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from pmap.models import ActualMap, DesignMap
from pmap.scanner import ProjectScanner
from pmap.stale import is_map_fresh
from pmap.yaml_loader import load_design_map

# design.yaml wins; without one, an mDm scheme with `код:` lines is the design (pmap/mdm.py).
DESIGN_CANDIDATES = ("design.yaml", ".pmap/design.yaml", "pmap.design.yaml", "PROJECT.mdm")


def load_map(repo_root: Path, warn: bool = True) -> ActualMap:
    """Use `.pmap/actual.json` while it is fresh; otherwise rescan in memory (the file is left as is)."""
    actual_file = repo_root / ".pmap" / "actual.json"
    if actual_file.exists():
        cached = ActualMap.from_dict(json.loads(actual_file.read_text(encoding="utf-8")))
        if is_map_fresh(repo_root, cached):
            return cached
        if warn:
            print(f"warning: {actual_file} is out of date; using a fresh in-memory scan "
                  "(run `pmap scan` to update it)", file=sys.stderr)
        roots = [r for r in cached.import_roots if r != "."]
        return ProjectScanner(import_roots=roots or None).scan_repository(repo_root)
    return ProjectScanner().scan_repository(repo_root)


def find_design(repo_root: Path, explicit: Optional[str] = None) -> Optional[DesignMap]:
    if explicit:
        return load_design_map(Path(explicit))
    for name in DESIGN_CANDIDATES:
        candidate = repo_root / name
        if candidate.is_file():
            return load_design_map(candidate)
    return None


class MapCache:
    """Keeps one map per repository for long-running processes; refreshes it when files change."""

    def __init__(self) -> None:
        self._maps: Dict[Path, ActualMap] = {}

    def get(self, repo_root: Path) -> ActualMap:
        repo_root = repo_root.resolve()
        cached = self._maps.get(repo_root)
        if cached is None or not is_map_fresh(repo_root, cached):
            cached = load_map(repo_root, warn=False)
            self._maps[repo_root] = cached
        return cached

    def get_with_design(self, repo_root: Path, design: Optional[str] = None) -> Tuple[ActualMap, Optional[DesignMap]]:
        return self.get(repo_root), find_design(repo_root.resolve(), design)
