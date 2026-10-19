"""Python stdlib `ast` implementation of the CodeParser interface."""

from __future__ import annotations

import ast

from pmap.parser import CodeParser, ParsedModuleAst, RawCall, RawImport, RawScope, RawSymbol

_OPTIONAL_WRAPPERS = {"Optional", "typing.Optional"}
_UNION_WRAPPERS = {"Union", "typing.Union"}


def _first_doc_line(node: ast.AST) -> str | None:
    if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    doc = ast.get_docstring(node)
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    return first if first else None


def _all_args(args: ast.arguments) -> list[ast.arg]:
    result = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg:
        result.append(args.vararg)
    if args.kwarg:
        result.append(args.kwarg)
    return result


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args_parts: list[str] = []
    all_args = list(node.args.posonlyargs) + list(node.args.args)
    defaults_offset = len(all_args) - len(node.args.defaults)
    for idx, arg in enumerate(all_args):
        part = arg.arg
        if arg.annotation is not None:
            try:
                part += f": {ast.unparse(arg.annotation)}"
            except Exception:
                pass
        if idx >= defaults_offset:
            part += "=..."
        args_parts.append(part)
    if node.args.vararg:
        args_parts.append(f"*{node.args.vararg.arg}")
    for kwarg in node.args.kwonlyargs:
        part = kwarg.arg
        if kwarg.annotation is not None:
            try:
                part += f": {ast.unparse(kwarg.annotation)}"
            except Exception:
                pass
        args_parts.append(part)
    if node.args.kwarg:
        args_parts.append(f"**{node.args.kwarg.arg}")

    sig = f"({', '.join(args_parts)})"
    if node.returns is not None:
        try:
            sig += f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass
    return sig


