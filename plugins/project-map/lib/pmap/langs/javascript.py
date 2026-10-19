"""TypeScript and JavaScript (ES modules, CommonJS, JSX) -> FileFacts."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, UNTRACKED, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of,
                             parser_for, preceding_comment, result_type_name, squash, text)

GRAMMAR_BY_EXT = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
                  ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript"}

# Global objects and functions: calls on them are not project links and not worth reporting as unresolved.
BUILTINS = {
    "console", "Math", "JSON", "Object", "Array", "Promise", "String", "Number", "Boolean", "Symbol", "Date",
    "RegExp", "Error", "TypeError", "Map", "Set", "WeakMap", "WeakSet", "Reflect", "Proxy", "Intl", "BigInt",
    "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "require", "import", "fetch", "structuredClone", "queueMicrotask", "encodeURIComponent", "decodeURIComponent",
    "document", "window", "globalThis", "process", "Buffer", "navigator", "localStorage", "sessionStorage",
    "describe", "it", "test", "expect", "beforeEach", "afterEach", "beforeAll", "afterAll", "jest", "vi",
    "Uint8Array", "ArrayBuffer", "URL", "URLSearchParams", "TextEncoder", "TextDecoder", "AbortController",
    "alert", "atob", "btoa", "requestAnimationFrame", "module", "exports", "__dirname", "__filename",
}

_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_CLASS_NODES = {"class_declaration", "abstract_class_declaration", "class"}
_TYPE_NODES = {"interface_declaration", "enum_declaration"}


def is_comment(node) -> bool:
    return node.type == "comment"


class _Extractor:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    # ---------------------------------------------------------------- top level

    def program(self, root) -> None:
        first = root.named_children[0] if root.named_children else None
        if first is not None and is_comment(first):
            from pmap.langs.base import doc_summary

            self.facts.doc = doc_summary(self.t(first))
        for node in root.named_children:
            self.statement(node, exported=False)
        # calls made at the top level of the file (module initialisation, React renders, scripts)
        for node in root.named_children:
            if node.type not in ("class_declaration", "abstract_class_declaration", "function_declaration",
                                 "generator_function_declaration", "export_statement", "lexical_declaration",
                                 "variable_declaration", "interface_declaration", "type_alias_declaration",
                                 "import_statement", "enum_declaration"):
                self.collect_calls(node, None)

    def statement(self, node, exported: bool, default: bool = False, doc_node=None) -> None:
        kind = node.type
        if kind == "import_statement":
            self.import_statement(node)
        elif kind == "export_statement":
            self.export_statement(node)
        elif kind in _CLASS_NODES:
            self.class_decl(node, exported, default, doc_node or node)
        elif kind in _TYPE_NODES:
            self.type_decl(node, exported, default, doc_node or node)
        elif kind in ("function_declaration", "generator_function_declaration"):
            self.function_decl(node, node.child_by_field_name("name"), node, exported, default, doc_node or node)
        elif kind in ("lexical_declaration", "variable_declaration"):
            self.variable_decl(node, exported, doc_node or node)
        elif kind == "expression_statement":
            self.commonjs_exports(node)
            self.requires(node)

    def import_statement(self, node) -> None:
        source = node.child_by_field_name("source")
        if source is None:
            return
        imp = Import(line=line_of(node), target=_string_value(self, source))
        imp.type_only = any(not c.is_named and self.t(c) == "type" for c in node.children)
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    imp.default = self.t(part)
                elif part.type == "namespace_import":
                    ident = next((c for c in part.named_children if c.type == "identifier"), None)
                    imp.alias = self.t(ident) if ident is not None else None
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        if spec.type != "import_specifier":
                            continue
                        name = spec.child_by_field_name("name")
                        alias = spec.child_by_field_name("alias")
                        imported = self.t(name)
                        imp.names.append((imported, self.t(alias) if alias is not None else imported))
        self.facts.imports.append(imp)

    def export_statement(self, node) -> None:
        source = node.child_by_field_name("source")
        decl = node.child_by_field_name("declaration")
        is_default = any(not c.is_named and self.t(c) == "default" for c in node.children)
        if source is not None:  # re-export
            imp = Import(line=line_of(node), target=_string_value(self, source), reexport=True)
            clause = next((c for c in node.named_children if c.type == "export_clause"), None)
            ns = next((c for c in node.named_children if c.type == "namespace_export"), None)
            if clause is not None:
                for spec in clause.named_children:
                    if spec.type == "export_specifier":
                        name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                        imp.names.append((self.t(name), self.t(alias) if alias is not None else self.t(name)))
            elif ns is not None:
                ident = next((c for c in ns.named_children if c.type in ("identifier", "string")), None)
                imp.alias = self.t(ident) if ident is not None else None
            else:
                imp.wildcard = True  # export * from
            self.facts.imports.append(imp)
            return
        if decl is not None:
            self.statement(decl, exported=True, default=is_default, doc_node=node)
            return
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if clause is not None:
            for spec in clause.named_children:
                if spec.type == "export_specifier":
                    name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                    self.facts.local_exports.append((self.t(name), self.t(alias) if alias is not None else self.t(name)))
            return
        value = node.child_by_field_name("value")
        if is_default and value is not None:
            if value.type == "identifier":
                self.facts.default_name = self.t(value)
            elif value.type in _CLASS_NODES:
                self.class_decl(value, True, True, node)
            elif value.type in _FUNCTION_VALUES:
                self.function_decl(value, value.child_by_field_name("name"), value, True, True, node,
                                   fallback_name="default")
            else:
                self.collect_calls(value, None)

    # ---------------------------------------------------------------- declarations

    def class_decl(self, node, exported: bool, default: bool, doc_node, parent: Optional[Decl] = None,
                   name: Optional[str] = None) -> Optional[Decl]:
        name_node = node.child_by_field_name("name")
        name = name or (self.t(name_node) if name_node is not None else ("default" if default else None))
        if not name:
            return None
        decl = Decl(name=name, kind="class", line_start=line_of(node), line_end=end_line_of(node), parent=parent,
                    doc=preceding_comment(doc_node, self.src, is_comment), exported=exported, default_export=default)
        for heritage in node.named_children:
            if heritage.type != "class_heritage":
                continue
            for clause in heritage.named_children:
                for value in clause.named_children:
                    if value.type in ("identifier", "member_expression", "type_identifier", "generic_type",
                                      "nested_type_identifier", "call_expression"):
                        target = value.child_by_field_name("name") if value.type == "generic_type" else value
                        if value.type == "call_expression":  # class X extends mixin(Base)
                            continue
                        decl.bases.append((base_type_name(self.t(target)), line_of(value)))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is None:
            return decl
        for member in body.named_children:
            if member.type in ("method_definition", "method_signature", "abstract_method_signature"):
                self.method(member, decl)
            elif member.type in ("public_field_definition", "field_definition"):
                fname = member.child_by_field_name("name") or member.child_by_field_name("property")
                value = member.child_by_field_name("value")
                ftype = _type_of_annotation(self, member.child_by_field_name("type"))
                if fname is not None and ftype:
                    decl.field_types[self.t(fname)] = ftype
                if fname is not None and value is not None and value.type in _FUNCTION_VALUES:
                    self.method(value, decl, name=self.t(fname), doc_node=member)
                elif value is not None:
                    if fname is not None and value.type == "new_expression":
                        ctor = value.child_by_field_name("constructor")
                        if ctor is not None:
                            decl.field_types.setdefault(self.t(fname), base_type_name(self.t(ctor)))
                    self.collect_calls(value, decl)
        return decl

    def type_decl(self, node, exported: bool, default: bool, doc_node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        decl = Decl(name=self.t(name_node), kind="class", line_start=line_of(node), line_end=end_line_of(node),
                    doc=preceding_comment(doc_node, self.src, is_comment), exported=exported, default_export=default,
                    interface=node.type == "interface_declaration")
        for clause in node.named_children:
            if clause.type in ("extends_type_clause", "extends_clause"):
                for value in clause.named_children:
                    decl.bases.append((base_type_name(self.t(value)), line_of(value)))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None and node.type == "interface_declaration":
            for member in body.named_children:
                if member.type == "method_signature":
                    self.method(member, decl)

    def method(self, node, cls: Decl, name: Optional[str] = None, doc_node=None) -> None:
        name_node = node.child_by_field_name("name")
        mname = name or (self.t(name_node) if name_node is not None else None)
        if not mname:
            return
        decl = Decl(name=mname, kind="method", line_start=line_of(node), line_end=end_line_of(node), parent=cls,
                    signature=self.signature(node), doc=preceding_comment(doc_node or node, self.src, is_comment),
                    returns=self.returns(node))
        self.parameters(node, decl, cls)
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            self.locals(body, decl)
            self.collect_calls(body, decl)

    def function_decl(self, node, name_node, fn_node, exported: bool, default: bool, doc_node,
                      fallback_name: Optional[str] = None, name: Optional[str] = None) -> None:
        fname = name or (self.t(name_node) if name_node is not None else fallback_name)
        if not fname:
            return
        decl = Decl(name=fname, kind="function", line_start=line_of(node), line_end=end_line_of(node),
                    signature=self.signature(fn_node), doc=preceding_comment(doc_node, self.src, is_comment),
                    exported=exported, default_export=default, returns=self.returns(fn_node))
        self.parameters(fn_node, decl, None)
        self.facts.decls.append(decl)
        body = fn_node.child_by_field_name("body")
        if body is not None:
            self.locals(body, decl)
            self.collect_calls(body, decl)

    def variable_decl(self, node, exported: bool, doc_node) -> None:
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name_node is None or value is None:
                continue
            if _is_require(self, value):
                self.require_binding(name_node, value)
                continue
            if name_node.type != "identifier":
                self.collect_calls(value, None)
                continue
            vtype = _type_of_annotation(self, declarator.child_by_field_name("type")) or _value_type(self, value)
            if vtype and value.type not in _FUNCTION_VALUES:
                self.facts.var_types[self.t(name_node)] = vtype
            if value.type in _FUNCTION_VALUES:
                self.function_decl(declarator, None, value, exported, False, doc_node, name=self.t(name_node))
            elif value.type == "class":
                self.class_decl(value, exported, False, doc_node, name=self.t(name_node))
            elif value.type == "call_expression" and _wraps_function(value):
                # const Button = React.memo(() => ...), forwardRef(...), styled(...)
                fn = next(c for c in value.child_by_field_name("arguments").named_children if c.type in _FUNCTION_VALUES)
                self.function_decl(declarator, None, fn, exported, False, doc_node, name=self.t(name_node))
                self.collect_calls(value.child_by_field_name("function"), None)
            else:
                self.collect_calls(value, None)

    def parameters(self, fn_node, decl: Decl, cls: Optional[Decl]) -> None:
        params = fn_node.child_by_field_name("parameters")
        if params is None:
            return
        for param in params.named_children:
            pattern = param.child_by_field_name("pattern") or param.child_by_field_name("name")
            ptype = _type_of_annotation(self, param.child_by_field_name("type"))
            if pattern is None or pattern.type != "identifier" or not ptype:
                continue
            decl.var_types[self.t(pattern)] = ptype
            # constructor(private repo: Repo) declares a field too
            if cls is not None and any(c.type == "accessibility_modifier" or self.t(c) == "readonly"
                                       for c in param.children):
                cls.field_types[self.t(pattern)] = ptype

    def locals(self, body, decl: Decl) -> None:
        for node in _walk_same_function(body):
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                if name_node is None or name_node.type != "identifier":
                    continue
                vtype = (_type_of_annotation(self, node.child_by_field_name("type"))
                         or _value_type(self, node.child_by_field_name("value")))
                if vtype:
                    decl.var_types[self.t(name_node)] = vtype
            elif node.type == "assignment_expression" and decl.parent is not None:
                left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
                if left is not None and left.type == "member_expression" and self.t(left.child_by_field_name("object")) == "this":
                    prop = self.t(left.child_by_field_name("property"))
                    if right is not None and right.type == "new_expression":
                        ctor = right.child_by_field_name("constructor")
                        if ctor is not None:
                            decl.parent.field_types.setdefault(prop, base_type_name(self.t(ctor)))
                    elif right is not None and right.type == "identifier" and self.t(right) in decl.var_types:
                        decl.parent.field_types.setdefault(prop, decl.var_types[self.t(right)])

    def returns(self, fn_node) -> Optional[str]:
        ret = fn_node.child_by_field_name("return_type")
        return result_type_name(self.t(ret)) if ret is not None else None

    def signature(self, fn_node) -> Optional[str]:
        params = fn_node.child_by_field_name("parameters") or fn_node.child_by_field_name("parameter")
        if params is None:
            return None
        sig = self.t(params)
        if not sig.startswith("("):
            sig = f"({sig})"
        ret = fn_node.child_by_field_name("return_type")
        if ret is not None:
            sig += self.t(ret) if self.t(ret).startswith(":") else ": " + self.t(ret)
        return squash(sig)

    # ---------------------------------------------------------------- CommonJS

    def require_binding(self, name_node, call) -> None:
        imp = Import(line=line_of(call), target=_require_target(self, call))
        if name_node.type == "identifier":
            imp.alias = self.t(name_node)
        elif name_node.type == "object_pattern":
            for prop in name_node.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    imp.names.append((self.t(prop), self.t(prop)))
                elif prop.type == "pair_pattern":
                    key, value = prop.child_by_field_name("key"), prop.child_by_field_name("value")
                    if key is not None and value is not None and value.type == "identifier":
                        imp.names.append((self.t(key), self.t(value)))
        self.facts.imports.append(imp)

    def requires(self, node) -> None:
        expr = node.named_children[0] if node.named_children else None
        if expr is not None and _is_require(self, expr):
            self.facts.imports.append(Import(line=line_of(expr), target=_require_target(self, expr)))

    def commonjs_exports(self, node) -> None:
        expr = node.named_children[0] if node.named_children else None
        if expr is None or expr.type != "assignment_expression":
            return
        left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
        target = self.t(left)
        if target == "module.exports" and right is not None:
            if right.type == "identifier":
                self.facts.default_name = self.t(right)
            elif right.type == "object":
                for prop in right.named_children:
                    if prop.type == "shorthand_property_identifier":
                        self.facts.local_exports.append((self.t(prop), self.t(prop)))
                    elif prop.type == "pair":
                        key, value = prop.child_by_field_name("key"), prop.child_by_field_name("value")
                        if value is not None and value.type == "identifier":
                            self.facts.local_exports.append((self.t(value), self.t(key)))
                        elif value is not None and value.type in _FUNCTION_VALUES:
                            self.function_decl(prop, None, value, True, False, prop, name=self.t(key))
        elif (target.startswith("exports.") or target.startswith("module.exports.")) and right is not None:
            name = target.rsplit(".", 1)[1]
            if right.type == "identifier":
                self.facts.local_exports.append((self.t(right), name))
            elif right.type in _FUNCTION_VALUES:
                self.function_decl(expr, None, right, True, False, node, name=name)

    # ---------------------------------------------------------------- calls

    def collect_calls(self, root, caller: Optional[Decl]) -> None:
        for node in _walk_same_function(root, include_root=True):
            if node.type == "call_expression":
                fn = node.child_by_field_name("function")
                if fn is not None and fn.type == "import":  # dynamic import("./x")
                    args = node.child_by_field_name("arguments")
                    lit = args.named_children[0] if args is not None and args.named_children else None
                    if lit is not None and lit.type == "string":
                        self.facts.imports.append(Import(line=line_of(node), target=_string_value(self, lit)))
                    continue
                chain = self.chain(fn)
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain))
            elif node.type == "new_expression":
                chain = self.chain(node.child_by_field_name("constructor"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))
            elif node.type in ("jsx_opening_element", "jsx_self_closing_element"):
                name = node.child_by_field_name("name")
                chain = self.chain(name)
                if chain and chain[-1][:1].isupper():  # <Button />, <ui.Card />; lowercase tags are HTML
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))
        # functions declared inside functions are not map nodes; their calls belong to the enclosing one
        for node in _nested_functions(root):
            body = node.child_by_field_name("body")
            if body is not None and node is not root:
                self.collect_calls(body, caller)

    def chain(self, node) -> Optional[List[str]]:
        if node is None:
            return None
        kind = node.type
        if kind in ("identifier", "type_identifier", "property_identifier", "private_property_identifier"):
            return [self.t(node)]
        if kind in ("this", "super"):
            return [kind]
        if kind in ("member_expression", "nested_identifier", "member_expression_optional"):
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            if obj is None or prop is None:
                parts = [c for c in node.named_children]
                if len(parts) != 2:
                    return None
                obj, prop = parts
            head = self.chain(obj)
            return head + [self.t(prop)] if head else None
        if kind == "non_null_expression" and node.named_children:
            return self.chain(node.named_children[0])
        if kind == "parenthesized_expression" and node.named_children:
            return self.chain(node.named_children[0])
        return None


def _string_value(ex: _Extractor, node) -> str:
    frag = next((c for c in node.named_children if c.type == "string_fragment"), None)
    return ex.t(frag) if frag is not None else ex.t(node).strip("'\"`")


def _is_require(ex: _Extractor, node) -> bool:
    if node.type == "await_expression" and node.named_children:
        node = node.named_children[0]
    if node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    return (fn is not None and ex.t(fn) == "require" and args is not None and len(args.named_children) == 1
            and args.named_children[0].type == "string")


def _require_target(ex: _Extractor, node) -> str:
    if node.type == "await_expression":
        node = node.named_children[0]
    return _string_value(ex, node.child_by_field_name("arguments").named_children[0])


def _wraps_function(call) -> bool:
    args = call.child_by_field_name("arguments")
    return args is not None and any(c.type in _FUNCTION_VALUES for c in args.named_children)


def _type_of_annotation(ex: _Extractor, ann) -> str:
    """`: Repo` / `: Repo<T>` / `: ns.Repo` -> the type name; UNTRACKED for builtin, union or array types."""
    if ann is None:
        return ""
    node = ann.named_children[0] if ann.type == "type_annotation" and ann.named_children else ann
    if node.type == "generic_type":
        node = node.child_by_field_name("name") or node
    if node.type in ("type_identifier", "nested_type_identifier", "identifier"):
        return base_type_name(ex.t(node))
    return UNTRACKED


_LITERALS = {"array", "object", "string", "template_string", "number", "true", "false", "regex", "arrow_function",
             "function_expression"}


def _value_type(ex: _Extractor, value) -> str:
    """The type a variable gets from its initial value: `new X()` -> X, `f()` -> f's result, a literal -> UNTRACKED."""
    if value is None:
        return ""
    if value.type == "await_expression" and value.named_children:
        value = value.named_children[0]
    if value.type == "call_expression":
        chain = ex.chain(value.child_by_field_name("function"))
        return RESULT + ".".join(chain) if chain else ""
    if value.type == "new_expression":
        ctor = value.child_by_field_name("constructor")
        return base_type_name(ex.t(ctor)) if ctor is not None else ""
    return UNTRACKED if value.type in _LITERALS else ""


def _walk_same_function(root, include_root: bool = True):
    """Descendants of `root` without entering nested function bodies (those are walked separately)."""
    stack = [root]
    first = True
    while stack:
        node = stack.pop()
        if not first and node.type in _FUNCTION_VALUES | {"function_declaration", "method_definition",
                                                          "generator_function_declaration", "class_body"}:
            continue
        if not first or include_root:
            yield node
        first = False
        stack.extend(reversed(node.named_children))


def _nested_functions(root):
    """Function bodies directly nested in `root` (closures, callbacks), one level deep."""
    stack = list(reversed(root.named_children))
    while stack:
        node = stack.pop()
        if node.type in _FUNCTION_VALUES | {"function_declaration", "generator_function_declaration"}:
            yield node
            continue
        if node.type in ("class_body", "method_definition"):
            continue
        stack.extend(reversed(node.named_children))


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    grammar = GRAMMAR_BY_EXT.get(ext, "javascript")
    tree = parser_for(grammar).parse(src)
    language = "typescript" if grammar in ("typescript", "tsx") else "javascript"
    facts = FileFacts(rel_path=rel_path, language=language, line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _Extractor(src, facts).program(tree.root_node)
    return facts
