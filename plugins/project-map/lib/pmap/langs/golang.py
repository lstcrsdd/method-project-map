"""Go -> FileFacts. Methods are attached to their receiver type by the resolver (it may live in another file)."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of,
                             parser_for, preceding_comment, result_type_name, squash, text)

BUILTINS = {
    "append", "cap", "clear", "close", "complex", "copy", "delete", "imag", "len", "make", "max", "min", "new",
    "panic", "print", "println", "real", "recover", "string", "int", "int8", "int16", "int32", "int64", "uint",
    "uint8", "uint16", "uint32", "uint64", "uintptr", "float32", "float64", "byte", "rune", "bool", "error", "any",
}
THIS = "@this"  # var_types marker: the method's receiver


def is_comment(node) -> bool:
    return node.type == "comment"


class _Extractor:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        for node in root.named_children:
            kind = node.type
            if kind == "package_clause":
                ident = next((c for c in node.named_children if c.type == "package_identifier"), None)
                self.facts.namespace = self.t(ident) if ident is not None else None
                self.facts.doc = preceding_comment(node, self.src, is_comment)
            elif kind == "import_declaration":
                for spec in _import_specs(node):
                    path = spec.child_by_field_name("path")
                    name = spec.child_by_field_name("name")
                    self.facts.imports.append(Import(line=line_of(spec), target=self.t(path).strip('"`'),
                                                     alias=self.t(name) if name is not None else None))
            elif kind == "type_declaration":
                for spec in node.named_children:
                    if spec.type in ("type_spec", "type_alias"):
                        self.type_spec(spec, node)
            elif kind == "function_declaration":
                self.function(node, None)
            elif kind == "method_declaration":
                self.method(node)
            elif kind in ("var_declaration", "const_declaration"):
                self.calls(node, None)

    def type_spec(self, spec, decl_node) -> None:
        name = spec.child_by_field_name("name")
        tnode = spec.child_by_field_name("type")
        if name is None:
            return
        doc_node = decl_node if len(decl_node.named_children) == 1 else spec
        decl = Decl(name=self.t(name), kind="class", line_start=line_of(spec), line_end=end_line_of(spec),
                    doc=preceding_comment(doc_node, self.src, is_comment), exported=self.t(name)[:1].isupper())
        if tnode is not None and tnode.type == "struct_type":
            for fdl in tnode.named_children:
                for field in fdl.named_children:
                    if field.type != "field_declaration":
                        continue
                    ftype = field.child_by_field_name("type")
                    names = [c for c in field.named_children if c.type == "field_identifier"]
                    if ftype is None:
                        continue
                    if not names:  # embedded struct: its methods are promoted
                        decl.bases.append((_type_name(self, ftype), line_of(field)))
                    for n in names:
                        tname = _type_name(self, ftype)
                        if tname:
                            decl.field_types[self.t(n)] = tname
        elif tnode is not None and tnode.type == "interface_type":
            decl.interface = True
            self.facts.decls.append(decl)
            for elem in tnode.named_children:
                if elem.type in ("method_elem", "method_spec"):
                    mname = elem.child_by_field_name("name")
                    if mname is not None:
                        self.facts.decls.append(Decl(
                            name=self.t(mname), kind="method", line_start=line_of(elem), line_end=end_line_of(elem),
                            parent=decl, signature=self.signature(elem), exported=self.t(mname)[:1].isupper(),
                            doc=preceding_comment(elem, self.src, is_comment)))
                elif elem.type in ("type_elem", "constraint_elem", "interface_type_name", "type_identifier",
                                   "qualified_type"):
                    tname = _type_name(self, elem)
                    if tname:
                        decl.bases.append((tname, line_of(elem)))
            return
        self.facts.decls.append(decl)

    def function(self, node, receiver: Optional[str], recv_name: Optional[str] = None) -> Decl:
        name = node.child_by_field_name("name")
        result = node.child_by_field_name("result")
        decl = Decl(name=self.t(name), kind="method" if receiver else "function", line_start=line_of(node),
                    line_end=end_line_of(node), signature=self.signature(node),
                    doc=preceding_comment(node, self.src, is_comment), exported=self.t(name)[:1].isupper(),
                    receiver=receiver, returns=result_type_name(self.t(result)) if result is not None else None)
        if recv_name:
            decl.var_types[recv_name] = THIS
        params = node.child_by_field_name("parameters")
        for param in params.named_children if params is not None else []:
            ptype = param.child_by_field_name("type")
            tname = _type_name(self, ptype) if ptype is not None else ""
            for n in param.named_children:
                if n.type == "identifier" and tname:
                    decl.var_types[self.t(n)] = tname
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            self.locals(body, decl)
            self.calls(body, decl)
        return decl

    def method(self, node) -> None:
        receiver = node.child_by_field_name("receiver")
        rtype, rname = "", None
        if receiver is not None and receiver.named_children:
            param = receiver.named_children[0]
            ptype = param.child_by_field_name("type")
            pname = param.child_by_field_name("name")
            rtype = _type_name(self, ptype) if ptype is not None else ""
            rname = self.t(pname) if pname is not None else None
        self.function(node, rtype or "?", rname)

    def locals(self, body, decl: Decl) -> None:
        for node in _walk(body):
            if node.type == "short_var_declaration":
                left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
                if left is None or right is None:
                    continue
                values = right.named_children
                for i, ident in enumerate(left.named_children):
                    if ident.type != "identifier":
                        continue
                    if len(values) == len(left.named_children):
                        tname = _literal_type(self, values[i])
                        if not tname and values[i].type == "call_expression":
                            chain = self.chain(values[i].child_by_field_name("function"))
                            tname = RESULT + ".".join(chain) if chain else ""
                    elif i == 0 and len(values) == 1 and values[0].type == "call_expression":
                        chain = self.chain(values[0].child_by_field_name("function"))  # v, err := f()
                        tname = RESULT + ".".join(chain) if chain else ""
                    else:
                        tname = ""
                    if tname:
                        decl.var_types[self.t(ident)] = tname
            elif node.type == "func_literal":  # closures: func(w ResponseWriter, r *Request) { ... }
                params = node.child_by_field_name("parameters")
                for param in params.named_children if params is not None else []:
                    tname = _type_name(self, param.child_by_field_name("type"))
                    for n in param.named_children:
                        if n.type == "identifier" and tname:
                            decl.var_types.setdefault(self.t(n), tname)
            elif node.type == "var_spec":
                vtype = node.child_by_field_name("type")
                tname = _type_name(self, vtype) if vtype is not None else ""
                for ident in node.named_children:
                    if ident.type == "identifier" and tname:
                        decl.var_types[self.t(ident)] = tname

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in _walk(root):
            if node.type == "call_expression":
                chain = self.chain(node.child_by_field_name("function"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain))
            elif node.type == "composite_literal":
                tnode = node.child_by_field_name("type")
                chain = self.chain(tnode)
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))

    def chain(self, node) -> Optional[List[str]]:
        if node is None:
            return None
        if node.type in ("identifier", "type_identifier", "field_identifier", "package_identifier"):
            return [self.t(node)]
        if node.type == "selector_expression":
            head = self.chain(node.child_by_field_name("operand"))
            field = node.child_by_field_name("field")
            return head + [self.t(field)] if head and field is not None else None
        if node.type == "qualified_type":
            pkg, name = node.child_by_field_name("package"), node.child_by_field_name("name")
            return [self.t(pkg), self.t(name)] if pkg is not None and name is not None else None
        if node.type == "generic_type":
            return self.chain(node.child_by_field_name("type"))
        if node.type == "parenthesized_expression" and node.named_children:
            return self.chain(node.named_children[0])
        return None

    def signature(self, node) -> Optional[str]:
        params = node.child_by_field_name("parameters")
        if params is None:
            return None
        result = node.child_by_field_name("result")
        return squash(self.t(params) + (" " + self.t(result) if result is not None else ""))


def _import_specs(node):
    for child in node.named_children:
        if child.type == "import_spec":
            yield child
        elif child.type == "import_spec_list":
            yield from (c for c in child.named_children if c.type == "import_spec")


def _type_name(ex: _Extractor, node) -> str:
    if node is None:
        return ""
    if node.type in ("pointer_type", "type_elem", "constraint_elem") and node.named_children:
        return _type_name(ex, node.named_children[0])
    if node.type == "generic_type":
        return _type_name(ex, node.child_by_field_name("type"))
    if node.type in ("type_identifier", "qualified_type", "identifier", "interface_type_name"):
        return base_type_name(ex.t(node))
    return ""


def _literal_type(ex: _Extractor, value) -> str:
    """`&T{...}` / `T{...}` -> T; anything else gives no type."""
    if value.type == "unary_expression" and value.named_children:
        value = value.named_children[-1]
    if value.type == "composite_literal":
        return _type_name(ex, value.child_by_field_name("type"))
    return ""


def _walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    tree = parser_for("go").parse(src)
    facts = FileFacts(rel_path=rel_path, language="go", line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _Extractor(src, facts).run(tree.root_node)
    return facts
