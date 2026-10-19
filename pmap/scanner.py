"""Static scanner that builds an ActualMap from Python files (other languages: pmap.langs).

Confidence rules (spec/PMF-v0.md, section 2):
  resolved   — exactly one possible target, proven from the analysed code alone;
  inferred   — the target comes from a type annotation, there are several candidate targets,
               or the class hierarchy may route the call elsewhere (overrides, external bases);
  unresolved — the reference exists, but its target cannot be determined statically.

Name analysis is flow-insensitive: every binding of a name anywhere in its scope is taken
into account, so a name bound two different ways is never reported as resolved.
"""

from __future__ import annotations

import builtins
import fnmatch
import glob
import hashlib
import importlib.machinery
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from pmap import PMF_VERSION, __version__
from pmap.models import ActualEdge, ActualMap, ActualNode, CodeLocation, EdgeLocation, ScanError
from pmap.parser import CodeParser, ParsedModuleAst, RawCall, RawImport, RawScope, RawSymbol
from pmap.parser.python_ast import PythonAstParser

_IGNORED_DIRS = {
    ".git",
    ".pmap",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".playwright-cli",
    ".aistudio",
}

_BUILTIN_NAMES = set(dir(builtins))

# Annotated types whose methods are not part of the project's dependency graph.
_UNTRACKED_TYPES = {
    "str", "int", "float", "bool", "bytes", "bytearray", "complex", "dict", "list", "set", "tuple",
    "frozenset", "None", "type", "List", "Dict", "Set", "Tuple", "FrozenSet", "Sequence",
    "MutableSequence", "Mapping", "MutableMapping", "Iterable", "Iterator", "Generator",
    "AsyncIterator", "AsyncIterable", "AsyncGenerator", "Callable", "Type", "Awaitable", "Coroutine",
    "Collection", "AbstractSet", "DefaultDict", "OrderedDict", "Counter", "Deque", "Literal",
    "ClassVar", "Final",
}
# Annotations that say nothing about the value's type.
_OPAQUE_TYPES = {"Any", "object"}
# Bases that contribute no methods a project calls; they keep a class hierarchy "closed".
_NEUTRAL_BASES = {"object", "ABC", "abc.ABC", "Protocol", "typing.Protocol", "Generic", "typing.Generic"}


def compute_file_hash(file_path: Path) -> str:
    """Compute sha256:<hex> hash of a file's raw bytes."""
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _normalize_type_name(expr: str) -> str:
    for prefix in ("typing.", "typing_extensions.", "collections.abc.", "collections."):
        if expr.startswith(prefix):
            return expr[len(prefix):]
    return expr


def detect_import_roots(repo_root: Path, explicit_roots: list[str] | None = None) -> list[str]:
    """Return import roots relative to `repo_root` (POSIX), most specific first, always ending with ".".

    Without explicit roots, a `src/` directory that is not itself a package is treated as a root
    (the common "src layout").
    """
    roots: list[str] = []
    for raw in explicit_roots or []:
        norm = Path(raw).as_posix().strip("/")
        if norm in ("", "."):
            continue
        if norm.startswith("./"):
            norm = norm[2:]
        if not (repo_root / norm).is_dir():
            raise FileNotFoundError(f"Import root does not exist: {repo_root / norm}")
        if norm not in roots:
            roots.append(norm)
    if not explicit_roots:
        src = repo_root / "src"
        if src.is_dir() and not (src / "__init__.py").exists():
            roots.append("src")
    roots.sort(key=lambda r: -len(r.split("/")))
    roots.append(".")
    return roots


def _rel_path_to_module_name(rel_posix: str) -> str:
    """Convert a POSIX path relative to an import root to a dotted module name."""
    if rel_posix.endswith(".py"):
        rel_posix = rel_posix[:-3]
    parts = [p for p in rel_posix.split("/") if p]
    if len(parts) > 1 and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _module_name_for(rel_posix: str, roots: list[str]) -> str:
    for root in roots:
        if root == ".":
            return _rel_path_to_module_name(rel_posix)
        if rel_posix.startswith(root + "/"):
            return _rel_path_to_module_name(rel_posix[len(root) + 1:])
    return _rel_path_to_module_name(rel_posix)


