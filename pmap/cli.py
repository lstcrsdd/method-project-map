"""Command-line interface for Method Project Map (`pmap`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pmap.loader import find_design, load_map
from pmap.mermaid import format_agent_context, generate_mermaid
from pmap.models import ActualMap, DesignMap
from pmap.query import describe_node, find_nodes, format_find, resolve_ref, source_of, suggest_nodes
from pmap.reflexion import check_reflexion, format_reflexion_text
from pmap.scanner import ProjectScanner
from pmap.stale import check_stale_nodes, find_unscanned_files, format_stale_text, is_map_fresh
from pmap.yaml_loader import load_design_map, validate_pmf_document


def _load_or_scan_actual(repo_root: Path) -> ActualMap:
    return load_map(repo_root)


def _auto_find_design(repo_root: Path, explicit_design: str | None) -> DesignMap | None:
    return find_design(repo_root, explicit_design)


def cmd_scan(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    scanner = ProjectScanner(import_roots=args.root)
    actual_map = scanner.scan_repository(repo_root)
    doc = actual_map.to_dict()

    errors = validate_pmf_document(doc)
    if errors:
        print(f"Error: generated actual map failed schema validation: {errors}", file=sys.stderr)
        return 1

    out_path = Path(args.output) if args.output else (repo_root / ".pmap" / "actual.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    internal_nodes = [n for n in actual_map.nodes if not n.external]
    external_nodes = [n for n in actual_map.nodes if n.external]
    resolved_edges = [e for e in actual_map.edges if e.confidence == "resolved"]
    inferred_edges = [e for e in actual_map.edges if e.confidence == "inferred"]
    unresolved_edges = [e for e in actual_map.edges if e.confidence == "unresolved"]

    print(
        f"Scanned {repo_root.name}: {len(internal_nodes)} internal nodes, "
        f"{len(external_nodes)} external packages, "
        f"{len(resolved_edges)} resolved / {len(inferred_edges)} inferred / "
        f"{len(unresolved_edges)} unresolved edges -> {out_path}"
    )
    print(f"Import roots: {', '.join(actual_map.import_roots)}")
    for err in actual_map.errors:
        where = f"{err.path}:{err.line}" if err.line else err.path
        print(f"warning: skipped {where} ({err.kind}): {err.message}", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    design = _auto_find_design(repo_root, args.design)
    if design is None:
        print("Error: design map not found. Pass --design <path/to/design.yaml>.", file=sys.stderr)
        return 2

    actual = _load_or_scan_actual(repo_root)
    report = check_reflexion(design, actual, include_type_only=args.include_type_only)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_reflexion_text(report), end="")

    if args.strict and not report.is_clean:
        return 1
    return 0


def cmd_mermaid(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    design = _auto_find_design(repo_root, args.design)
    actual = _load_or_scan_actual(repo_root)
    diagram = generate_mermaid(actual=actual, design=design, level=args.level)
    print(diagram, end="")
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    actual_file = repo_root / ".pmap" / "actual.json"
    if not actual_file.exists():
        print(f"Error: {actual_file} does not exist. Run `pmap scan {args.repo}` first.", file=sys.stderr)
        return 2

    actual_data = json.loads(actual_file.read_text(encoding="utf-8"))
    actual = ActualMap.from_dict(actual_data)
    stale_list = check_stale_nodes(repo_root, actual)
    unscanned = find_unscanned_files(repo_root, actual)

    if args.json:
        print(json.dumps(
            {"stale_nodes": [s.to_dict() for s in stale_list], "unscanned_files": unscanned},
            indent=2,
            ensure_ascii=False,
        ))
    else:
        print(format_stale_text(stale_list, unscanned), end="")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    design = _auto_find_design(repo_root, args.design)
    actual = _load_or_scan_actual(repo_root)
    text = format_agent_context(actual=actual, design=design, max_chars=args.max_chars)
    print(text, end="")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    actual = _load_or_scan_actual(Path(args.repo).resolve())
    found = find_nodes(actual, args.name, limit=args.limit)
    suggestions = [] if found else suggest_nodes(actual, args.name)
    print(format_find(found, args.name, suggestions), end="")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    actual = _load_or_scan_actual(Path(args.repo).resolve())
    node = resolve_ref(actual, args.ref)
    if node is None:
        print(f"Unknown node {args.ref!r}. Use `pmap find <name>` to look up node ids.", file=sys.stderr)
        return 2
    print(describe_node(actual, node), end="")
    return 0


def cmd_source(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    actual = _load_or_scan_actual(repo_root)
    node = resolve_ref(actual, args.ref)
    if node is None:
        print(f"Unknown node {args.ref!r}. Use `pmap find <name>` to look up node ids.", file=sys.stderr)
        return 2
    print(source_of(node, repo_root, context_lines=args.context), end="")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from pmap.mcp import serve_stdio

    return serve_stdio(default_repo=args.repo)


def cmd_serve(args: argparse.Namespace) -> int:
    from pmap.web import serve

    return serve(Path(args.repo).resolve(), host=args.host, port=args.port, design=args.design, open_browser=args.open)


def cmd_ui(args: argparse.Namespace) -> int:
    from pmap.web import launch_ui

    return launch_ui(args.folder, port=args.port)


def cmd_shortcut(args: argparse.Namespace) -> int:
    from pmap.web import desktop

    try:
        if args.remove:
            removed = desktop.remove_shortcut()
            print(f"Removed {removed}" if removed else "No Method Project Map shortcut to remove.")
        else:
            print(f"Created {desktop.install_shortcut()}")
    except Exception as exc:  # a missing osacompile / PowerShell must not break the installer
        print(f"Could not {'remove' if args.remove else 'create'} the shortcut: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_html(args: argparse.Namespace) -> int:
    from pmap.web import export_html

    out = export_html(Path(args.repo).resolve(), Path(args.output), design=args.design)
    print(f"Wrote {out}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    from pmap.connect import connect

    claude, codex = (True, True) if args.all else (args.claude or not args.codex, args.codex)
    for line in connect(claude=claude, codex=codex):
        print(line)
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    from pmap.connect import disconnect

    claude, codex = (True, True) if args.all or not (args.claude or args.codex) else (args.claude, args.codex)
    for line in disconnect(claude=claude, codex=codex):
        print(line)
    return 0


def cmd_connection(args: argparse.Namespace) -> int:
    from pmap.connect import status

    print(json.dumps(status(), indent=2))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    from pmap.hook import run

    return run(args.event, sys.stdin.read() if not sys.stdin.isatty() else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmap",
        description="Method Project Map (PMF v0) — hierarchical static scanner and Reflexion Model verifier.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_scan = subparsers.add_parser("scan", help="Scan a Python repository and write .pmap/actual.json")
    p_scan.add_argument("repo", help="Path to the repository root")
    p_scan.add_argument("--output", "-o", help="Optional custom output path for actual.json")
    p_scan.add_argument(
        "--root",
        action="append",
        help="Import root relative to the repository (repeatable). Default: auto-detect `src/` layout.",
    )
    p_scan.set_defaults(func=cmd_scan)

    p_check = subparsers.add_parser("check", help="Compare actual code dependencies against design.yaml")
    p_check.add_argument("repo", help="Path to the repository root")
    p_check.add_argument("--design", "-d", required=False, help="Path to design.yaml")
    p_check.add_argument("--json", action="store_true", help="Output report as JSON")
    p_check.add_argument("--strict", action="store_true", help="Exit with code 1 if divergences exist")
    p_check.add_argument(
        "--include-type-only",
        action="store_true",
        help="Also count imports made only for type checking (`if TYPE_CHECKING:`)",
    )
    p_check.set_defaults(func=cmd_check)

    p_mermaid = subparsers.add_parser("mermaid", help="Generate Mermaid diagram for subsystem or module level")
    p_mermaid.add_argument("repo", help="Path to the repository root")
    p_mermaid.add_argument(
        "--level",
        "-l",
        choices=["subsystem", "module"],
        default="subsystem",
        help="Granularity level: 'subsystem' or 'module'",
    )
    p_mermaid.add_argument("--design", "-d", required=False, help="Optional path to design.yaml")
    p_mermaid.set_defaults(func=cmd_mermaid)

    p_stale = subparsers.add_parser("stale", help="List nodes whose underlying files changed since last scan")
    p_stale.add_argument("repo", help="Path to the repository root")
    p_stale.add_argument("--json", action="store_true", help="Output stale nodes as JSON")
    p_stale.set_defaults(func=cmd_stale)

    p_context = subparsers.add_parser("context", help="Generate compact size-bounded text context for an AI agent")
    p_context.add_argument("repo", help="Path to the repository root")
    p_context.add_argument("--design", "-d", required=False, help="Optional path to design.yaml")
    p_context.add_argument("--max-chars", type=int, default=4000, help="Maximum character budget")
    p_context.set_defaults(func=cmd_context)

    p_find = subparsers.add_parser("find", help="Find classes, functions and methods by name")
    p_find.add_argument("name", help="Symbol name, dotted suffix (Class.method) or substring")
    p_find.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    p_find.add_argument("--limit", type=int, default=30, help="Maximum number of results")
    p_find.set_defaults(func=cmd_find)

    p_show = subparsers.add_parser("show", help="Show one node: location, signature, dependencies and users")
    p_show.add_argument("ref", help="Node id (e.g. sym:pkg.mod:Class.method), file path, or qualified name")
    p_show.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    p_show.set_defaults(func=cmd_show)

    p_source = subparsers.add_parser("source", help="Print the source of one node (function, class, method, module)")
    p_source.add_argument("ref", help="Node id, file path, or qualified name")
    p_source.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    p_source.add_argument("--context", type=int, default=0, help="Extra lines before and after")
    p_source.set_defaults(func=cmd_source)

    p_mcp = subparsers.add_parser("mcp", help="Run the MCP server (stdio) for Claude Code, Codex and other agents")
    p_mcp.add_argument("--repo", default=None, help="Default repository (default: $PMAP_REPO or the working directory)")
    p_mcp.set_defaults(func=cmd_mcp)

    p_serve = subparsers.add_parser("serve", help="Open the interactive map in a local web UI")
    p_serve.add_argument("repo", nargs="?", default=".", help="Repository root (default: current directory)")
    p_serve.add_argument("--design", "-d", help="Optional path to design.yaml")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--open", action="store_true", help="Open the browser")
    p_serve.set_defaults(func=cmd_serve)

    p_ui = subparsers.add_parser("ui", help="Open the map in the browser: the last project, or choose a folder")
    p_ui.add_argument("folder", nargs="?", help="Project folder (default: the last one opened, else ask)")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.set_defaults(func=cmd_ui)

    p_short = subparsers.add_parser("shortcut", help="Add a 'Method Project Map' app shortcut (Applications / Start menu / app menu)")
    p_short.add_argument("--remove", action="store_true", help="Remove the shortcut")
    p_short.set_defaults(func=cmd_shortcut)

    p_html = subparsers.add_parser("html", help="Export the interactive map as one self-contained HTML file")
    p_html.add_argument("repo", nargs="?", default=".", help="Repository root (default: current directory)")
    p_html.add_argument("--output", "-o", default="project-map.html")
    p_html.add_argument("--design", "-d", help="Optional path to design.yaml")
    p_html.set_defaults(func=cmd_html)

    for name, helptext in (("connect", "Give Claude Code / Codex access to project maps (skill, hook, MCP)"),
                           ("disconnect", "Remove everything `pmap connect` added")):
        p = subparsers.add_parser(name, help=helptext)
        p.add_argument("--claude", action="store_true", help="Claude Code (default for connect)")
        p.add_argument("--codex", action="store_true", help="OpenAI Codex")
        p.add_argument("--all", action="store_true", help="Both agents")
        p.set_defaults(func=cmd_connect if name == "connect" else cmd_disconnect)

    p_conn = subparsers.add_parser("connection", help="Show which agents are connected")
    p_conn.set_defaults(func=cmd_connection)

    p_hook = subparsers.add_parser("hook", help="Agent hook entry point (used by Claude Code)")
    p_hook.add_argument("event", choices=["session-start"])
    p_hook.set_defaults(func=cmd_hook)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
