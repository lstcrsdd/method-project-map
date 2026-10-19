"""Data models for Project Map Format (PMF) v0."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CodeLocation:
    """Source code binding for an actual map node."""
    path: str
    line_start: int
    line_end: int
    file_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "file_hash": self.file_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeLocation:
        return cls(
            path=data["path"],
            line_start=int(data["line_start"]),
            line_end=int(data["line_end"]),
            file_hash=data["file_hash"],
        )


@dataclass(frozen=True)
class EdgeLocation:
    """Source code line where an edge (import/call/inherit) originates."""
    path: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EdgeLocation:
        return cls(path=data["path"], line=int(data["line"]))


@dataclass
class ActualNode:
    """Node in the Actual Map (module, class, function, method)."""
    id: str
    kind: str  # "module" | "class" | "function" | "method"
    name: str
    qualified_name: str
    parent_id: str | None = None
    external: bool = False
    signature: str | None = None
    docstring_summary: str | None = None
    location: CodeLocation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "parent_id": self.parent_id,
            "external": self.external,
            "signature": self.signature,
            "docstring_summary": self.docstring_summary,
            "location": self.location.to_dict() if self.location else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActualNode:
        loc = CodeLocation.from_dict(data["location"]) if data.get("location") else None
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            qualified_name=data["qualified_name"],
            parent_id=data.get("parent_id"),
            external=bool(data.get("external", False)),
            signature=data.get("signature"),
            docstring_summary=data.get("docstring_summary"),
            location=loc,
        )


@dataclass
class ActualEdge:
    """Directed edge in the Actual Map."""
    from_id: str
    to_id: str
    kind: str  # "contains" | "imports" | "calls" | "inherits"
    confidence: str  # "resolved" | "inferred" | "unresolved"
    source: str = "python-ast-v0"
    target_symbol: str | None = None
    location: EdgeLocation | None = None
    candidates: list[str] | None = None  # possible targets of an `inferred` edge
    type_only: bool = False  # import used only for type checking (`if TYPE_CHECKING:`)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "from": self.from_id,
            "to": self.to_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "source": self.source,
            "target_symbol": self.target_symbol,
            "location": self.location.to_dict() if self.location else None,
        }
        if self.candidates is not None:
            res["candidates"] = list(self.candidates)
        if self.type_only:
            res["type_only"] = True
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActualEdge:
        loc = EdgeLocation.from_dict(data["location"]) if data.get("location") else None
        return cls(
            from_id=data["from"],
            to_id=data["to"],
            kind=data["kind"],
            confidence=data["confidence"],
            source=data.get("source", "python-ast-v0"),
            target_symbol=data.get("target_symbol"),
            location=loc,
            candidates=list(data["candidates"]) if data.get("candidates") is not None else None,
            type_only=bool(data.get("type_only", False)),
        )


@dataclass(frozen=True)
class ScanError:
    """A file the scanner could not analyse (it is absent from the map's nodes)."""
    path: str
    kind: str  # "syntax_error" | "decode_error" | "duplicate_module"
    message: str
    line: int | None = None
    file_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "message": self.message,
            "line": self.line,
            "file_hash": self.file_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanError:
        return cls(
            path=data["path"],
            kind=data["kind"],
            message=data.get("message", ""),
            line=data.get("line"),
            file_hash=data.get("file_hash"),
        )


@dataclass
class ActualMap:
    """Complete Actual Map document."""
    pmf_version: str
    generated_at: str
    repo_root: str
    scanner_name: str = "pmap-python-ast"
    scanner_version: str = "0.1.0"
    nodes: list[ActualNode] = field(default_factory=list)
    edges: list[ActualEdge] = field(default_factory=list)
    import_roots: list[str] = field(default_factory=lambda: ["."])
    errors: list[ScanError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pmf_version": self.pmf_version,
            "kind": "actual",
            "generated_at": self.generated_at,
            "repo_root": self.repo_root,
            "scanner": {
                "name": self.scanner_name,
                "version": self.scanner_version,
            },
            "import_roots": list(self.import_roots),
            "errors": [e.to_dict() for e in self.errors],
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActualMap:
        scanner = data.get("scanner", {})
        return cls(
            pmf_version=data.get("pmf_version", "0.1.0"),
            generated_at=data.get("generated_at", ""),
            repo_root=data.get("repo_root", "."),
            scanner_name=scanner.get("name", "pmap-python-ast"),
            scanner_version=scanner.get("version", "0.1.0"),
            nodes=[ActualNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[ActualEdge.from_dict(e) for e in data.get("edges", [])],
            import_roots=list(data.get("import_roots", ["."])),
            errors=[ScanError.from_dict(e) for e in data.get("errors", [])],
        )


@dataclass
class SubsystemSpec:
    """Subsystem definition in a Design Map."""
    id: str
    name: str
    paths: list[str]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "paths": list(self.paths),
        }
        if self.description:
            res["description"] = self.description
        return res


@dataclass
class AllowedDependencySpec:
    """Allowed architectural dependency between two subsystems."""
    from_subsystem: str
    to_subsystem: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "from": self.from_subsystem,
            "to": self.to_subsystem,
        }
        if self.description:
            res["description"] = self.description
        return res


@dataclass
class DesignMap:
    """Complete Design Map document."""
    pmf_version: str
    project_name: str
    project_description: str = ""
    subsystems: list[SubsystemSpec] = field(default_factory=list)
    allowed_dependencies: list[AllowedDependencySpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        proj: dict[str, Any] = {"name": self.project_name}
        if self.project_description:
            proj["description"] = self.project_description
        return {
            "pmf_version": self.pmf_version,
            "kind": "design",
            "project": proj,
            "subsystems": [s.to_dict() for s in self.subsystems],
            "allowed_dependencies": [d.to_dict() for d in self.allowed_dependencies],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignMap:
        proj = data.get("project", {})
        subsystems = [
            SubsystemSpec(
                id=s["id"],
                name=s.get("name", s["id"]),
                paths=list(s.get("paths", [])),
                description=s.get("description", ""),
            )
            for s in data.get("subsystems", [])
        ]
        allowed = [
            AllowedDependencySpec(
                from_subsystem=d["from"],
                to_subsystem=d["to"],
                description=d.get("description", ""),
            )
            for d in data.get("allowed_dependencies", [])
        ]
        return cls(
            pmf_version=str(data.get("pmf_version", "0.1.0")),
            project_name=proj.get("name", "unnamed_project"),
            project_description=proj.get("description", ""),
            subsystems=subsystems,
            allowed_dependencies=allowed,
        )
