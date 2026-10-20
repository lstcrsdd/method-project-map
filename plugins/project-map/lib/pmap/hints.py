"""What an agent should know right after it edits code: who else depends on what it changed.

`pmap hook post-edit` runs after Claude Code's Edit / Write and Codex's apply_patch. It finds the functions and
classes the edit touched and names, in a few lines, what depends on them elsewhere: callers in other files and the
other side of every link between languages (the Swift or Kotlin handler of a Flutter channel method, the Rust
function behind FFI or JNI, a channel method a platform does not handle). It says nothing when nothing outside the
file depends on the change, and never repeats itself within a session.

The map comes from the cache the session-start hook writes (~/.pmap/cache); after an edit the cache is refreshed in
the background, so the next note sees the code as it is now.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pmap.models import ActualEdge, ActualMap, ActualNode

MAX_SYMBOLS = 3
MAX_ITEMS = 6
MAX_CHARS = 2400
HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".h++")
SYNC_SCAN_FILES = 600  # without a usable cached map, a hook scans right away only a project this small
SESSION_TTL = 2 * 24 * 3600
_DECLARED = re.compile(r"\b(?:def|func|fun|fn|function|class|struct|enum|interface|protocol|trait|object|record|"
                       r"extension|mixin)\s+([A-Za-z_]\w*)|\b([A-Za-z_]\w*)\s*\([^;{}()]*\)\s*(?:const\s*)?"
                       r"(?:->[^{;]*|:\s*[\w<>?,. ]+)?\s*(?:async\s*)?\{")


def pmap_home() -> Path:
    from pmap.connect import pmap_home as home

    return home()


# ====================================================================== the map cache


def cache_path(root: Path) -> Path:
    key = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return pmap_home() / "cache" / f"{key}.json"


def save_cache(root: Path, actual: ActualMap, scan_seconds: float = 0.0) -> None:
    """The map, and next to it the compact index the note after an edit reads (a big map loads in seconds)."""
    path = cache_path(root)
    saved = time.time()
    nodes = [[n.id, n.name, n.parent_id, n.kind, n.location.path, n.location.line_start, n.location.line_end]
             for n in actual.nodes if n.location is not None and not n.external]
    edges = [[e.from_id, e.to_id, e.kind, e.confidence, e.location.path if e.location else None,
              e.location.line if e.location else 0, e.candidates, e.bridge]
             for e in actual.edges if e.kind in ("calls", "inherits") and (e.confidence != "unresolved" or e.bridge)]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for target, data in ((path, {"root": str(root.resolve()), "saved": saved, "map": actual.to_dict()}),
                             (path.with_suffix(".idx.json"), {"saved": saved, "nodes": nodes, "edges": edges}),
                             (path.with_suffix(".meta.json"), {"saved": saved, "scan_seconds": scan_seconds})):
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
    except OSError:
        pass


def load_cache(root: Path) -> Tuple[Optional[ActualMap], float]:
    try:
        data = json.loads(cache_path(root).read_text(encoding="utf-8"))
        return ActualMap.from_dict(data["map"]), float(data.get("saved", 0))
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0.0


def current_map(root: Path, scan: bool = True) -> Optional[ActualMap]:
    """The cached map while it matches the files on disk; otherwise a fresh scan (cached) when `scan`, else None
    while a scan runs in the background."""
    from pmap.loader import load_map
    from pmap.stale import is_map_fresh

    cached, _saved = load_cache(root)
    if cached is not None and not (root / ".pmap" / "actual.json").exists():
        try:
            if is_map_fresh(root, cached):
                return cached
        except Exception:
            pass
    if not scan:
        refresh_in_background(root)
        return None
    started = time.time()
    actual = load_map(root, warn=False)
    save_cache(root, actual, time.time() - started)
    return actual


def refresh_cache(root: Path) -> None:
    """Rescan the project and save the map (run in the background by `pmap hook refresh <root>`)."""
    from pmap.loader import load_map

    marker = cache_path(root).with_suffix(".refreshing")
    try:
        started = time.time()
        actual = load_map(root, warn=False)
        save_cache(root, actual, time.time() - started)
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def refresh_in_background(root: Path) -> None:
    marker = cache_path(root).with_suffix(".refreshing")
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < 120:
            return  # a refresh is already running
        try:  # a big project is rescanned at most every two scan times, a small one within seconds
            meta = json.loads(cache_path(root).with_suffix(".meta.json").read_text(encoding="utf-8"))
            if time.time() - float(meta["saved"]) < max(5.0, 2 * float(meta["scan_seconds"])):
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
        from pmap.connect import BOOTSTRAP

        lib = str(Path(__file__).resolve().parent.parent)
        kwargs: Dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                     "stderr": subprocess.DEVNULL, "cwd": str(root)}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, "-I", "-c", BOOTSTRAP, lib, "hook", "refresh", str(root)], **kwargs)
    except Exception:
        pass


# ====================================================================== what the edit changed


@dataclass
class Change:
    path: Path  # absolute
    start: int  # first changed line; lines above it are unchanged, so the cached map's numbers hold up to here
    old_lines: int  # lines the change replaced
    old_text: str = ""
    whole_file: bool = False


def _abs(value, cwd: Path) -> Optional[Path]:
    if not value:
        return None
    p = Path(str(value))
    return p if p.is_absolute() else (cwd / p)


def _read(path: Optional[Path]) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""


def _line_of(text: str, needle: str, every: bool) -> List[int]:
    lines, pos = [], text.find(needle) if needle else -1
    while pos >= 0 and len(lines) < 5:
        lines.append(text.count("\n", 0, pos) + 1)
        if not every:
            break
        pos = text.find(needle, pos + max(1, len(needle)))
    return lines


def changes_from(payload: dict) -> List[Change]:
    """The changes a Claude Code Edit / MultiEdit / Write or a Codex apply_patch made."""
    tool = str(payload.get("tool_name") or "")
    data = payload.get("tool_input")
    cwd = Path(str(payload.get("cwd") or os.getcwd()))
    if not isinstance(data, dict):
        data = {"command": data} if isinstance(data, (str, list)) else {}
    patch = next((v for v in _strings(data) if "*** Begin Patch" in v), None)
    if patch is not None:
        return _patch_changes(patch, cwd)
    path = _abs(data.get("file_path"), cwd)
    if path is None:
        return []
    if tool == "Write" or ("content" in data and "old_string" not in data and "edits" not in data):
        return [Change(path, 1, 1 << 30, whole_file=True)]
    text = _read(path)
    out = []
    for edit in (data.get("edits") if isinstance(data.get("edits"), list) else [data]):
        if not isinstance(edit, dict):
            continue
        old, new = str(edit.get("old_string") or ""), str(edit.get("new_string") or "")
        found = _line_of(text, new, bool(edit.get("replace_all"))) if new.strip() else []
        for start in found or [0]:  # 0: a deletion; only the names it declared can tell what it removed
            out.append(Change(path, start, old.count("\n") + 1, old))
    return out


def _strings(value) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _patch_changes(patch: str, cwd: Path) -> List[Change]:
    """Codex apply_patch: `*** Update File: p` with `@@` hunks of ` `, `-` and `+` lines; Add / Delete File."""
    out: List[Change] = []
    current: Optional[Tuple[str, Path]] = None
    hunk: Dict[str, List[str]] = {"old": [], "new": [], "before": []}

    def flush() -> None:
        if current is None or current[0] != "Update" or not (hunk["old"] or hunk["new"]):
            return
        text = _read(current[1])
        new_block = "\n".join(hunk["new"])
        start = (_line_of(text, new_block, False) or [0])[0] if new_block.strip() else 0
        if not start and hunk["before"]:
            anchor = _line_of(text, "\n".join(hunk["before"][-3:]), False)
            start = anchor[0] + min(3, len(hunk["before"])) if anchor else 0
        out.append(Change(current[1], start, max(1, len(hunk["old"])), "\n".join(hunk["old"])))

    for line in patch.splitlines():
        m = re.match(r"\*\*\* (Update|Add|Delete) File: (.+)", line)
        if m:
            flush()
            hunk = {"old": [], "new": [], "before": []}
            current = (m.group(1), _abs(m.group(2).strip(), cwd) or Path(m.group(2).strip()))
            if current[0] == "Delete":
                out.append(Change(current[1], 1, 1 << 30, whole_file=True))
            continue
        if line.startswith("@@") or line.startswith("*** End"):
            flush()
            hunk = {"old": [], "new": [], "before": []}
            continue
        if line.startswith("-"):
            hunk["old"].append(line[1:])
        elif line.startswith("+"):
            hunk["new"].append(line[1:])
        elif not hunk["old"] and not hunk["new"]:
            hunk["before"].append(line[1:] if line.startswith(" ") else line)
    flush()
    return out


# ====================================================================== who depends on it


class _Loc:
    __slots__ = ("path", "line_start", "line_end", "line")

    def __init__(self, path: str, start: int, end: int) -> None:
        self.path, self.line_start, self.line_end, self.line = path, start, end, start


class _Node:
    __slots__ = ("id", "name", "parent_id", "kind", "location", "external")

    def __init__(self, row: list) -> None:
        self.id, self.name, self.parent_id, self.kind = row[0], row[1], row[2], row[3]
        self.location, self.external = _Loc(row[4], row[5], row[6]), False


class _Edge:
    __slots__ = ("from_id", "to_id", "kind", "confidence", "location", "candidates", "bridge")

    def __init__(self, row: list) -> None:
        self.from_id, self.to_id, self.kind, self.confidence = row[0], row[1], row[2], row[3]
        self.location = _Loc(row[4], row[5], row[5]) if row[4] else None
        self.candidates, self.bridge = row[6], row[7]


def load_index(root: Path) -> Tuple[Optional["_Index"], float]:
    try:
        data = json.loads(cache_path(root).with_suffix(".idx.json").read_text(encoding="utf-8"))
        return _Index([_Node(r) for r in data["nodes"]], [_Edge(r) for r in data["edges"]]), float(data["saved"])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None, 0.0


class _Index:
    """Who depends on what, from map nodes and edges (ActualNode / ActualEdge or the compact cache rows)."""

    @classmethod
    def from_map(cls, actual: ActualMap) -> "_Index":
        return cls(actual.nodes, actual.edges)

    def __init__(self, nodes, edges) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.by_file: Dict[str, List[ActualNode]] = {}
        for n in nodes:
            if n.location is not None and n.kind != "module" and not n.external:
                self.by_file.setdefault(n.location.path, []).append(n)
        self.incoming: Dict[str, List[ActualEdge]] = {}
        self.outgoing_links: Dict[str, List[ActualEdge]] = {}
        self.ffi: Dict[str, List[ActualEdge]] = {}  # C symbol -> the calls that reach its definition
        self.headers: Dict[str, List[ActualNode]] = {}  # name -> C / C++ header declarations
        for e in edges:
            if e.kind not in ("calls", "inherits"):
                continue
            for target in ({e.to_id} | set(e.candidates or [])):
                self.incoming.setdefault(target, []).append(e)
            if e.bridge:
                self.outgoing_links.setdefault(e.from_id, []).append(e)
                if e.bridge.get("via") == "ffi":
                    self.ffi.setdefault(str(e.bridge.get("name")), []).append(e)
        for n in nodes:
            if n.kind == "function" and n.location is not None and n.location.path.endswith(HEADER_EXTS):
                self.headers.setdefault(n.name, []).append(n)

    def path(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node.location.path if node is not None and node.location else ""

    def label(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return node_id.split(":")[-1]
        parent = self.nodes.get(node.parent_id or "")
        if parent is not None and parent.kind == "class" and not node.name.startswith(parent.name):
            return f"{parent.name}.{node.name}"
        return node.name

    def touched(self, rel: str, change: Change) -> List[Tuple[ActualNode, bool]]:
        """(node, signature) for the declarations a change touched; signature = its head or the whole file."""
        nodes = self.by_file.get(rel, [])
        declared = {a or b for a, b in _DECLARED.findall(change.old_text)} if change.old_text else set()
        out: Dict[str, Tuple[ActualNode, bool]] = {}
        if change.whole_file:
            for n in nodes:
                out[n.id] = (n, True)
        elif change.start:
            end = change.start + max(0, change.old_lines - 1)
            inside = [n for n in nodes if n.location.line_start <= end and n.location.line_end >= change.start]
            innermost = [n for n in inside if not any(o is not n and o.location.line_start >= n.location.line_start
                                                      and o.location.line_end <= n.location.line_end
                                                      and o.parent_id == n.id for o in inside)]
            for n in innermost:
                head = change.start <= n.location.line_start + 1 or n.name in declared
                out[n.id] = (n, head)
        for n in nodes:  # a rename or a deletion: the old text declared these
            if n.name in declared and n.id not in out:
                out[n.id] = (n, True)
        return list(out.values())

    def note(self, node: ActualNode, signature: bool, change: Change) -> List[str]:
        rel = node.location.path
        lines: List[str] = []
        links_out = [e for e in self.outgoing_links.get(node.id, [])]
        links_in = [e for e in self.incoming.get(node.id, []) if e.bridge and not e.bridge.get("missing")]
        if links_in and not change.whole_file and change.start and not signature:
            # A call that lands on the whole declaration (a Rust function behind FFI) concerns any edit of it;
            # one that lands on a branch (`case "connect":` of a channel handler) concerns edits of that branch.
            end = change.start + max(0, change.old_lines - 1)
            links_in = [e for e in links_in if int((e.bridge.get("to") or {}).get("line", 0)) == node.location.line_start
                        or change.start - 1 <= int((e.bridge.get("to") or {}).get("line", 0)) <= end + 1]
        for e in links_out:
            b = e.bridge or {}
            what = _what(b)
            if b.get("missing"):
                to = b.get("to") or {}
                lines.append(f"  ! {what} is not handled in {b['missing']} (its other methods: {to.get('path')}:"
                             f"{to.get('line')}) — a call fails there at run time")
                continue
            to = b.get("to") or {}
            lines.append(f"  → {to.get('path', self.path(e.to_id))}:{to.get('line', '?')}  {self.label(e.to_id)}"
                         f"  ({_lang(to.get('path', ''))}, {what}{', possible' if e.confidence != 'resolved' else ''})")
        for e in links_in:
            where = f"{e.location.path}:{e.location.line}" if e.location else self.path(e.from_id)
            lines.append(f"  ← {where}  {self.label(e.from_id)}  ({_lang(where)}, {_what(e.bridge or {})})")
        if links_in and any((e.bridge or {}).get("via") == "ffi" for e in links_in):
            for decl in self.headers.get(node.name, []):  # the C declaration other languages compile against
                lines.append(f"  = {decl.location.path}:{decl.location.line_start}  declares {node.name} for C, C++ "
                             "and Swift — keep it in step")
        if rel.endswith(HEADER_EXTS) and node.name in self.ffi:  # a header declaration: the definition behind it
            reached = self.ffi[node.name]
            for target in sorted({e.to_id for e in reached}):
                to = (reached[0].bridge or {}).get("to") or {}
                lines.append(f"  = {to.get('path', self.path(target))}:{to.get('line', '?')}  {self.label(target)}  "
                             f"({_lang(to.get('path', ''))}, the definition of this declaration)")
            for e in reached:
                where = f"{e.location.path}:{e.location.line}" if e.location else self.path(e.from_id)
                lines.append(f"  ← {where}  {self.label(e.from_id)}  ({_lang(where)}, C ABI {node.name})")
        callers = [e for e in self.incoming.get(node.id, []) if not e.bridge and e.location is not None
                   and e.location.path != rel and e.confidence in ("resolved", "inferred")]
        if callers:
            files = sorted({e.location.path for e in callers})
            if signature:
                lines.append(f"  used in {len(files)} other file{'s' if len(files) != 1 else ''}:")
                by_caller: Dict[Tuple[str, str], List[ActualEdge]] = {}
                for e in sorted(callers, key=lambda e: (e.location.path, e.location.line)):
                    by_caller.setdefault((e.location.path, e.from_id), []).append(e)
                for (path, caller), es in list(by_caller.items())[:MAX_ITEMS]:
                    kind = "" if any(e.confidence == "resolved" for e in es) else " (possible)"
                    where = ",".join(str(e.location.line) for e in es[:4]) + ("..." if len(es) > 4 else "")
                    lines.append(f"    {path}:{where}  {self.label(caller)}{kind}")
                if len(by_caller) > MAX_ITEMS:
                    lines.append(f"    ... {len(by_caller) - MAX_ITEMS} more: `pmap show {node.id}`")
            else:
                sample = ", ".join(f"{e.location.path}:{e.location.line}" for e in callers[:3])
                lines.append(f"  used in {len(callers)} place{'s' if len(callers) != 1 else ''} in {len(files)} other "
                             f"file{'s' if len(files) != 1 else ''} ({sample}{', ...' if len(callers) > 3 else ''})")
        if not lines:
            return []
        loc = node.location
        return [f"{self.label(node.id)} ({loc.path}:{loc.line_start})"] + lines


def _what(b: dict) -> str:
    from pmap.langs.bridges import VIA_NAMES

    via = VIA_NAMES.get(str(b.get("via")), str(b.get("via")))
    method = f' "{b["method"]}"' if b.get("method") else ""
    if b.get("via") in ("channel", "event-channel", "message-channel", "pigeon"):
        return f"{via} {b.get('name')}{method}"
    return f"{via} {b.get('name')}"


def _lang(path: str) -> str:
    from pmap.langs.bridges import LANGUAGE_NAMES, language_of

    lang = language_of(path.split(":")[0])
    return LANGUAGE_NAMES.get(lang, lang or "?")


# ====================================================================== the hook


def _session_file(session_id: str) -> Path:
    return pmap_home() / "sessions" / (hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:16] + ".json")


def _seen(session_id: str) -> Dict[str, int]:
    if not session_id:
        return {}
    try:
        return dict(json.loads(_session_file(session_id).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def _remember(session_id: str, seen: Dict[str, int]) -> None:
    if not session_id:
        return
    path = _session_file(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen), encoding="utf-8")
        for old in path.parent.glob("*.json"):  # forget sessions nobody resumed for two days
            if time.time() - old.stat().st_mtime > SESSION_TTL:
                old.unlink()
    except OSError:
        pass


def post_edit_context(payload: dict) -> Optional[str]:
    from pmap import langs
    from pmap.hook import count_source_files, project_root

    changes = [c for c in changes_from(payload) if c.path.suffix in langs.EXT_TO_LANGUAGE or c.path.suffix == ".py"]
    if not changes:
        return None
    # The session's project, not the nearest marker above the file: in a Flutter app `rust/Cargo.toml` is part of it.
    root = project_root(Path(str(payload.get("cwd") or changes[0].path.parent)))
    if root is None or root.resolve() not in changes[0].path.resolve().parents:
        root = project_root(changes[0].path.parent)
    if root is None:
        return None
    index, _saved = load_index(root)
    if index is None:
        actual, _saved = load_cache(root)
        if actual is None:
            if count_source_files(root, SYNC_SCAN_FILES, mapped_only=True) > SYNC_SCAN_FILES:
                refresh_in_background(root)
                return None
            from pmap.loader import load_map

            actual = load_map(root, warn=False)
            save_cache(root, actual)
        index = _Index.from_map(actual)
    session = str(payload.get("session_id") or "")
    seen = _seen(session)
    notes: List[List[str]] = []
    for change in changes:
        try:
            rel = change.path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        for node, signature in index.touched(rel, change):
            level = 2 if signature else 1
            if seen.get(node.id, 0) >= level:
                continue
            note = index.note(node, signature, change)
            if note:
                seen[node.id] = level
                notes.append(note)
    refresh_in_background(root)  # the edit changed the code: the next note should see it
    if not notes:
        return None
    notes.sort(key=len, reverse=True)
    _remember(session, seen)
    out = ["Method Project Map — code elsewhere depends on what you just changed; keep it in step:"]
    for note in notes[:MAX_SYMBOLS]:
        out.extend(note)
    if len(notes) > MAX_SYMBOLS:
        out.append(f"(and {len(notes) - MAX_SYMBOLS} more changed declarations with users elsewhere)")
    text = "\n".join(out)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n... (`pmap show` for more)"