def load_ignore_patterns(repo_root: Path) -> list[str]:
    """Patterns from `.pmapignore` (one glob per line, `#` comments; `dir/` ignores a whole folder)."""
    path = repo_root / ".pmapignore"
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def is_ignored(rel_posix: str, patterns: list[str]) -> bool:
    parts = rel_posix.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts))]
    for pattern in patterns:
        if pattern.endswith("/"):
            folder = pattern.rstrip("/")
            if any(fnmatch.fnmatchcase(prefix, folder) for prefix in prefixes):
                return True
        elif fnmatch.fnmatchcase(rel_posix, pattern):
            return True
    return False


def discover_python_files(repo_root: Path, roots: list[str] | None = None) -> list[tuple[Path, str, str]]:
    """Discover all .py files in repo_root, returning sorted list of (abs_path, rel_posix, module_name)."""
    roots = roots or ["."]
    ignore = load_ignore_patterns(repo_root)
    results: list[tuple[Path, str, str]] = []
    # os.walk prunes ignored folders instead of descending into them (node_modules can hold 100k files)
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # macOS stores resource-fork metadata in binary AppleDouble sidecars.
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith("._")
                       and not d.startswith(".pmap")]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("._"):
                continue
            path = Path(dirpath) / name
            rel_posix = path.relative_to(repo_root).as_posix()
            if ignore and is_ignored(rel_posix, ignore):
                continue
            results.append((path, rel_posix, _module_name_for(rel_posix, roots)))
    results.sort(key=lambda item: item[1])
    return results


def _resolve_relative_module(current_module: str, is_package_init: bool, level: int, target_module: str | None) -> str:
    """Resolve relative import module name against current module."""
    if level == 0:
        return target_module or ""
    parts = current_module.split(".")
    base_parts = parts if is_package_init else parts[:-1]
    up = level - 1
    if up > 0:
        base_parts = base_parts[:-up] if up <= len(base_parts) else []
    if target_module:
        return ".".join(base_parts + [target_module]) if base_parts else target_module
    return ".".join(base_parts)


class ExternalModuleResolver:
    """Decides whether a top-level module name is a known external (stdlib or installed) module.

    The scanned repository and the current directory are excluded from the search path, so a
    project directory is never mistaken for an installed package. A virtualenv inside the
    repository (.venv/venv/env) is searched as well.
    """

    def __init__(self, repo_root: Path) -> None:
        repo = os.path.abspath(repo_root)
        cwd = os.getcwd()

        def is_project_dir(p: str) -> bool:
            ap = os.path.abspath(p)
            return ap in (cwd, repo) or ap.startswith(repo + os.sep)

        self._search_path = [p for p in sys.path if p and not is_project_dir(p)]
        for venv in (".venv", "venv", "env"):
            pattern_unix = os.path.join(repo, venv, "lib", "python*", "site-packages")
            pattern_win = os.path.join(repo, venv, "Lib", "site-packages")
            self._search_path.extend(sorted(glob.glob(pattern_unix)) + sorted(glob.glob(pattern_win)))
        self._cache: dict[str, bool] = {}

    def is_external(self, top: str) -> bool:
        if top not in self._cache:
            known = top in sys.builtin_module_names or top in getattr(sys, "stdlib_module_names", ())
            if not known:
                try:
                    known = importlib.machinery.PathFinder.find_spec(top, self._search_path) is not None
                except (ImportError, ValueError):
                    known = False
            self._cache[top] = known
        return self._cache[top]


@dataclass(frozen=True)
class _Ent:
    """What an expression may evaluate to."""
    kind: str  # "sym" | "mod" | "inst" | "ext" | "skip" | "unknown"
    ref: str = ""  # symbol id, dotted module name, or class id for "inst"
    certain: bool = True
    exact: bool = True  # "inst" only: runtime class is exactly `ref`, not a subclass


_UNKNOWN = _Ent("unknown")
_SKIP = _Ent("skip")  # value of an external/builtin type: not tracked
_EXT = _Ent("ext")


@dataclass(frozen=True)
class _Ctx:
    module: str
    scope: RawScope | None  # None: module scope
    class_id: str | None = None
    has_self: bool = False
    nested: bool = False


def _dedupe(ents: list[_Ent]) -> list[_Ent]:
    return list(dict.fromkeys(ents))


