"""Staleness detector comparing ActualMap node file hashes against current disk files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pmap.models import ActualMap, ActualNode
from pmap.scanner import compute_file_hash, discover_python_files


@dataclass(frozen=True)
class StaleNodeInfo:
    """Details about a single node whose underlying source file changed or was deleted."""
    node_id: str
    kind: str
    qualified_name: str
    path: str
    line_start: int
    line_end: int
    recorded_hash: str
    current_hash: str | None  # None if file was deleted on disk
    reason: str  # "hash_mismatch" | "file_missing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "recorded_hash": self.recorded_hash,
            "current_hash": self.current_hash,
            "reason": self.reason,
        }


def check_stale_nodes(repo_root: str | Path, actual: ActualMap) -> list[StaleNodeInfo]:
    """Return all internal nodes in `actual` whose source file hash differs from the current file on disk."""
    root = Path(repo_root).resolve()
    file_hash_cache: dict[str, str | None] = {}
    stale_nodes: list[StaleNodeInfo] = []

    for node in actual.nodes:
        if node.external or node.location is None:
            continue
        rel_path = node.location.path
        if rel_path not in file_hash_cache:
            abs_file = root / rel_path
            if not abs_file.exists():
                file_hash_cache[rel_path] = None
            else:
                file_hash_cache[rel_path] = compute_file_hash(abs_file)

        curr_hash = file_hash_cache[rel_path]
        if curr_hash is None:
            stale_nodes.append(
                StaleNodeInfo(
                    node_id=node.id,
                    kind=node.kind,
                    qualified_name=node.qualified_name,
                    path=rel_path,
                    line_start=node.location.line_start,
                    line_end=node.location.line_end,
                    recorded_hash=node.location.file_hash,
                    current_hash=None,
                    reason="file_missing",
                )
            )
        elif curr_hash != node.location.file_hash:
            stale_nodes.append(
                StaleNodeInfo(
                    node_id=node.id,
                    kind=node.kind,
                    qualified_name=node.qualified_name,
                    path=rel_path,
                    line_start=node.location.line_start,
                    line_end=node.location.line_end,
                    recorded_hash=node.location.file_hash,
                    current_hash=curr_hash,
                    reason="hash_mismatch",
                )
            )

    return stale_nodes


def find_unscanned_files(repo_root: str | Path, actual: ActualMap) -> list[str]:
    """Return Python files the map does not describe: added after the scan, or failed-to-parse files that changed."""
    root = Path(repo_root).resolve()
    known_paths = {n.location.path for n in actual.nodes if n.location is not None}
    error_hashes = {e.path: e.file_hash for e in actual.errors}
    result: list[str] = []
    for abs_path, rel_posix, _mod in discover_python_files(root, actual.import_roots):
        if rel_posix in known_paths:
            continue
        if rel_posix in error_hashes and error_hashes[rel_posix] == compute_file_hash(abs_path):
            continue  # still the same unparsable file, already reported in `errors`
        result.append(rel_posix)
    return result


def is_map_fresh(repo_root: str | Path, actual: ActualMap) -> bool:
    """True if every mapped file is unchanged and no Python file is missing from the map."""
    return not check_stale_nodes(repo_root, actual) and not find_unscanned_files(repo_root, actual)


def format_stale_text(stale_list: list[StaleNodeInfo], unscanned: list[str] | None = None) -> str:
    """Format a human-readable report of stale nodes and files missing from the map."""
    lines: list[str] = []
    if not stale_list:
        lines.append("All nodes are up-to-date (0 stale nodes).")
    else:
        lines.append(f"Found {len(stale_list)} stale node(s):")
        for item in stale_list:
            lines.append(
                f"  - [{item.kind}] {item.node_id} ({item.path}:L{item.line_start}-L{item.line_end}) "
                f"reason={item.reason}"
            )
    if unscanned:
        lines.append(f"Found {len(unscanned)} file(s) not covered by the map (added or changed since scan):")
        lines.extend(f"  - {path}" for path in unscanned)
    return "\n".join(lines) + "\n"
