"""C and C++ (Win32, WinUI / C++/WinRT, libraries) -> FileFacts.

Classes, namespaces, functions and methods; a method defined outside its class (`bool Manager::Start(...)`) is
attached to the class by the resolver, and declarations in headers give way to the definitions.
"""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of, parser_for,
                             preceding_comment, squash, text, walk)

BUILTINS = {"sizeof", "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast", "std", "printf", "fprintf",
            "sprintf", "snprintf", "malloc", "calloc", "realloc", "free", "memcpy", "memset", "memcmp", "strlen",
            "strcmp", "strcpy", "strncpy", "assert", "exit", "abort", "this", "nullptr", "NULL", "TEXT", "L",
            "_T", "decltype", "alignof", "offsetof", "va_start", "va_end", "va_arg", "winrt", "Windows"}
_CLASSES = ("class_specifier", "struct_specifier", "union_specifier")
CPP_EXTS = (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".inl", ".tpp", ".h")


def is_comment(node) -> bool:
    return node.type == "comment"


class _C:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        self.scope(root.named_children, [], None)

    def scope(self, nodes, ns: List[str], cls: Optional[Decl]) -> None:
        for node in nodes:
            kind = node.type
            if kind == "preproc_include":
                path = node.child_by_field_name("path")
                if path is not None:
                    raw = self.t(path)
                    self.facts.imports.append(Import(line=line_of(node), target=raw.strip('"<>'),
                                                     static=raw.startswith("<")))  # static = a system header
            elif kind in ("preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef"):
                self.scope(node.named_children, ns, cls)
            elif kind == "namespace_definition":
                name = node.child_by_field_name("name")
                body = node.child_by_field_name("body")
                inner = ns + (self.t(name).split("::") if name is not None else [])
                self.scope(body.named_children if body is not None else [], inner, cls)
            elif kind == "linkage_specification":
                body = node.child_by_field_name("body")
                self.scope(body.named_children if body is not None and body.type == "declaration_list"
                           else ([body] if body is not None else []), ns, cls)
            elif kind == "template_declaration":
                self.scope([c for c in node.named_children if c.type != "template_parameter_list"], ns, cls)
            elif kind == "using_declaration" and "namespace" in self.t(node):
                target = next((c for c in node.named_children if c.type in ("identifier", "qualified_identifier",
                                                                            "namespace_identifier")), None)
                if target is not None:
                    self.facts.imports.append(Import(line=line_of(node), target=self.t(target), wildcard=True))
            elif kind in _CLASSES:
                self.class_decl(node, ns, cls)
            elif kind in ("declaration", "type_definition", "field_declaration"):
                spec = next((c for c in node.named_children if c.type in _CLASSES), None)
                if spec is not None and spec.child_by_field_name("body") is not None:
                    self.class_decl(spec, ns, cls, doc_node=node)
                if kind == "declaration" and cls is None:
                    self.prototype(node, ns)
            elif kind == "function_definition":
                self.function(node, ns, cls)

    def class_decl(self, node, ns: List[str], parent: Optional[Decl], doc_node=None) -> None:
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name is None or body is None:
            return
        decl = Decl(name=base_type_name(self.t(name).split("::")[-1]), kind="class", line_start=line_of(node),
                    line_end=end_line_of(node), parent=parent, namespace="::".join(ns) or None,
                    doc=preceding_comment(doc_node or node, self.src, is_comment))
        for clause in node.named_children:
            if clause.type == "base_class_clause":
                for tnode in clause.named_children:
                    if tnode.type in ("type_identifier", "qualified_identifier", "template_type"):
                        decl.bases.append((_type_text(self, tnode), line_of(tnode)))
        self.facts.decls.append(decl)
        for member in body.named_children:
            kind = member.type
            if kind == "template_declaration":
                member = next((c for c in member.named_children if c.type != "template_parameter_list"), member)
                kind = member.type
            if kind == "function_definition":
                self.function(member, ns, decl)
            elif kind in ("field_declaration", "declaration"):
                spec = next((c for c in member.named_children if c.type in _CLASSES), None)
                if spec is not None and spec.child_by_field_name("body") is not None:
                    self.class_decl(spec, ns, decl, doc_node=member)
                    continue
                fdecl = _function_declarator(member.child_by_field_name("declarator"))
                if fdecl is not None:  # a method declared here, maybe defined in a .cpp
                    self.method_declaration(member, fdecl, decl)
                else:
                    ftype = member.child_by_field_name("type")
                    for d in member.named_children:
                        ident = _declarator_name(self, d) if d.type in ("field_identifier", "pointer_declarator",
                                                                         "reference_declarator", "init_declarator",
                                                                         "array_declarator") else None
                        if ident and ftype is not None:
                            decl.field_types[ident] = _type_text(self, ftype)

    def method_declaration(self, node, fdecl, cls: Decl) -> None:
        name = _name_of(self, fdecl.child_by_field_name("declarator"))
        if not name:
            return
        params = fdecl.child_by_field_name("parameters")
        rtype = node.child_by_field_name("type")
        sig = self.t(params) if params is not None else "()"
        if rtype is not None:
            sig += ": " + self.t(rtype)
        self.facts.decls.append(Decl(
            name=name, kind="method", line_start=line_of(node), line_end=end_line_of(node), parent=cls,
            signature=squash(sig), doc=preceding_comment(node, self.src, is_comment), declaration_only=True,
            returns=_type_text(self, rtype) if rtype is not None else None))

    def prototype(self, node, ns: List[str]) -> None:
        fdecl = _function_declarator(node.child_by_field_name("declarator"))
        if fdecl is None:
            return
        inner = fdecl.child_by_field_name("declarator")
        if inner is None or inner.type not in ("identifier",):
            return
        params = fdecl.child_by_field_name("parameters")
        rtype = node.child_by_field_name("type")
        self.facts.decls.append(Decl(
            name=self.t(inner), kind="function", line_start=line_of(node), line_end=end_line_of(node),
            signature=squash((self.t(params) if params is not None else "()") + (": " + self.t(rtype) if rtype is not None else "")),
            doc=preceding_comment(node, self.src, is_comment), namespace="::".join(ns) or None, declaration_only=True,
            returns=_type_text(self, rtype) if rtype is not None else None))

    def function(self, node, ns: List[str], cls: Optional[Decl]) -> None:
        fdecl = _function_declarator(node.child_by_field_name("declarator"))
        if fdecl is None:
            return
        inner = fdecl.child_by_field_name("declarator")
        if inner is None:
            return
        receiver = None
        if inner.type == "qualified_identifier" and cls is None:  # bool Manager::Start(...) { ... }
            parts = self.t(inner).replace(" ", "").split("::")
            receiver, name = "::".join(parts[:-1]), parts[-1]
        else:
            name = _name_of(self, inner)
        if not name or (receiver is not None and not receiver):
            return
        params = fdecl.child_by_field_name("parameters")
        rtype = node.child_by_field_name("type")
        sig = self.t(params) if params is not None else "()"
        if rtype is not None:
            sig += ": " + self.t(rtype)
        decl = Decl(name=name, kind="method" if (cls is not None or receiver) else "function", line_start=line_of(node),
                    line_end=end_line_of(node), parent=cls, signature=squash(sig), namespace="::".join(ns) or None,
                    doc=preceding_comment(node, self.src, is_comment), receiver=receiver,
                    returns=_type_text(self, rtype) if rtype is not None and self.t(rtype) != "auto" else None)
        for p in params.named_children if params is not None else []:
            if p.type in ("parameter_declaration", "optional_parameter_declaration"):
                ptype = p.child_by_field_name("type")
                pname = _declarator_name(self, p.child_by_field_name("declarator"))
                if ptype is not None and pname:
                    decl.var_types[pname] = _type_text(self, ptype)
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            self.locals_and_calls(body, decl)

    def locals_and_calls(self, body, decl: Decl) -> None:
        for node in walk(body):
            kind = node.type
            if kind == "declaration":
                vtype = node.child_by_field_name("type")
                tname = _type_text(self, vtype) if vtype is not None else ""
                for d in node.named_children:
                    if d.type == "function_declarator":  # `Session s(cfg);` constructs a Session
                        vname = _name_of(self, d.child_by_field_name("declarator"))
                        if vname and tname and tname != "auto":
                            decl.var_types[vname] = tname
                            self.facts.calls.append(Call(line=line_of(d), caller=decl, chain=tname.split("."), new=True))
                    elif d.type in ("init_declarator", "identifier", "pointer_declarator", "reference_declarator"):
                        vname = _declarator_name(self, d)
                        value = d.child_by_field_name("value") if d.type == "init_declarator" else None
                        vt = tname
                        if value is not None and value.type in ("argument_list", "initializer_list") and tname \
                                and tname != "auto":  # Foo x(args); / Foo x{args}; constructs a Foo
                            self.facts.calls.append(Call(line=line_of(d), caller=decl, chain=tname.split("."), new=True))
                        elif (not vt or vt == "auto") and value is not None:
                            vt = self.value_type(value)
                        if vname and vt and vt != "auto":
                            decl.var_types[vname] = vt
            elif kind == "call_expression":
                chain = self.chain(node.child_by_field_name("function"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=decl, chain=chain))
            elif kind == "new_expression":
                tnode = node.child_by_field_name("type")
                if tnode is not None:
                    self.facts.calls.append(Call(line=line_of(node), caller=decl,
                                                 chain=_type_text(self, tnode).split("."), new=True))

    def value_type(self, value) -> str:
        if value.type == "new_expression":
            tnode = value.child_by_field_name("type")
            return _type_text(self, tnode) if tnode is not None else ""
        if value.type == "call_expression":
            chain = self.chain(value.child_by_field_name("function"))
            if chain and len(chain) == 1 and chain[0][:1].isupper():
                return chain[0]
            return RESULT + ".".join(chain) if chain else ""
        return ""

    def chain(self, node) -> Optional[List[str]]:
        if node is None:
            return None
        kind = node.type
        if kind in ("identifier", "field_identifier", "type_identifier", "namespace_identifier"):
            return [self.t(node)]
        if kind == "this":
            return ["this"]
        if kind == "field_expression":
            head = self.chain(node.child_by_field_name("argument"))
            field = node.child_by_field_name("field")
            return head + [self.t(field)] if head and field is not None else None
        if kind == "qualified_identifier":
            return [p for p in self.t(node).replace(" ", "").split("::") if p] or None
        if kind == "template_function":
            return self.chain(node.child_by_field_name("name"))
        if kind == "parenthesized_expression" and node.named_children:
            return self.chain(node.named_children[0])
        return None


