"""MCP server (stdio, newline-delimited JSON-RPC 2.0) exposing the project map to coding agents.

Run with `pmap mcp`. Works with any MCP client (Claude Code, Codex, ...). Standard library only.
The repository defaults to $PMAP_REPO, then $CLAUDE_PROJECT_DIR, then the working directory.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from pmap import __version__
from pmap.loader import MapCache
from pmap.mermaid import format_agent_context
from pmap.query import describe_node, find_nodes, format_find, resolve_ref, source_of, suggest_nodes
from pmap.reflexion import check_reflexion, format_reflexion_text
from pmap.stale import check_stale_nodes, find_unscanned_files, format_stale_text

PROTOCOL_VERSION = "2025-06-18"
SERVER_INSTRUCTIONS = (
    "Method Project Map: a static (no LLM) index of this repository's packages, modules, classes and functions and "
    "the call/import edges between them. Start a task with pmap_overview, then pmap_find / pmap_show to locate "
    "code and pmap_source to read one symbol instead of whole files. Edge confidence: resolved = proven; "
    "inferred = candidates, verify; unresolved = unknown. Maps are rescanned automatically when files change."
)

_REPO = {"type": "string", "description": "Repository root (default: the project directory)"}
TOOLS = [
    {
        "name": "pmap_overview",
        "description": "Compact map of the repository: packages, modules (with docstrings and main names), then "
                       "signatures, within a character budget. Call this first.",
        "inputSchema": {"type": "object", "properties": {
            "repo": _REPO,
            "max_chars": {"type": "integer", "description": "Budget in characters (default 8000)"},
        }},
    },
    {
        "name": "pmap_find",
        "description": "Find classes, functions and methods by name (exact, Class.method, substring); suggests "
                       "close names when nothing matches.",
        "inputSchema": {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"}, "repo": _REPO, "limit": {"type": "integer"},
        }},
    },
    {
        "name": "pmap_show",
        "description": "One node (id like sym:pkg.mod:Class.method, a file path, or a qualified name): location, "
                       "signature, members, what it depends on and who uses it.",
        "inputSchema": {"type": "object", "required": ["ref"], "properties": {"ref": {"type": "string"}, "repo": _REPO}},
    },
    {
        "name": "pmap_source",
        "description": "Source lines of one node, numbered. Cheaper than reading the whole file.",
        "inputSchema": {"type": "object", "required": ["ref"], "properties": {
            "ref": {"type": "string"}, "repo": _REPO,
            "context_lines": {"type": "integer", "description": "Extra lines before/after (default 0)"},
        }},
    },
    {
        "name": "pmap_check",
        "description": "Compare code dependencies with the design map (design.yaml, or an mDm scheme PROJECT.mdm "
                       "with `код:` lines): convergences, divergences (architecture violations), absences.",
        "inputSchema": {"type": "object", "properties": {
            "repo": _REPO,
            "design": {"type": "string", "description": "Path to design.yaml or PROJECT.mdm (auto-detected)"},
            "include_type_only": {"type": "boolean"},
        }},
    },
    {
        "name": "pmap_stale",
        "description": "Which mapped files changed since the last `pmap scan`, and which Python files are new.",
        "inputSchema": {"type": "object", "properties": {"repo": _REPO}},
    },
    {
        "name": "pmap_scan",
        "description": "Rescan the repository and write .pmap/actual.json.",
        "inputSchema": {"type": "object", "properties": {"repo": _REPO}},
    },
]


class PmapServer:
    def __init__(self, default_repo: Optional[str] = None) -> None:
        self.default_repo = default_repo or os.environ.get("PMAP_REPO") or os.environ.get("CLAUDE_PROJECT_DIR") \
            or os.getcwd()
        self.cache = MapCache()
        self.tools: Dict[str, Callable[[Dict[str, Any]], str]] = {
            "pmap_overview": self.overview,
            "pmap_find": self.find,
            "pmap_show": self.show,
            "pmap_source": self.source,
            "pmap_check": self.check,
            "pmap_stale": self.stale,
            "pmap_scan": self.scan,
        }

    def repo(self, args: Dict[str, Any]) -> Path:
        root = Path(args.get("repo") or self.default_repo).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"repository {root} does not exist")
        return root

    # ------------------------------------------------------------------ tools

    def overview(self, args: Dict[str, Any]) -> str:
        root = self.repo(args)
        actual, design = self.cache.get_with_design(root)
        return format_agent_context(actual, design=design, max_chars=int(args.get("max_chars") or 8000))

    def find(self, args: Dict[str, Any]) -> str:
        actual = self.cache.get(self.repo(args))
        name = str(args["name"])
        found = find_nodes(actual, name, limit=int(args.get("limit") or 20))
        return format_find(found, name, [] if found else suggest_nodes(actual, name))

    def _node(self, args: Dict[str, Any]):
        actual = self.cache.get(self.repo(args))
        node = resolve_ref(actual, str(args["ref"]))
        if node is None:
            raise ValueError(f"unknown node {args['ref']!r}; use pmap_find to look up node ids")
        return actual, node

    def show(self, args: Dict[str, Any]) -> str:
        actual, node = self._node(args)
        return describe_node(actual, node)

    def source(self, args: Dict[str, Any]) -> str:
        _actual, node = self._node(args)
        return source_of(node, self.repo(args), context_lines=int(args.get("context_lines") or 0))

    def check(self, args: Dict[str, Any]) -> str:
        root = self.repo(args)
        actual, design = self.cache.get_with_design(root, args.get("design"))
        if design is None:
            return "No design map found (design.yaml, .pmap/design.yaml, PROJECT.mdm); nothing to check against.\n"
        report = check_reflexion(design, actual, include_type_only=bool(args.get("include_type_only")))
        return format_reflexion_text(report)

    def stale(self, args: Dict[str, Any]) -> str:
        root = self.repo(args)
        actual_file = root / ".pmap" / "actual.json"
        if not actual_file.exists():
            return "No .pmap/actual.json yet; every tool scans in memory. Run pmap_scan to save a map.\n"
        from pmap.models import ActualMap

        saved = ActualMap.from_dict(json.loads(actual_file.read_text(encoding="utf-8")))
        return format_stale_text(check_stale_nodes(root, saved), find_unscanned_files(root, saved))

    def scan(self, args: Dict[str, Any]) -> str:
        from pmap.scanner import ProjectScanner

        root = self.repo(args)
        actual = ProjectScanner().scan_repository(root)
        out = root / ".pmap" / "actual.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(actual.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        internal = sum(1 for n in actual.nodes if not n.external)
        return (f"Scanned {root.name}: {internal} nodes, {len(actual.edges)} edges, "
                f"{len(actual.errors)} unparsable files -> {out}\n")

    # ------------------------------------------------------------------ protocol

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None:  # notification (e.g. notifications/initialized): no reply
            return None
        try:
            if method == "initialize":
                requested = (message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
                result: Any = {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "pmap", "version": __version__},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = message.get("params") or {}
                name = params.get("name")
                if name not in self.tools:
                    return _error(msg_id, -32602, f"unknown tool {name!r}")
                try:
                    text = self.tools[name](params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except Exception as exc:  # tool errors are results, not protocol errors
                    result = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
            else:
                return _error(msg_id, -32601, f"method {method!r} not found")
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc(file=sys.stderr)
            return _error(msg_id, -32603, str(exc))
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve_stdio(default_repo: Optional[str] = None, stdin=None, stdout=None) -> int:
    server = PmapServer(default_repo)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: Optional[Dict[str, Any]] = _error(None, -32700, "parse error")
        else:
            reply = server.handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
