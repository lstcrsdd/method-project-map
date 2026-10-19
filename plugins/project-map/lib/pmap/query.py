"""Selective map queries for agents: find symbols by name, show one node with its neighbours."""

from __future__ import annotations

import re

from pmap.models import ActualEdge, ActualMap, ActualNode

_DEPENDENCY_KINDS = ("imports", "calls", "inherits")


def _loc(node: ActualNode) -> str:
    if node.location is None:
        return "(external)"
    loc = node.location
    return f"{loc.path}:{loc.line_start}-{loc.line_end}"


def _edge_line(edge: ActualEdge) -> str:
    return f"{edge.location.path}:{edge.location.line}" if edge.location else ""


def _words(text: str) -> list[str]:
    """`calculate_tax` -> [calculate, tax]; `TaxCalculator.compute` -> [tax, calculator, compute]."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4:
        stem = min(len(a), len(b), 5)
        return a[:stem] == b[:stem]
    return False


def find_nodes(actual: ActualMap, query: str, limit: int = 30) -> list[ActualNode]:
    """Nodes whose name matches `query`: exact names first, then dotted-suffix and substring matches."""
    q = query.strip().lower().replace("::", ".")  # Rust / C++ paths: `Type::method` == `Type.method`
    if not q:
        return []
    exact, suffix, partial = [], [], []
    for node in actual.nodes:
        if node.external:
            continue
        name = node.name.lower().replace("::", ".")
        qualified = node.qualified_name.lower().replace("::", ".")
        if name == q or qualified == q:
            exact.append(node)
        elif qualified.endswith("." + q):
            suffix.append(node)
        elif q in qualified:
            partial.append(node)
    return (exact + suffix + partial)[:limit]


def suggest_nodes(actual: ActualMap, query: str, limit: int = 10) -> list[ActualNode]:
    """Closest symbols by shared words, for queries that match nothing literally (`calculate_tax`)."""
    q_words = _words(query)
    scored = []
    for node in actual.nodes:
        if node.external:
            continue
        name_words = _words(node.name)
        context_words = _words(node.qualified_name)[: -len(name_words) or None]
        in_name = [w for w in q_words if any(_similar(w, n) for n in name_words)]
        in_context = [w for w in q_words if any(_similar(w, c) for c in context_words)]
        covered = len(set(in_name) | set(in_context))  # query words explained anywhere: the main key
        score = 2 * len(in_name) + len(in_context)
        if score:
            scored.append((-covered, -score, len(node.qualified_name), node.id, node))
    scored.sort(key=lambda item: item[:4])
    return [node for *_rest, node in scored[:limit]]


def format_find(nodes: list[ActualNode], query: str, suggestions: list[ActualNode] | None = None) -> str:
    if not nodes:
        if not suggestions:
            return f"No symbols matching {query!r}.\n"
        header = f"No symbols named {query!r}. Closest by name:\n"
        return header + format_find(suggestions, query)
    lines = []
    for n in nodes:
        sig = n.signature or ""
        lines.append(f"{n.id}  {n.kind}  {_loc(n)}  {n.name}{sig}".rstrip())
    return "\n".join(lines) + "\n"


def resolve_ref(actual: ActualMap, ref: str) -> ActualNode | None:
    """Accept a node id, a repository-relative file path, or an exact qualified name."""
    ref = ref.strip()
    by_id = {n.id: n for n in actual.nodes}
    if ref in by_id:
        return by_id[ref]
    norm = ref.lstrip("./")
    for n in actual.nodes:
        if n.kind == "module" and n.location is not None and n.location.path == norm:
            return n
    matches = [n for n in actual.nodes if n.qualified_name == ref]
    return matches[0] if len(matches) == 1 else None


def describe_node(actual: ActualMap, node: ActualNode, max_items: int = 40) -> str:
    """Compact text: location, signature, children, outgoing and incoming dependency edges."""
    by_id = {n.id: n for n in actual.nodes}
    children = [n for n in actual.nodes if n.parent_id == node.id]
    outgoing = [e for e in actual.edges if e.kind in _DEPENDENCY_KINDS and e.from_id == node.id]
    incoming = [
        e for e in actual.edges
        if e.kind in _DEPENDENCY_KINDS and (e.to_id == node.id or node.id in (e.candidates or []))
    ]

    lines = [f"{node.id}  {node.kind}  {_loc(node)}"]
    if node.signature:
        lines.append(f"  signature: {node.name}{node.signature}")
    if node.docstring_summary:
        lines.append(f"  doc: {node.docstring_summary}")
    if node.parent_id:
        lines.append(f"  parent: {node.parent_id}")

    if children:
        lines.append(f"  contains ({len(children)}):")
        for child in children[:max_items]:
            lines.append(f"    {child.kind} {child.name}{child.signature or ''}  {_loc(child)}")
        if len(children) > max_items:
            lines.append(f"    ... +{len(children) - max_items} more")

    def section(title: str, edges: list[ActualEdge], incoming_side: bool) -> None:
        if not edges:
            return
        known = [e for e in edges if e.confidence != "unresolved"]
        unknown = [e for e in edges if e.confidence == "unresolved"]
        lines.append(f"  {title} ({len(edges)}):")
        for e in known[:max_items]:
            other = e.from_id if incoming_side else e.to_id
            where = _edge_line(e)
            if e.confidence == "inferred" and not incoming_side and e.candidates and len(e.candidates) > 1:
                other = "one of " + ", ".join(e.candidates)
            elif other in by_id and not incoming_side:
                other = f"{other}  [{_loc(by_id[other])}]"
            lines.append(f"    {e.confidence:<8} {e.kind:<8} {other}  @ {where}")
        if len(known) > max_items:
            lines.append(f"    ... +{len(known) - max_items} more")
        if unknown:
            exprs = sorted({e.target_symbol or e.to_id for e in unknown})
            shown = ", ".join(exprs[:15]) + (" ..." if len(exprs) > 15 else "")
            lines.append(f"    unresolved ({len(unknown)}): {shown}")

    section("depends on", outgoing, incoming_side=False)
    section("used by", incoming, incoming_side=True)
    return "\n".join(lines) + "\n"


def source_of(node: ActualNode, repo_root, context_lines: int = 0, max_lines: int = 400) -> str:
    """The node's source lines, numbered; only files inside `repo_root` are read."""
    from pathlib import Path

    if node.location is None:
        return f"{node.id} is external: no source in this repository.\n"
    root = Path(repo_root).resolve()
    path = (root / node.location.path).resolve()
    if root not in path.parents:
        raise ValueError(f"{node.location.path} is outside the repository")
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(1, node.location.line_start - context_lines)
    end = min(len(lines), node.location.line_end + context_lines)
    truncated = end - start + 1 > max_lines
    if truncated:
        end = start + max_lines - 1
    width = len(str(end))
    body = [f"{n:>{width}}  {lines[n - 1]}" for n in range(start, end + 1)]
    head = f"# {node.location.path}:{start}-{end} ({node.id})"
    tail = [f"... truncated at {max_lines} lines"] if truncated else []
    return "\n".join([head] + body + tail) + "\n"
