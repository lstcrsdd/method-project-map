"""Reflexion Model (Murphy & Notkin, 1995) comparison between DesignMap and ActualMap."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pmap.models import ActualEdge, ActualMap, ActualNode, DesignMap


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a POSIX path glob (supporting `*`, `?`, and `**`) to a compiled regex."""
    norm = pattern.strip().strip("/")
    i = 0
    n = len(norm)
    regex_parts = ["^"]
    while i < n:
        ch = norm[i]
        if ch == "*":
            if i + 1 < n and norm[i + 1] == "*":
                if i + 2 < n and norm[i + 2] == "/":
                    regex_parts.append("(?:.+/)?")
                    i += 3
                    continue
                regex_parts.append(".*")
                i += 2
                continue
            regex_parts.append("[^/]*")
        elif ch == "?":
            regex_parts.append("[^/]")
        else:
            regex_parts.append(re.escape(ch))
        i += 1
    regex_parts.append("$")
    return re.compile("".join(regex_parts))


def _pattern_specificity(pattern: str) -> tuple[int, int]:
    """Rank glob pattern specificity: (number of literal path segments before first wildcard, pattern length)."""
    norm = pattern.strip().strip("/")
    segments = norm.split("/")
    literal_segs = 0
    for seg in segments:
        if "*" in seg or "?" in seg:
            break
        literal_segs += 1
    return (literal_segs, len(norm))


def match_subsystem(rel_path: str, design: DesignMap) -> str | None:
    """Return the subsystem ID matching `rel_path`, choosing the most specific pattern on overlap."""
    norm_path = rel_path.strip().lstrip("/")
    best_sub_id: str | None = None
    best_score: tuple[int, int] = (-1, -1)

    for sub in design.subsystems:
        for pat in sub.paths:
            regex = _glob_to_regex(pat)
            if regex.match(norm_path):
                score = _pattern_specificity(pat)
                if score > best_score:
                    best_score = score
                    best_sub_id = sub.id
    return best_sub_id


@dataclass
class EdgeEvidence:
    """Concrete source-level edge backing a subsystem-to-subsystem relation."""
    from_node: str
    to_node: str
    from_module: str
    to_module: str
    kind: str
    path: str | None
    line: int | None
    target_symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node": self.from_node,
            "to_node": self.to_node,
            "from_module": self.from_module,
            "to_module": self.to_module,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "target_symbol": self.target_symbol,
        }


@dataclass
class SubsystemRelationResult:
    """Comparison result for a directed pair of subsystems (from_subsystem -> to_subsystem)."""
    from_subsystem: str
    to_subsystem: str
    status: str  # "convergence" | "divergence" | "absence"
    evidence: list[EdgeEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_subsystem,
            "to": self.to_subsystem,
            "status": self.status,
            "evidence_count": len(self.evidence),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class ReflexionReport:
    """Full Reflexion Model report comparing a DesignMap and an ActualMap."""
    project_name: str
    convergences: list[SubsystemRelationResult] = field(default_factory=list)
    divergences: list[SubsystemRelationResult] = field(default_factory=list)
    absences: list[SubsystemRelationResult] = field(default_factory=list)
    unmapped_modules: list[str] = field(default_factory=list)
    node_to_subsystem: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Readable names next to ids in the text report; filled for designs whose ids say little (mDm).
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        """True if there are no architectural divergences."""
        return len(self.divergences) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project_name,
            "summary": {
                "convergences": len(self.convergences),
                "divergences": len(self.divergences),
                "absences": len(self.absences),
                "unmapped_modules": len(self.unmapped_modules),
                "is_clean": self.is_clean,
            },
            "warnings": list(self.warnings),
            "convergences": [c.to_dict() for c in self.convergences],
            "divergences": [d.to_dict() for d in self.divergences],
            "absences": [a.to_dict() for a in self.absences],
            "unmapped_modules": list(self.unmapped_modules),
            **({"labels": dict(self.labels)} if self.labels else {}),
        }


def _owning_module_id(node_id: str, nodes_by_id: dict[str, ActualNode]) -> str:
    """Find the module node ID (`mod:...`) that owns `node_id`."""
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