def _extract_dotted_name(expr: ast.AST) -> str | None:
    """Return dotted string for Name or Attribute chain (e.g. 'self.repo.save_order'), else None."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        prefix = _extract_dotted_name(expr.value)
        if prefix:
            return f"{prefix}.{expr.attr}"
    return None


def _is_none(expr: ast.AST) -> bool:
    return (isinstance(expr, ast.Constant) and expr.value is None) or _extract_dotted_name(expr) == "None"


def _flatten_bitor(expr: ast.AST) -> list[ast.AST]:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _flatten_bitor(expr.left) + _flatten_bitor(expr.right)
    return [expr]


def _single_non_none(elts: list[ast.AST]) -> str | None:
    non_none = [e for e in elts if not _is_none(e)]
    if len(non_none) != 1:
        return None
    return annotation_type_expr(non_none[0])


def annotation_type_expr(ann: ast.AST | None) -> str | None:
    """Reduce an annotation to one dotted type name, or None when it names no single type.

    `Optional[X]`, `X | None` and `"X"` reduce to `X`; generic containers (`list[X]`) reduce
    to the container name; unions of several types reduce to None.
    """
    if ann is None:
        return None
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            return annotation_type_expr(ast.parse(ann.value, mode="eval").body)
        except SyntaxError:
            return None
    dotted = _extract_dotted_name(ann)
    if dotted:
        return dotted
    if isinstance(ann, ast.Subscript):
        outer = _extract_dotted_name(ann.value)
        inner = ann.slice
        if isinstance(inner, getattr(ast, "Index", ())):  # Python 3.8 wraps subscripts
            inner = inner.value  # type: ignore[attr-defined]
        if outer in _OPTIONAL_WRAPPERS:
            return annotation_type_expr(inner)
        if outer in _UNION_WRAPPERS:
            return _single_non_none(list(inner.elts) if isinstance(inner, ast.Tuple) else [inner])
        return outer
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _single_non_none(_flatten_bitor(ann))
    return None


def _constructor_callee(value: ast.AST | None) -> str | None:
    """`Foo(...)` -> "Foo"; anything else -> None (value of unknown type)."""
    if isinstance(value, ast.Call):
        return _extract_dotted_name(value.func)
    return None


def _is_type_checking_test(test: ast.AST) -> bool:
    name = _extract_dotted_name(test)
    return name is not None and (name == "TYPE_CHECKING" or name.endswith(".TYPE_CHECKING"))


def _is_bare_super_call(expr: ast.AST) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "super"
        and not expr.args
        and not expr.keywords
    )


def _base_expr(base: ast.AST) -> str:
    """Dotted name of a class base; `Generic[T]` -> "Generic"; unanalysable bases -> "<expr>"."""
    dotted = _extract_dotted_name(base)
    if dotted:
        return dotted
    if isinstance(base, ast.Subscript):
        outer = _extract_dotted_name(base.value)
        if outer:
            return outer
    return "<expr>"


def _node_start_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if node.decorator_list:
        return min(getattr(d, "lineno", node.lineno) for d in node.decorator_list)
    return node.lineno


class _ScopeVisitor(ast.NodeVisitor):
    """Collects calls and every name binding of one scope, descending into nested scopes.

    Bindings made in nested functions/classes are merged into the enclosing scope. That is
    deliberately conservative: an extra binding can only make a name ambiguous, never
    make an ambiguous name look resolved.
    """

    def __init__(self, scope: RawScope, imports: list[RawImport], track_self: bool) -> None:
        self.scope = scope
        self.imports = imports
        self.track_self = track_self
        self.depth = 0
        self.global_names: set[str] = set()

    def finish(self) -> None:
        self.scope.local_names -= self.global_names
        # `self.x = Foo()` where `Foo` is a local name cannot be resolved from module scope later.
        for attr, values in self.scope.self_assignments.items():
            self.scope.self_assignments[attr] = [
                v if v is None or v.split(".")[0] not in self.scope.local_names else None for v in values
            ]

    def _bind(self, name: str, callee: str | None) -> None:
        self.scope.local_names.add(name)
        self.scope.assignments.setdefault(name, []).append(callee)

    def _bind_self_attr(self, attr: str, callee: str | None) -> None:
        if self.track_self:
            # In nested code `self` may be another object (e.g. a nested class): value unknown.
            self.scope.self_assignments.setdefault(attr, []).append(callee if self.depth == 0 else None)

    @staticmethod
    def _self_attr_name(target: ast.AST) -> str | None:
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            return target.attr
        return None

    def _bind_target(self, target: ast.AST, callee: str | None) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, callee)
            return
        attr = self._self_attr_name(target)
        if attr is not None:
            self._bind_self_attr(attr, callee)
            return
        # Tuples, starred, subscripts: every name bound inside gets an unknown value.
        self.visit(target)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._bind(node.id, None)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Store):
            attr = self._self_attr_name(node)
            if attr is not None:
                self._bind_self_attr(attr, None)
                return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        callee = _constructor_callee(node.value)
        for target in node.targets:
            self._bind_target(target, callee)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        ann = annotation_type_expr(node.annotation)
        if self.depth == 0 and ann:
            if isinstance(node.target, ast.Name):
                self.scope.annotations[node.target.id] = ann
            else:
                attr = self._self_attr_name(node.target)
                if attr is not None and self.track_self:
                    self.scope.self_annotations[attr] = ann
        if node.value is not None:
            self._bind_target(node.target, _constructor_callee(node.value))
            self.visit(node.value)
        elif isinstance(node.target, ast.Name):
            self.scope.local_names.add(node.target.id)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.global_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name, None)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.AST) -> None:
        name = getattr(node, "name", None)
        if name:
            self._bind(name, None)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.AST) -> None:
        self.visit_MatchAs(node)

    def visit_MatchMapping(self, node: ast.AST) -> None:
        rest = getattr(node, "rest", None)
        if rest:
            self._bind(rest, None)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(
            RawImport(
                module=None,
                names=[(a.name, a.asname) for a in node.names],
                level=0,
                line=node.lineno,
                is_from=False,
                scope="local",
            )
        )
        for alias in node.names:
            self._bind(alias.asname or alias.name.split(".")[0], None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(
            RawImport(
                module=node.module,
                names=[(a.name, a.asname) for a in node.names],
                level=node.level or 0,
                line=node.lineno,
                is_from=True,
                scope="local",
            )
        )
        for alias in node.names:
            if alias.name != "*":
                self._bind(alias.asname or alias.name, None)

    def _visit_nested_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        # Decorators and defaults run in the enclosing scope.
        for dec in getattr(node, "decorator_list", []):
            self.visit(dec)
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)
        if not isinstance(node, ast.Lambda):
            self._bind(node.name, None)
        self.depth += 1
        for arg in _all_args(node.args):
            self._bind(arg.arg, None)
        body = node.body if isinstance(node.body, list) else [node.body]
        for stmt in body:
            self.visit(stmt)
        self.depth -= 1

    visit_FunctionDef = _visit_nested_function
    visit_AsyncFunctionDef = _visit_nested_function
    visit_Lambda = _visit_nested_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw.value)
        self._bind(node.name, None)
        self.depth += 1
        for stmt in node.body:
            self.visit(stmt)
        self.depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        callee = _extract_dotted_name(node.func)
        if callee is None and isinstance(node.func, ast.Attribute) and _is_bare_super_call(node.func.value):
            callee = f"super.{node.func.attr}"
        if callee:
            self.scope.calls.append(RawCall(callee_expr=callee, line=node.lineno, nested=self.depth > 0))
        self.generic_visit(node)


class PythonAstParser(CodeParser):
    """Concrete CodeParser using Python's built-in `ast` module."""

    @property
    def analyzer_id(self) -> str:
        return "python-ast-v0"

    def parse_file(self, source_code: str, rel_path: str, module_name: str, file_hash: str) -> ParsedModuleAst:
        tree = ast.parse(source_code, filename=rel_path)
        total_lines = max(1, len(source_code.splitlines()))

        imports: list[RawImport] = []
        symbols: list[RawSymbol] = []
        scope = RawScope()
        visitor = _ScopeVisitor(scope, imports, track_self=False)
        self._walk_module_body(tree.body, visitor, imports, symbols, type_checking=False)
        visitor.finish()
        imports.sort(key=lambda imp: imp.line)

        return ParsedModuleAst(
            rel_path=rel_path,
            module_name=module_name,
            line_start=1,
            line_end=total_lines,
            file_hash=file_hash,
            docstring_summary=_first_doc_line(tree),
            imports=imports,
            symbols=symbols,
            scope=scope,
        )

    def _walk_module_body(
        self,
        stmts: list[ast.stmt],
        visitor: _ScopeVisitor,
        imports: list[RawImport],
        symbols: list[RawSymbol],
        type_checking: bool,
    ) -> None:
        """Walk module-level statements, entering compound statements so conditional defs/imports are kept."""

        def walk(body: list[ast.stmt], tc: bool = type_checking) -> None:
            self._walk_module_body(body, visitor, imports, symbols, tc)

        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(self._parse_function(stmt, "function", imports))
            elif isinstance(stmt, ast.ClassDef):
                symbols.append(self._parse_class(stmt, imports))
            elif isinstance(stmt, ast.Import):
                imports.append(
                    RawImport(
                        module=None,
                        names=[(a.name, a.asname) for a in stmt.names],
                        level=0,
                        line=stmt.lineno,
                        is_from=False,
                        type_only=type_checking,
                    )
                )
            elif isinstance(stmt, ast.ImportFrom):
                imports.append(
                    RawImport(
                        module=stmt.module,
                        names=[(a.name, a.asname) for a in stmt.names],
                        level=stmt.level or 0,
                        line=stmt.lineno,
                        is_from=True,
                        type_only=type_checking,
                    )
                )
            elif isinstance(stmt, ast.If):
                if _is_type_checking_test(stmt.test):
                    walk(stmt.body, True)
                    walk(stmt.orelse)
                else:
                    visitor.visit(stmt.test)
                    walk(stmt.body)
                    walk(stmt.orelse)
            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                walk(stmt.body)
                for handler in stmt.handlers:
                    if handler.type is not None:
                        visitor.visit(handler.type)
                    if handler.name:
                        visitor._bind(handler.name, None)
                    walk(handler.body)
                walk(stmt.orelse)
                walk(stmt.finalbody)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    visitor.visit(item.context_expr)
                    if item.optional_vars is not None:
                        visitor._bind_target(item.optional_vars, None)
                walk(stmt.body)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                visitor._bind_target(stmt.target, None)
                visitor.visit(stmt.iter)
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.While):
                visitor.visit(stmt.test)
                walk(stmt.body)
                walk(stmt.orelse)
            elif type(stmt).__name__ == "Match":
                visitor.visit(stmt.subject)  # type: ignore[attr-defined]
                for case in stmt.cases:  # type: ignore[attr-defined]
                    visitor.visit(case.pattern)
                    if case.guard is not None:
                        visitor.visit(case.guard)
                    walk(case.body)
            else:
                visitor.visit(stmt)

    def _parse_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str, imports: list[RawImport]
    ) -> RawSymbol:
        positional = list(node.args.posonlyargs) + list(node.args.args)
        has_self = kind == "method" and bool(positional) and positional[0].arg == "self"
        scope = RawScope()
        visitor = _ScopeVisitor(scope, imports, track_self=has_self)
        for arg in _all_args(node.args):
            visitor._bind(arg.arg, None)
            ann = annotation_type_expr(arg.annotation)
            if ann:
                scope.annotations[arg.arg] = ann
        for stmt in node.body:
            visitor.visit(stmt)
        visitor.finish()

        return RawSymbol(
            kind=kind,
            name=node.name,
            line_start=_node_start_line(node),
            line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
            signature=_format_signature(node),
            docstring_summary=_first_doc_line(node),
            scope=scope,
            has_self=has_self,
        )

    def _parse_class(self, node: ast.ClassDef, imports: list[RawImport]) -> RawSymbol:
        bases = [(_base_expr(base), getattr(base, "lineno", node.lineno)) for base in node.bases]
        scope = RawScope()
        visitor = _ScopeVisitor(scope, imports, track_self=False)
        methods: list[RawSymbol] = []
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._parse_function(stmt, "method", imports))
            else:
                visitor.visit(stmt)
        visitor.finish()

        return RawSymbol(
            kind="class",
            name=node.name,
            line_start=_node_start_line(node),
            line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
            docstring_summary=_first_doc_line(node),
            bases=bases,
            scope=scope,
            children=methods,
        )