def _function_declarator(node):
    while node is not None and node.type in ("pointer_declarator", "reference_declarator", "attributed_declarator"):
        node = node.child_by_field_name("declarator") or (node.named_children[-1] if node.named_children else None)
    return node if node is not None and node.type == "function_declarator" else None


def _name_of(ex: _C, node) -> Optional[str]:
    if node is None:
        return None
    if node.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
        return ex.t(node)
    if node.type == "qualified_identifier":
        return ex.t(node).replace(" ", "").split("::")[-1]
    return None


def _declarator_name(ex: _C, node) -> Optional[str]:
    while node is not None and node.type not in ("identifier", "field_identifier"):
        nxt = node.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in node.named_children if c.type in ("identifier", "field_identifier",
                                                                     "pointer_declarator", "reference_declarator")), None)
        node = nxt
    return ex.t(node) if node is not None else None


def _type_text(ex: _C, node) -> str:
    """`const vpn::Profile&` -> `vpn.Profile`; `std::unique_ptr<Tunnel>` -> `Tunnel`; primitives -> ""."""
    if node is None or node.type in ("primitive_type", "sized_type_specifier"):
        return ""
    if node.type == "template_type":
        name = ex.t(node.child_by_field_name("name"))
        if name.split("::")[-1] in ("unique_ptr", "shared_ptr", "weak_ptr", "com_ptr", "ComPtr", "optional"):
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            inner = first.child_by_field_name("type") if first is not None and first.type == "type_descriptor" else first
            return _type_text(ex, inner) if inner is not None else ""
        return base_type_name(name.replace("::", "."))
    if node.type in ("type_identifier", "qualified_identifier", "struct_specifier", "class_specifier"):
        name = node.child_by_field_name("name") if node.type in ("struct_specifier", "class_specifier") else node
        return base_type_name(ex.t(name).replace("::", ".")) if name is not None else ""
    if node.type == "placeholder_type_specifier":
        return "auto"
    return ""


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    grammar = "c" if ext == ".c" else "cpp"
    tree = parser_for(grammar).parse(src)
    facts = FileFacts(rel_path=rel_path, language="c" if grammar == "c" else "cpp",
                      line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _C(src, facts).run(tree.root_node)
    return facts