class ProjectScanner:
    """Scans a Python repository into a PMF v0 ActualMap using an isolated CodeParser."""

    def __init__(self, parser: CodeParser | None = None, import_roots: list[str] | None = None) -> None:
        self.parser: CodeParser = parser or PythonAstParser()
        self.explicit_roots = import_roots

    # ------------------------------------------------------------------ scanning

    def scan_repository(self, repo_root: str | Path, generated_at: str | None = None) -> ActualMap:
        root_path = Path(repo_root).resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(f"Repository directory not found: {repo_root}")

        timestamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        roots = detect_import_roots(root_path, self.explicit_roots)
        self._external = ExternalModuleResolver(root_path)
        self.modules: dict[str, ParsedModuleAst] = {}
        errors: list[ScanError] = []

        for abs_path, rel_posix, mod_name in discover_python_files(root_path, roots):
            fhash = compute_file_hash(abs_path)
            if mod_name in self.modules:
                errors.append(
                    ScanError(
                        path=rel_posix,
                        kind="duplicate_module",
                        message=f"module {mod_name!r} already defined by {self.modules[mod_name].rel_path}",
                        file_hash=fhash,
                    )
                )
                continue
            try:
                source_code = abs_path.read_text(encoding="utf-8")
                parsed = self.parser.parse_file(source_code, rel_posix, mod_name, fhash)
            except UnicodeDecodeError as exc:
                errors.append(ScanError(path=rel_posix, kind="decode_error", message=str(exc), file_hash=fhash))
                continue
            except (SyntaxError, ValueError, RecursionError) as exc:
                errors.append(
                    ScanError(
                        path=rel_posix,
                        kind="syntax_error",
                        message=f"{type(exc).__name__}: {getattr(exc, 'msg', None) or exc}",
                        line=getattr(exc, "lineno", None),
                        file_hash=fhash,
                    )
                )
                continue
            self.modules[mod_name] = parsed

        self._package_prefixes = {
            ".".join(name.split(".")[:i]) for name in self.modules for i in range(1, name.count(".") + 1)
        }

        nodes_by_id: dict[str, ActualNode] = {}
        self._edges: list[ActualEdge] = []
        self._edge_keys: set[tuple] = set()
        self.symbol_kind: dict[str, str] = {}
        self.class_methods: dict[str, dict[str, list[str]]] = {}
        self.class_attr_values: dict[str, dict[str, list[tuple[str, str | None]]]] = {}
        self.class_attr_annotations: dict[str, dict[str, list[tuple[str, str]]]] = {}
        self.class_parents: dict[str, list[str]] = {}
        self.class_foreign: dict[str, bool] = {}
        self.class_children: dict[str, list[str]] = {}
        self.bindings: dict[str, dict[str, list[tuple[str, object]]]] = {}
        symbol_ids: dict[tuple[str, int], str] = {}  # (module, id(RawSymbol)) -> node id

        # PASS 1: nodes and `contains` edges
        for mod_name, parsed in self.modules.items():
            mod_id = f"mod:{mod_name}"
            nodes_by_id[mod_id] = self._make_node(mod_id, "module", mod_name.split(".")[-1], mod_name, None,
                                                  parsed.rel_path, parsed.line_start, parsed.line_end,
                                                  parsed.file_hash, None, parsed.docstring_summary)
            taken: dict[str, int] = {}
            for sym in parsed.symbols:
                sym_id = self._unique_id(f"sym:{mod_name}:{sym.name}", taken)
                symbol_ids[(mod_name, id(sym))] = sym_id
                nodes_by_id[sym_id] = self._make_node(sym_id, sym.kind, sym.name, f"{mod_name}.{sym.name}", mod_id,
                                                      parsed.rel_path, sym.line_start, sym.line_end,
                                                      parsed.file_hash, sym.signature, sym.docstring_summary)
                self.symbol_kind[sym_id] = sym.kind
                self._add_edge(mod_id, sym_id, "contains", "resolved", parsed.rel_path, sym.line_start)
                if sym.kind != "class":
                    continue
                methods: dict[str, list[str]] = {}
                attr_values: dict[str, list[tuple[str, str | None]]] = {}
                attr_anns: dict[str, list[tuple[str, str]]] = {}
                # Class-body assignments are class attributes, visible through instances.
                for attr, values in sym.scope.assignments.items():
                    attr_values.setdefault(attr, []).extend((mod_name, v) for v in values)
                for attr, ann in sym.scope.annotations.items():
                    attr_anns.setdefault(attr, []).append((mod_name, ann))
                method_taken: dict[str, int] = {}
                for child in sym.children:
                    meth_id = self._unique_id(f"{sym_id}.{child.name}", method_taken)
                    symbol_ids[(mod_name, id(child))] = meth_id
                    nodes_by_id[meth_id] = self._make_node(meth_id, "method", child.name,
                                                           f"{mod_name}.{sym.name}.{child.name}", sym_id,
                                                           parsed.rel_path, child.line_start, child.line_end,
                                                           parsed.file_hash, child.signature, child.docstring_summary)
                    self.symbol_kind[meth_id] = "method"
                    methods.setdefault(child.name, []).append(meth_id)
                    self._add_edge(sym_id, meth_id, "contains", "resolved", parsed.rel_path, child.line_start)
                    for attr, values in child.scope.self_assignments.items():
                        attr_values.setdefault(attr, []).extend((mod_name, v) for v in values)
                    for attr, ann in child.scope.self_annotations.items():
                        attr_anns.setdefault(attr, []).append((mod_name, ann))
                self.class_methods[sym_id] = methods
                self.class_attr_values[sym_id] = attr_values
                self.class_attr_annotations[sym_id] = attr_anns

        # PASS 2: module bindings and `imports` edges
        for mod_name, parsed in self.modules.items():
            bindings: dict[str, list[tuple[str, object]]] = {}
            self.bindings[mod_name] = bindings
            for sym in parsed.symbols:
                self._bind(bindings, sym.name, ("sym", symbol_ids[(mod_name, id(sym))]))
            for name, values in parsed.scope.assignments.items():
                for value in values:
                    self._bind(bindings, name, ("var", value))
            for imp in parsed.imports:
                self._process_import(mod_name, parsed, imp, bindings, nodes_by_id)

        # PASS 3: class hierarchy (`inherits` edges)
        for mod_name, parsed in self.modules.items():
            ctx = _Ctx(module=mod_name, scope=None)
            for sym in parsed.symbols:
                if sym.kind == "class":
                    self._process_bases(symbol_ids[(mod_name, id(sym))], sym, ctx, parsed.rel_path)
        for cls_id, parents in self.class_parents.items():
            for parent in parents:
                self.class_children.setdefault(parent, []).append(cls_id)

        # PASS 4: calls
        for mod_name, parsed in self.modules.items():
            mod_ctx = _Ctx(module=mod_name, scope=None)
            for call in parsed.scope.calls:
                self._process_call(f"mod:{mod_name}", replace(mod_ctx, nested=call.nested), call, parsed.rel_path)
            for sym in parsed.symbols:
                sym_id = symbol_ids[(mod_name, id(sym))]
                if sym.kind == "function":
                    ctx = _Ctx(module=mod_name, scope=sym.scope)
                    for call in sym.scope.calls:
                        self._process_call(sym_id, replace(ctx, nested=call.nested), call, parsed.rel_path)
                    continue
                body_ctx = _Ctx(module=mod_name, scope=sym.scope)
                for call in sym.scope.calls:
                    self._process_call(sym_id, replace(body_ctx, nested=call.nested), call, parsed.rel_path)
                for child in sym.children:
                    meth_id = symbol_ids[(mod_name, id(child))]
                    ctx = _Ctx(module=mod_name, scope=child.scope, class_id=sym_id, has_self=child.has_self)
                    for call in child.scope.calls:
                        self._process_call(meth_id, replace(ctx, nested=call.nested), call, parsed.rel_path)

        # TypeScript / JavaScript, Go, Java, Kotlin, C# (tree-sitter, optional); one project may mix them with Python
        from pmap import langs

        others = langs.scan(root_path, load_ignore_patterns(root_path))
        for node in others.nodes:
            nodes_by_id.setdefault(node.id, node)
        languages = {}
        python_files = len(self.modules) + sum(1 for e in errors if e.path.endswith(".py"))
        if python_files:
            languages["python"] = {"files": python_files, "mapped": True}
        languages.update(others.languages)

        sorted_nodes = sorted(nodes_by_id.values(), key=lambda n: (n.external, n.id))
        return ActualMap(
            pmf_version=PMF_VERSION,
            generated_at=timestamp,
            repo_root=root_path.name,
            scanner_name="pmap",
            scanner_version=__version__,
            nodes=sorted_nodes,
            edges=self._edges + others.edges,
            import_roots=roots,
            errors=errors + others.errors,
            languages=languages,
        )

    @staticmethod
    def _unique_id(base_id: str, taken: dict[str, int]) -> str:
        """Same-named definitions in one scope get `#2`, `#3`, ... suffixes."""
        taken[base_id] = taken.get(base_id, 0) + 1
        return base_id if taken[base_id] == 1 else f"{base_id}#{taken[base_id]}"

    @staticmethod
    def _make_node(node_id: str, kind: str, name: str, qualified: str, parent: str | None, path: str,
                   line_start: int, line_end: int, fhash: str, signature: str | None,
                   doc: str | None) -> ActualNode:
        return ActualNode(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            parent_id=parent,
            external=False,
            signature=signature,
            docstring_summary=doc,
            location=CodeLocation(path=path, line_start=line_start, line_end=line_end, file_hash=fhash),
        )

    @staticmethod
    def _bind(bindings: dict[str, list[tuple[str, object]]], name: str, binding: tuple[str, object]) -> None:
        entries = bindings.setdefault(name, [])
        if binding not in entries:
            entries.append(binding)

    def _add_edge(self, from_id: str, to_id: str, kind: str, confidence: str, path: str, line: int,
                  target_symbol: str | None = None, candidates: list[str] | None = None,
                  type_only: bool = False) -> None:
        key = (from_id, to_id, kind, line, type_only)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._edges.append(
            ActualEdge(
                from_id=from_id,
                to_id=to_id,
                kind=kind,
                confidence=confidence,
                source=self.parser.analyzer_id,
                target_symbol=target_symbol,
                location=EdgeLocation(path=path, line=line),
                candidates=candidates,
                type_only=type_only,
            )
        )

    # ------------------------------------------------------------------ imports

    def _is_namespace(self, name: str) -> bool:
        return name not in self.modules and name in self._package_prefixes

    def _is_internal(self, name: str) -> bool:
        return name in self.modules or name in self._package_prefixes

    def _ensure_external_node(self, top_pkg: str, nodes_by_id: dict[str, ActualNode]) -> str:
        ext_id = f"ext:{top_pkg}"
        if ext_id not in nodes_by_id:
            nodes_by_id[ext_id] = ActualNode(
                id=ext_id,
                kind="module",
                name=top_pkg,
                qualified_name=top_pkg,
                parent_id=None,
                external=True,
            )
        return ext_id

    def _process_import(
        self,
        mod_name: str,
        parsed: ParsedModuleAst,
        imp: RawImport,
        bindings: dict[str, list[tuple[str, object]]],
        nodes_by_id: dict[str, ActualNode],
    ) -> None:
        mod_id = f"mod:{mod_name}"

        def edge(to_id: str, confidence: str, target: str) -> None:
            self._add_edge(mod_id, to_id, "imports", confidence, parsed.rel_path, imp.line,
                           target_symbol=target, type_only=imp.type_only)

        def bind(name: str, binding: tuple[str, object]) -> None:
            # Imports inside functions do not bind module-level names.
            if imp.scope == "module":
                self._bind(bindings, name, binding)

        def import_module(dotted: str) -> tuple[str, object]:
            """Emit the edge for importing module `dotted`; return what the module object is."""
            if dotted in self.modules:
                edge(f"mod:{dotted}", "resolved", dotted)
                return ("mod", dotted)
            if self._is_namespace(dotted):
                return ("mod", dotted)  # namespace package: no file, no node
            top = dotted.split(".")[0]
            if not self._is_internal(top) and self._external.is_external(top):
                edge(self._ensure_external_node(top, nodes_by_id), "resolved", dotted)
                return ("ext", None)
            edge(f"unresolved:import:{dotted}", "unresolved", dotted)
            return ("unknown", None)

        if not imp.is_from:
            for raw_name, asname in imp.names:
                target = import_module(raw_name)
                if asname:
                    bind(asname, target)
                else:
                    head = raw_name.split(".")[0]
                    if target[0] == "mod":
                        bind(head, ("mod", head))
                    else:
                        bind(head, target)
            return

        base = _resolve_relative_module(mod_name, parsed.rel_path.endswith("__init__.py"), imp.level, imp.module)
        for imported_name, asname in imp.names:
            local_alias = asname or imported_name
            full = f"{base}.{imported_name}" if base else imported_name
            if imported_name == "*":
                if imp.level > 0 and not self._is_internal(base):
                    edge(f"unresolved:import:{base}", "unresolved", f"{base}.*")
                else:
                    import_module(base)
                continue
            if full in self.modules or self._is_namespace(full):
                bind(local_alias, import_module(full))
            elif base in self.modules:
                edge(f"mod:{base}", "resolved", full)
                bind(local_alias, ("attr", (base, imported_name)))
            elif imp.level > 0 or self._is_internal(base.split(".")[0]):
                # Relative imports and imports from project packages must resolve inside the project.
                edge(f"unresolved:import:{full}", "unresolved", full)
                bind(local_alias, ("unknown", None))
            else:
                bind(local_alias, import_module(base) if base else import_module(imported_name))

    # ------------------------------------------------------------------ class hierarchy

    def _process_bases(self, cls_id: str, sym: RawSymbol, ctx: _Ctx, rel_path: str) -> None:
        parents: list[str] = []
        foreign = False
        for base_expr, line in sym.bases:
            if base_expr in _NEUTRAL_BASES:
                continue
            if base_expr == "<expr>":
                foreign = True
                self._add_edge(cls_id, "unresolved:<expr>", "inherits", "unresolved", rel_path, line, base_expr)
                continue
            ents = _dedupe(self._eval_expr(base_expr, ctx, frozenset()))
            if all(e.kind in ("ext", "skip") for e in ents):
                foreign = True  # external base class: its methods are invisible to us
                continue
            classes = [e for e in ents if e.kind == "sym" and self.symbol_kind.get(e.ref) == "class"]
            if len(classes) != len(ents):
                foreign = True
                self._add_edge(cls_id, f"unresolved:{base_expr}", "inherits", "unresolved", rel_path, line, base_expr)
            elif len(classes) == 1 and classes[0].certain:
                parents.append(classes[0].ref)
                self._add_edge(cls_id, classes[0].ref, "inherits", "resolved", rel_path, line, base_expr)
            else:
                foreign = True
                refs = [e.ref for e in classes]
                to_id = refs[0] if len(refs) == 1 else f"ambiguous:{base_expr}"
                self._add_edge(cls_id, to_id, "inherits", "inferred", rel_path, line, base_expr, candidates=refs)
        self.class_parents[cls_id] = parents
        self.class_foreign[cls_id] = foreign

    def _mro(self, cls_id: str) -> list[str]:
        """Left-to-right depth-first linearisation of the internal class hierarchy."""
        order: list[str] = []
        stack = [cls_id]
        while stack:
            current = stack.pop(0)
            if current in order:
                continue
            order.append(current)
            stack[0:0] = self.class_parents.get(current, [])
        return order

    def _descendants(self, cls_id: str) -> list[str]:
        result: list[str] = []
        queue = list(self.class_children.get(cls_id, []))
        while queue:
            current = queue.pop(0)
            if current in result:
                continue
            result.append(current)
            queue.extend(self.class_children.get(current, []))
        return result

    def _mro_lookup(self, cls_id: str, name: str) -> tuple[list[str], bool]:
        """Find method `name`; the flag is False if an external base could supply it first."""
        complete = True
        for current in self._mro(cls_id):
            found = self.class_methods.get(current, {}).get(name)
            if found:
                return found, complete
            if self.class_foreign.get(current):
                complete = False
        return [], complete

    def _hierarchy_has_foreign_base(self, cls_id: str) -> bool:
        return any(self.class_foreign.get(c) for c in self._mro(cls_id))

    # ------------------------------------------------------------------ evaluation

    def _module_ctx(self, module: str) -> _Ctx:
        return _Ctx(module=module, scope=None)

    def _eval_expr(self, expr: str, ctx: _Ctx, guard: frozenset) -> list[_Ent]:
        parts = expr.split(".")
        if parts[0] == "super" and len(parts) == 2:
            return self._super_method(parts[1], ctx)
        ents = self._eval_name(parts[0], ctx, guard)
        for attr in parts[1:]:
            ents = _dedupe([m for e in ents for m in self._member(e, attr, guard)])
        return ents

    def _eval_name(self, name: str, ctx: _Ctx, guard: frozenset) -> list[_Ent]:
        scope = ctx.scope
        if scope is not None:
            if name == "self" and ctx.has_self and not ctx.nested and ctx.class_id:
                return [_Ent("inst", ctx.class_id, certain=True, exact=False)]
            if name in scope.local_names:
                key = ("local", id(scope), name)
                if key in guard:
                    return [_UNKNOWN]
                values = scope.assignments.get(name) or [None]
                return self._values_to_ents(values, scope.annotations.get(name), ctx, guard | {key})
        return self._eval_module_name(ctx.module, name, guard)

    def _eval_module_name(self, module: str, name: str, guard: frozenset) -> list[_Ent]:
        entries = self.bindings.get(module, {}).get(name)
        if not entries:
            return [_SKIP] if name in _BUILTIN_NAMES else [_UNKNOWN]
        mctx = self._module_ctx(module)
        out: list[_Ent] = []
        for kind, payload in entries:
            if kind == "sym":
                out.append(_Ent("sym", str(payload)))
            elif kind == "mod":
                out.append(_Ent("mod", str(payload)))
            elif kind == "ext":
                out.append(_EXT)
            elif kind == "attr":
                base, attr = payload  # type: ignore[misc]
                out.extend(self._member_of_module(base, attr, guard))
            elif kind == "var":
                key = ("var", module, name)
                if key in guard:
                    out.append(_UNKNOWN)
                    continue
                ann = self.modules[module].scope.annotations.get(name)
                out.extend(self._values_to_ents([payload], ann, mctx, guard | {key}))  # type: ignore[list-item]
            else:
                out.append(_UNKNOWN)
        return out or [_UNKNOWN]

    def _values_to_ents(self, values: list[str | None], ann: str | None, ctx: _Ctx, guard: frozenset) -> list[_Ent]:
        """Evaluate the bindings of one name: constructor calls give instances, anything else is unknown."""
        ents: list[_Ent] = []
        unknown = False
        for callee in values:
            if callee is None:
                unknown = True
            else:
                ents.extend(self._instances_of(self._eval_expr(callee, ctx, guard)))
        if unknown:
            ents.extend(self._eval_annotation(ann, ctx, guard) if ann else [_UNKNOWN])
        return ents or [_UNKNOWN]

    def _instances_of(self, callee_ents: list[_Ent]) -> list[_Ent]:
        """What calling each entity returns: classes give exact instances, external types are untracked."""
        out: list[_Ent] = []
        for e in callee_ents:
            if e.kind == "sym" and self.symbol_kind.get(e.ref) == "class":
                out.append(_Ent("inst", e.ref, certain=e.certain, exact=True))
            elif e.kind in ("ext", "skip"):
                out.append(_SKIP)
            else:
                out.append(_UNKNOWN)
        return out

    def _eval_annotation(self, ann: str, ctx: _Ctx, guard: frozenset) -> list[_Ent]:
        """A declared type is a promise, not a proof: at most an `inferred` instance of it or a subclass."""
        norm = _normalize_type_name(ann)
        if norm in _UNTRACKED_TYPES:
            return [_SKIP]
        if norm in _OPAQUE_TYPES:
            return [_UNKNOWN]
        out: list[_Ent] = []
        for e in self._eval_expr(ann, ctx, guard):
            if e.kind == "sym" and self.symbol_kind.get(e.ref) == "class":
                out.append(_Ent("inst", e.ref, certain=False, exact=False))
            elif e.kind in ("ext", "skip"):
                out.append(_SKIP)
            else:
                out.append(_UNKNOWN)
        return out or [_UNKNOWN]

    def _member_of_module(self, module: str, attr: str, guard: frozenset) -> list[_Ent]:
        full = f"{module}.{attr}"
        if full in self.modules or self._is_namespace(full):
            return [_Ent("mod", full)]
        if module in self.modules and self.bindings.get(module, {}).get(attr):
            key = ("member", module, attr)
            if key in guard:
                return [_UNKNOWN]
            # Follows re-exports: `pkg/__init__.py` doing `from .core import Thing`.
            return self._eval_module_name(module, attr, guard | {key})
        return [_UNKNOWN]

    def _member(self, ent: _Ent, attr: str, guard: frozenset) -> list[_Ent]:
        if ent.kind in ("ext", "skip"):
            return [ent]
        if ent.kind == "mod":
            return self._member_of_module(ent.ref, attr, guard)
        if ent.kind == "sym" and self.symbol_kind.get(ent.ref) == "class":
            return self._class_member(ent.ref, attr, exact=True, certain=ent.certain, guard=guard)
        if ent.kind == "inst":
            return self._class_member(ent.ref, attr, exact=ent.exact, certain=ent.certain, guard=guard)
        return [_UNKNOWN]

    def _class_member(self, cls_id: str, attr: str, exact: bool, certain: bool, guard: frozenset) -> list[_Ent]:
        hierarchy = self._mro(cls_id) + self._descendants(cls_id)
        values = [v for c in hierarchy for v in self.class_attr_values.get(c, {}).get(attr, [])]
        anns = [a for c in hierarchy for a in self.class_attr_annotations.get(c, {}).get(attr, [])]

        found, complete = self._mro_lookup(cls_id, attr)
        if found:
            overrides = [] if exact else [m for d in self._descendants(cls_id)
                                          for m in self.class_methods.get(d, {}).get(attr, [])]
            sure = certain and complete and len(found) == 1 and not overrides
            out = [_Ent("sym", m, certain=sure) for m in found]
            out.extend(_Ent("sym", m, certain=False) for m in overrides)
            if values:
                out.append(_UNKNOWN)  # the method name is also rebound as an attribute
            return out

        if values or anns:
            ents: list[_Ent] = []
            unknown = not values
            for module, callee in values:
                if callee is None:
                    unknown = True
                else:
                    ents.extend(self._instances_of(self._eval_expr(callee, self._module_ctx(module), guard)))
            if unknown:
                if anns:
                    for module, ann in anns:
                        ents.extend(self._eval_annotation(ann, self._module_ctx(module), guard))
                else:
                    ents.append(_UNKNOWN)
            if not certain:
                ents = [replace(e, certain=False) if e.kind in ("inst", "sym") else e for e in ents]
            return ents

        if self._hierarchy_has_foreign_base(cls_id):
            return [_SKIP]  # most likely inherited from an external base class
        return [_UNKNOWN]

    def _super_method(self, name: str, ctx: _Ctx) -> list[_Ent]:
        if ctx.class_id is None or ctx.nested or not ctx.has_self:
            return [_UNKNOWN]
        parents = self.class_parents.get(ctx.class_id, [])
        single_line = len(parents) == 1 and not self.class_foreign.get(ctx.class_id)
        for parent in parents:
            found, complete = self._mro_lookup(parent, name)
            if found:
                sure = single_line and complete and len(found) == 1
                return [_Ent("sym", m, certain=sure) for m in found]
        if self._hierarchy_has_foreign_base(ctx.class_id):
            return [_SKIP]
        return [_UNKNOWN]

    # ------------------------------------------------------------------ calls

    def _process_call(self, caller_id: str, ctx: _Ctx, call: RawCall, rel_path: str) -> None:
        expr = call.callee_expr
        ents = _dedupe(self._eval_expr(expr, ctx, frozenset()))

        if all(e.kind in ("ext", "skip") for e in ents):
            return  # external or builtin callee: not part of the project graph
        if any(e.kind != "sym" for e in ents):
            self._add_edge(caller_id, f"unresolved:{expr}", "calls", "unresolved", rel_path, call.line, expr)
            return

        targets = list(dict.fromkeys(e.ref for e in ents))
        if len(targets) == 1 and all(e.certain for e in ents):
            self._add_edge(caller_id, targets[0], "calls", "resolved", rel_path, call.line, expr)
            return
        to_id = targets[0] if len(targets) == 1 else f"ambiguous:{expr}"
        self._add_edge(caller_id, to_id, "calls", "inferred", rel_path, call.line, expr, candidates=targets)
