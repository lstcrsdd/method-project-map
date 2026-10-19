"""Parser abstraction layer for pmap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class RawImport:
    """Raw import statement extracted from a source file."""
    module: str | None  # e.g. "app.db.connection" or None for "import x" / "from . import x"
    names: list[tuple[str, str | None]]  # list of (imported_name, alias)
    level: int  # 0 for absolute, >=1 for relative imports
    line: int
    is_from: bool
    type_only: bool = False  # inside `if TYPE_CHECKING:`; not a runtime dependency
    scope: str = "module"  # "module" binds names at module level, "local" only inside a function


@dataclass(frozen=True)
class RawCall:
    """Raw function/method call expression inside a scope."""
    callee_expr: str  # e.g. "validate_discount", "self.repo.save_order", "Order", "super.save"
    line: int
    nested: bool = False  # made inside a nested function/lambda/class of this scope


@dataclass
class RawScope:
    """Name facts collected for one scope (module, class body, or function incl. its nested scopes).

    Collection is flow-insensitive: every assignment anywhere in the scope is recorded,
    so the scanner can refuse to resolve names that are bound more than one way.
    """
    local_names: set[str] = field(default_factory=set)
    # name -> one entry per binding: constructor callee expr (`x = Foo()` -> "Foo") or None (unknown value)
    assignments: dict[str, list[str | None]] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)  # name -> declared type expr
    # `self.<attr>` bindings made directly in this method (None = unknown value)
    self_assignments: dict[str, list[str | None]] = field(default_factory=dict)
    self_annotations: dict[str, str] = field(default_factory=dict)
    calls: list[RawCall] = field(default_factory=list)


@dataclass
class RawSymbol:
    """Raw class, function, or method definition inside a module."""
    kind: str  # "class" | "function" | "method"
    name: str
    line_start: int
    line_end: int
    signature: str | None = None
    docstring_summary: str | None = None
    bases: list[tuple[str, int]] = field(default_factory=list)  # (base_expr, line) for classes
    scope: RawScope = field(default_factory=RawScope)
    children: list[RawSymbol] = field(default_factory=list)
    has_self: bool = False  # method whose first parameter is `self`


@dataclass
class ParsedModuleAst:
    """Intermediate representation of a parsed source file, independent of AST implementation."""
    rel_path: str
    module_name: str
    line_start: int
    line_end: int
    file_hash: str
    docstring_summary: str | None = None
    imports: list[RawImport] = field(default_factory=list)
    symbols: list[RawSymbol] = field(default_factory=list)
    scope: RawScope = field(default_factory=RawScope)  # module-level variables and calls


class CodeParser(Protocol):
    """Protocol that any language/backend parser (stdlib ast, tree-sitter, SCIP) must implement."""

    @property
    def analyzer_id(self) -> str:
        """Identifier recorded in edge.source (e.g., 'python-ast-v0')."""
        ...

    def parse_file(self, source_code: str, rel_path: str, module_name: str, file_hash: str) -> ParsedModuleAst:
        """Parse a single source file into a ParsedModuleAst. Raises SyntaxError on invalid source."""
        ...