def check_reflexion(design: DesignMap, actual: ActualMap, include_type_only: bool = False) -> ReflexionReport:
    """Run the Murphy & Notkin Reflexion Model comparison.

    Only `resolved` edges are lifted to subsystems. Imports used purely for type checking are
    skipped unless `include_type_only` is set. Everything left out is counted in `warnings`.
    """
    nodes_by_id: dict[str, ActualNode] = {n.id: n for n in actual.nodes}
    node_to_subsystem: dict[str, str] = {}
    unmapped_modules: list[str] = []

    for node in actual.nodes:
        if node.external or not node.location:
            continue
        sub_id = match_subsystem(node.location.path, design)
        if sub_id is not None:
            node_to_subsystem[node.id] = sub_id
        elif node.kind == "module":
            unmapped_modules.append(node.id)

    allowed_pairs: set[tuple[str, str]] = {
        (dep.from_subsystem, dep.to_subsystem) for dep in design.allowed_dependencies
    }

    actual_pairs: dict[tuple[str, str], list[EdgeEvidence]] = {}
    uncertain_edges = 0
    type_only_edges = 0
    unresolved_imports = 0

    for edge in actual.edges:
        if edge.kind not in ("imports", "calls", "inherits"):
            continue
        if edge.confidence != "resolved":
            uncertain_edges += 1
            if edge.kind == "imports":
                unresolved_imports += 1
            continue
        if edge.type_only and not include_type_only:
            type_only_edges += 1
            continue

        src_sub = node_to_subsystem.get(edge.from_id)
        dst_sub = node_to_subsystem.get(edge.to_id)
        if not src_sub or not dst_sub or src_sub == dst_sub:
            continue

        pair = (src_sub, dst_sub)
        actual_pairs.setdefault(pair, []).append(
            EdgeEvidence(
                from_node=edge.from_id,
                to_node=edge.to_id,
                from_module=_owning_module_id(edge.from_id, nodes_by_id),
                to_module=_owning_module_id(edge.to_id, nodes_by_id),
                kind=edge.kind,
                path=edge.location.path if edge.location else None,
                line=edge.location.line if edge.location else None,
                target_symbol=edge.target_symbol,
            )
        )

    convergences: list[SubsystemRelationResult] = []
    divergences: list[SubsystemRelationResult] = []
    absences: list[SubsystemRelationResult] = []

    # Check all allowed dependencies first (in design order)
    for dep in design.allowed_dependencies:
        pair = (dep.from_subsystem, dep.to_subsystem)
        if pair in actual_pairs:
            convergences.append(
                SubsystemRelationResult(
                    from_subsystem=pair[0],
                    to_subsystem=pair[1],
                    status="convergence",
                    evidence=actual_pairs[pair],
                )
            )
        elif dep.required:
            absences.append(
                SubsystemRelationResult(
                    from_subsystem=pair[0],
                    to_subsystem=pair[1],
                    status="absence",
                    evidence=[],
                )
            )

    # Check actual dependencies that are not allowed -> divergences
    for pair in sorted(actual_pairs.keys()):
        if pair not in allowed_pairs:
            divergences.append(
                SubsystemRelationResult(
                    from_subsystem=pair[0],
                    to_subsystem=pair[1],
                    status="divergence",
                    evidence=actual_pairs[pair],
                )
            )

    warnings: list[str] = list(design.notes)
    if actual.errors:
        warnings.append(
            f"{len(actual.errors)} file(s) could not be analysed and are absent from the check: "
            + ", ".join(e.path for e in actual.errors[:5])
            + (" ..." if len(actual.errors) > 5 else "")
        )
    if unresolved_imports:
        warnings.append(
            f"{unresolved_imports} import(s) could not be resolved; dependencies through them are not checked "
            "(missing package, wrong import root?)"
        )
    if uncertain_edges - unresolved_imports > 0:
        warnings.append(
            f"{uncertain_edges - unresolved_imports} inferred/unresolved call or inheritance edge(s) were not counted; "
            "the report covers statically proven dependencies only"
        )
    if type_only_edges:
        warnings.append(f"{type_only_edges} type-checking-only import(s) skipped (use --include-type-only to count them)")

    return ReflexionReport(
        project_name=design.project_name,
        convergences=convergences,
        divergences=divergences,
        absences=absences,
        unmapped_modules=sorted(unmapped_modules),
        node_to_subsystem=node_to_subsystem,
        warnings=warnings,
        labels={s.id: s.name for s in design.subsystems} if design.source == "mdm" else {},
    )


def format_reflexion_text(report: ReflexionReport) -> str:
    """Format a human- and agent-readable summary of the ReflexionReport."""

    def named(rel: SubsystemRelationResult) -> str:
        if not report.labels:
            return ""
        return f" [{report.labels.get(rel.from_subsystem, rel.from_subsystem)} -> " \
               f"{report.labels.get(rel.to_subsystem, rel.to_subsystem)}]"

    lines: list[str] = [
        f"Reflexion Report for project '{report.project_name}'",
        "=" * 54,
        f"Convergences (Design == Code): {len(report.convergences)}",
        f"Divergences  (Code > Design) : {len(report.divergences)}",
        f"Absences     (Design > Code) : {len(report.absences)}",
    ]
    if report.unmapped_modules:
        lines.append(f"Unmapped modules             : {len(report.unmapped_modules)}")
    lines.append("")

    for warning in report.warnings:
        lines.append(f"[warning] {warning}")
    if report.warnings:
        lines.append("")

    if report.divergences:
        lines.append("[!] DIVERGENCES (Architectural Violations):")
        for div in report.divergences:
            lines.append(f"  ✗ {div.from_subsystem} -> {div.to_subsystem}{named(div)} ({len(div.evidence)} violation(s))")
            for ev in div.evidence:
                loc_str = f"{ev.path}:{ev.line}" if ev.path and ev.line else (ev.path or "unknown")
                lines.append(f"      - [{ev.kind}] {ev.from_node} -> {ev.to_node} at {loc_str}")
        lines.append("")
    else:
        lines.append("[OK] No architectural divergences detected.")
        lines.append("")

    if report.convergences:
        lines.append("[✓] CONVERGENCES (Matched Dependencies):")
        for conv in report.convergences:
            kinds = sorted({ev.kind for ev in conv.evidence})
            lines.append(
                f"  ✓ {conv.from_subsystem} -> {conv.to_subsystem} "
                f"({len(conv.evidence)} edge(s): {', '.join(kinds)})"
            )
        lines.append("")

    if report.absences:
        lines.append("[-] ABSENCES (Allowed in Design, Unused in Code):")
        for ab in report.absences:
            lines.append(f"  - {ab.from_subsystem} -.-> {ab.to_subsystem}{named(ab)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
