"""Mermaid diagram generator (`subsystem` and `module` levels) and compact agent context builder."""

from __future__ import annotations

import re

from pmap.langs.bridges import summary_lines as bridge_summary
from pmap.models import ActualMap, ActualNode, DesignMap
from pmap.reflexion import LANGUAGE_NAMES, ReflexionReport, check_reflexion


def _safe_mermaid_id(raw_id: str) -> str:
    """Convert a node or subsystem ID into a safe Mermaid identifier."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw_id)


def _owning_module_id(node_id: str, nodes_by_id: dict[str, ActualNode]) -> str:
    curr = nodes_by_id.get(node_id)
    while curr is not None:
        if curr.kind == "module":
            return curr.id
        if not curr.parent_id:
            break
        curr = nodes_by_id.get(curr.parent_id)
    if node_id.startswith("sym:"):
        parts = node_id.split(":")
        if len(parts) >= 2:
            return f"mod:{parts[1]}"
    return node_id


def generate_mermaid(
    actual: ActualMap,
    design: DesignMap | None = None,
    level: str = "subsystem",
) -> str:
    """Generate a valid Mermaid `flowchart TD` diagram at the `subsystem` or `module` level."""
    if level not in ("subsystem", "module"):
        raise ValueError(f"Unsupported Mermaid level: {level!r} (expected 'subsystem' or 'module')")

    report: ReflexionReport | None = check_reflexion(design, actual) if design is not None else None
    lines: list[str] = ["flowchart TD"]
    link_styles: list[str] = []
    edge_idx = 0

    if level == "subsystem":
        if design is None or report is None:
            # Fallback if no design.yaml is present: group by top-level directory/package
            pkgs: set[str] = set()
            for node in actual.nodes:
                if not node.external and node.kind == "module":
                    parts = node.qualified_name.split(".")
                    pkgs.add(parts[0] if len(parts) == 1 else ".".join(parts[:2]))
            for pkg in sorted(pkgs):
                mid = _safe_mermaid_id(f"sub_{pkg}")
                lines.append(f'    {mid}["{pkg}"]')
            return "\n".join(lines) + "\n"

        for sub in design.subsystems:
            mid = _safe_mermaid_id(f"sub_{sub.id}")
            lines.append(f'    {mid}["{sub.name} ({sub.id})"]')

        for conv in report.convergences:
            src_id = _safe_mermaid_id(f"sub_{conv.from_subsystem}")
            dst_id = _safe_mermaid_id(f"sub_{conv.to_subsystem}")
            kinds = ", ".join(sorted({ev.kind for ev in conv.evidence}))
            label = f"{kinds} ({len(conv.evidence)})"
            lines.append(f'    {src_id} -->|"{label}"| {dst_id}')
            link_styles.append(f"    linkStyle {edge_idx} stroke:#16a34a,stroke-width:2px")
            edge_idx += 1

        for div in report.divergences:
            src_id = _safe_mermaid_id(f"sub_{div.from_subsystem}")
            dst_id = _safe_mermaid_id(f"sub_{div.to_subsystem}")
            kinds = ", ".join(sorted({ev.kind for ev in div.evidence}))
            label = f"DIVERGENCE: {kinds} ({len(div.evidence)})"
            lines.append(f'    {src_id} ==>|"{label}"| {dst_id}')
            link_styles.append(f"    linkStyle {edge_idx} stroke:#dc2626,stroke-width:3px,color:#dc2626")
            edge_idx += 1

        for ab in report.absences:
            src_id = _safe_mermaid_id(f"sub_{ab.from_subsystem}")
            dst_id = _safe_mermaid_id(f"sub_{ab.to_subsystem}")
            lines.append(f'    {src_id} -.->|"ABSENCE (0)"| {dst_id}')
            link_styles.append(f"    linkStyle {edge_idx} stroke:#94a3b8,stroke-width:1.5px,stroke-dasharray:5 5")
            edge_idx += 1

        lines.extend(link_styles)
        return "\n".join(lines) + "\n"

    # level == "module"
    nodes_by_id: dict[str, ActualNode] = {n.id: n for n in actual.nodes}
    internal_modules = [
        n
        for n in actual.nodes
        if n.kind == "module"
        and not n.external
        and not n.qualified_name.endswith("__init__")
        and not (n.location is not None and n.location.path.endswith("__init__.py"))
    ]
    # Also include __init__ modules only if they are explicitly referenced or if no non-init modules exist
    referenced_mod_ids: set[str] = {m.id for m in internal_modules}
    for edge in actual.edges:
        if edge.kind in ("imports", "calls", "inherits") and edge.confidence == "resolved":
            src_m = _owning_module_id(edge.from_id, nodes_by_id)
            dst_m = _owning_module_id(edge.to_id, nodes_by_id)
            if src_m in nodes_by_id and not nodes_by_id[src_m].external:
                referenced_mod_ids.add(src_m)
            if dst_m in nodes_by_id and not nodes_by_id[dst_m].external:
                referenced_mod_ids.add(dst_m)

    active_modules = [nodes_by_id[mid] for mid in sorted(referenced_mod_ids) if mid in nodes_by_id]

    if design is not None and report is not None:
        sub_to_mods: dict[str, list[ActualNode]] = {s.id: [] for s in design.subsystems}
        unmapped_mods: list[ActualNode] = []
        for mod in active_modules:
            sub_id = report.node_to_subsystem.get(mod.id)
            if sub_id and sub_id in sub_to_mods:
                sub_to_mods[sub_id].append(mod)
            else:
                unmapped_mods.append(mod)

        for sub in design.subsystems:
            mods = sub_to_mods.get(sub.id, [])
            if not mods:
                continue
            sg_id = _safe_mermaid_id(f"sg_{sub.id}")
            lines.append(f'    subgraph {sg_id} ["{sub.name} ({sub.id})"]')
            for m in mods:
                mid = _safe_mermaid_id(m.id)
                lines.append(f'        {mid}["{m.qualified_name}"]')
            lines.append("    end")

        if unmapped_mods:
            lines.append('    subgraph sg_unmapped ["Unmapped"]')
            for m in unmapped_mods:
                mid = _safe_mermaid_id(m.id)
                lines.append(f'        {mid}["{m.qualified_name}"]')
            lines.append("    end")
    else:
        for m in active_modules:
            mid = _safe_mermaid_id(m.id)
            lines.append(f'    {mid}["{m.qualified_name}"]')

    # Aggregate resolved edges between distinct internal modules
    mod_edges: dict[tuple[str, str], set[str]] = {}
    for edge in actual.edges:
        if edge.kind not in ("imports", "calls", "inherits") or edge.confidence != "resolved":
            continue
        src_m = _owning_module_id(edge.from_id, nodes_by_id)
        dst_m = _owning_module_id(edge.to_id, nodes_by_id)
        if src_m == dst_m:
            continue
        if src_m not in referenced_mod_ids or dst_m not in referenced_mod_ids:
            continue
        mod_edges.setdefault((src_m, dst_m), set()).add(edge.kind)

    allowed_sub_pairs: set[tuple[str, str]] = set()
    if design is not None:
        allowed_sub_pairs = {(d.from_subsystem, d.to_subsystem) for d in design.allowed_dependencies}

    for (src_m, dst_m), kinds_set in sorted(mod_edges.items()):
        src_mid = _safe_mermaid_id(src_m)
        dst_mid = _safe_mermaid_id(dst_m)
        kinds_str = ", ".join(sorted(kinds_set))

        is_divergence = False
        if report is not None and design is not None:
            s_sub = report.node_to_subsystem.get(src_m)
            d_sub = report.node_to_subsystem.get(dst_m)
            if s_sub and d_sub and s_sub != d_sub and (s_sub, d_sub) not in allowed_sub_pairs:
                is_divergence = True

        if is_divergence:
            lines.append(f'    {src_mid} ==>|"DIVERGENCE: {kinds_str}"| {dst_mid}')
            link_styles.append(f"    linkStyle {edge_idx} stroke:#dc2626,stroke-width:3px,color:#dc2626")
        else:
            lines.append(f'    {src_mid} -->|"{kinds_str}"| {dst_mid}')
        edge_idx += 1

    lines.extend(link_styles)
    return "\n".join(lines) + "\n"


def format_agent_context(
    actual: ActualMap,
    design: DesignMap | None = None,
    max_chars: int = 4000,
) -> str:
    """Produce a size-bounded progressive-disclosure text summary of the project map for an AI agent."""
    report = check_reflexion(design, actual) if design is not None else None
    proj_title = design.project_name if design else actual.repo_root

    header_lines: list[str] = [
        f"# Method Project Map: {proj_title} (PMF v{actual.pmf_version})",
        "Edges listed are statically proven (resolved); inferred/unresolved ones are omitted.",
    ]
    names = LANGUAGE_NAMES
    if actual.languages and (len(actual.languages) > 1 or "python" not in actual.languages):
        parts = [f"{names.get(k, k)} {v.get('files', 0)} files" + ("" if v.get("mapped") else " (NOT MAPPED)")
                 for k, v in sorted(actual.languages.items(), key=lambda kv: -kv[1].get("files", 0))]
        header_lines.append("Languages: " + ", ".join(parts))
        if any(not v.get("mapped") for v in actual.languages.values()):
            header_lines.append("[NOT MAPPED] languages need their grammars: run `pmap langs install`.")
    unreadable = [e for e in actual.errors if e.kind != "partial_parse"]
    if unreadable:
        header_lines.append(
            f"[NOT MAPPED] {len(unreadable)} unparsable file(s): " + ", ".join(e.path for e in unreadable[:5])
        )
    links = bridge_summary(actual)
    if links:
        # Calls no compiler checks (channel and symbol names); an agent changing one side must change the other.
        header_lines.append("Links between languages (`pmap show <node>` lists both ends):")
        header_lines.extend(line if line.startswith(("[GAP]", "[MISMATCH]")) else f"- {line}" for line in links)
    if design is not None and design.source == "mdm":
        # The mDm scheme is the design: the agent sees which function lives where before reading code.
        header_lines.append("Scheme (mDm, PROJECT.mdm): functions and objects linked to code with `код:`")
        for sub in design.subsystems:
            header_lines.append(f"- {sub.name} ({sub.id}): {', '.join(sub.paths)}")
        header_lines.extend(f"[mDm] {note}" for note in design.notes)
    if report is not None and design is not None and design.subsystems:
        header_lines.append(
            f"Architecture status: {len(report.convergences)} convergences, "
            f"{len(report.divergences)} divergences, {len(report.absences)} absences"
        )
        for div in report.divergences:
            for ev in div.evidence:
                loc = f"{ev.path}:{ev.line}" if ev.path and ev.line else ""
                header_lines.append(
                    f"[DIVERGENCE] {div.from_subsystem} -> {div.to_subsystem} "
                    f"via {loc} ({ev.kind} {ev.from_node} -> {ev.to_node})"
                )

    nodes_by_id = {n.id: n for n in actual.nodes}
    modules = [n for n in actual.nodes if n.kind == "module" and not n.external and n.location is not None]

    # What survives a tight budget: code that many *other packages* depend on comes first
    # (dependants inside the same package say little about a module's importance).
    fan_in: dict[str, set[str]] = {m.id: set() for m in modules}
    pkg_fan_in: dict[str, set[str]] = {m.id: set() for m in modules}
    pkg_of = {m.id: _package_of(m) for m in modules}
    pkg_deps: dict[str, dict[str, int]] = {}
    for e in actual.edges:
        if e.kind not in ("imports", "calls", "inherits") or e.confidence != "resolved":
            continue
        src_m = _owning_module_id(e.from_id, nodes_by_id)
        dst_m = _owning_module_id(e.to_id, nodes_by_id)
        if src_m == dst_m or dst_m not in fan_in or src_m not in fan_in:
            continue
        fan_in[dst_m].add(src_m)
        src_p, dst_p = pkg_of[src_m], pkg_of[dst_m]
        if src_p != dst_p:
            pkg_fan_in[dst_m].add(src_p)
            counts = pkg_deps.setdefault(src_p, {})
            counts[dst_p] = counts.get(dst_p, 0) + 1

    children: dict[str, list[ActualNode]] = {}
    for n in actual.nodes:
        if n.parent_id:
            children.setdefault(n.parent_id, []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: n.location.line_start if n.location else 0)

    packages: dict[str, list[ActualNode]] = {}
    for m in modules:
        packages.setdefault(pkg_of[m.id], []).append(m)
    pkg_rank = {pkg: -sum(len(pkg_fan_in[m.id]) for m in mods) for pkg, mods in packages.items()}
    mod_rank = {m.id: (-len(pkg_fan_in[m.id]), -len(fan_in[m.id]), m.qualified_name) for m in modules}

    def short_pkg(dep: str, pkg: str) -> str:
        root = pkg.split("/", 1)[0]
        return dep.split("/", 1)[1] if dep.startswith(root + "/") else dep

    def package_line(pkg: str) -> str:
        mods = packages[pkg]
        init = next((m for m in mods if m.location.path.endswith("__init__.py")), None)
        doc = f" - {init.docstring_summary}" if init is not None and init.docstring_summary else ""
        deps = sorted(pkg_deps.get(pkg, {}).items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(short_pkg(d, pkg) for d, _ in deps[:6])
        dep_str = (f" | uses: {shown}" + (" ..." if len(deps) > 6 else "")) if deps else ""
        count = sum(1 for m in mods if not _is_bare_init(m, children))
        return f"## {pkg}/{doc} ({count} module{'' if count == 1 else 's'}){dep_str}"

    def module_line(mod: ActualNode) -> str:
        filename = mod.location.path.rsplit("/", 1)[-1]
        sub_tag = f" ({report.node_to_subsystem.get(mod.id, 'unmapped')})" if report else ""
        doc = f" - {mod.docstring_summary}" if mod.docstring_summary else ""
        public = [c.name for c in children.get(mod.id, []) if not c.name.startswith("_")]
        names = ", ".join(public[:4]) + (f" +{len(public) - 4}" if len(public) > 4 else "")
        names_str = f" [{names}]" if names else ""
        return f"- {filename}{sub_tag}{doc}{names_str} ({mod.location.line_end} lines)"

    def symbol_line(sym: ActualNode, indent: str) -> str:
        loc = sym.location
        span = f" [L{loc.line_start}-{loc.line_end}]" if loc else ""
        kind = "def" if sym.kind in ("function", "method") else sym.kind
        return f"{indent}- {kind} {sym.name}{sym.signature or ''}{span}"

    # Progressive disclosure (spec 6B): packages, then modules, then public top-level symbols,
    # then public methods, then private and dunder members; each level in fan-in order.
    items: list[tuple[tuple, str, str, str]] = []  # (priority, key, parent key, text)
    for pkg in packages:
        items.append(((2, pkg_rank[pkg], pkg), f"pkg:{pkg}", "", package_line(pkg)))
    for mod in modules:
        if _is_bare_init(mod, children):
            continue  # an empty __init__ is described by its package line
        items.append(((3, mod_rank[mod.id]), mod.id, f"pkg:{pkg_of[mod.id]}", module_line(mod)))
        for i, sym in enumerate(children.get(mod.id, [])):
            level = 6 if sym.name.startswith("_") else 4
            items.append(((level, mod_rank[mod.id], i), sym.id, mod.id, symbol_line(sym, "  ")))
            for j, meth in enumerate(children.get(sym.id, [])):
                if meth.name == "__init__" or not meth.name.startswith("_"):
                    level = 5
                else:
                    level = 6
                items.append(((level, mod_rank[mod.id], i, j), meth.id, sym.id, symbol_line(meth, "    ")))
    items.sort(key=lambda item: item[0])

    header = "\n".join(header_lines)
    used = len(header) + 160  # room for the footer
    included: set[str] = set()
    for _prio, key, parent, text in items:
        if parent and parent not in included:
            continue
        if used + len(text) + 1 > max_chars:
            continue
        included.add(key)
        used += len(text) + 1

    lines = [header]
    for pkg in sorted(packages):
        if f"pkg:{pkg}" not in included:
            continue
        lines.append(package_line(pkg))
        for mod in sorted(packages[pkg], key=lambda m: m.location.path):
            if mod.id not in included:
                continue
            lines.append(module_line(mod))
            for sym in children.get(mod.id, []):
                if sym.id in included:
                    lines.append(symbol_line(sym, "  "))
                    lines.extend(symbol_line(m, "    ") for m in children.get(sym.id, []) if m.id in included)

    omitted_pkgs = sum(1 for pkg in packages if f"pkg:{pkg}" not in included)
    omitted_mods = sum(1 for _p, key, parent, _t in items if parent.startswith("pkg:") and key not in included)
    omitted_syms = sum(1 for _p, key, parent, _t in items if parent and not parent.startswith("pkg:")
                       and key not in included)
    if omitted_pkgs or omitted_mods or omitted_syms:
        lines.append(
            f"... (omitted at max_chars={max_chars}: {omitted_pkgs} packages, {omitted_mods} modules, "
            f"{omitted_syms} symbols; use `pmap show <file path>` or `pmap find <name>`)"
        )
    return "\n".join(lines) + "\n"


def _package_of(module: ActualNode) -> str:
    path = module.location.path if module.location else module.qualified_name.replace(".", "/") + ".py"
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _is_bare_init(module: ActualNode, children: dict[str, list[ActualNode]]) -> bool:
    return module.location.path.endswith("__init__.py") and not children.get(module.id)
