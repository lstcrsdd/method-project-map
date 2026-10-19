"""Dart / Flutter -> FileFacts. Calls are a primary expression followed by `.name` and `(...)` selectors."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (LOCAL, RESULT, UNTRACKED, Call, Decl, FileFacts, Import, base_type_name, doc_summary,
                             end_line_of, line_of, parser_for, squash, text, walk)

BUILTINS = {"print", "debugPrint", "identical", "List", "Map", "Set", "String", "int", "double", "num", "bool",
            "Future", "Stream", "Duration", "DateTime", "Uri", "Object", "Exception", "Error", "StateError",
            "ArgumentError", "UnimplementedError", "Iterable", "Completer", "Timer", "Random", "super", "this",
            "setState", "context", "widget", "mounted"}
_CLASSES = ("class_definition", "mixin_declaration", "extension_declaration", "enum_declaration",
            "extension_type_declaration")
_SIGNATURES = ("function_signature", "getter_signature", "setter_signature", "constructor_signature",
               "constant_constructor_signature", "factory_constructor_signature", "redirecting_factory_constructor_signature",
               "operator_signature")


def is_comment(node) -> bool:
    return node.type in ("comment", "documentation_comment")


def _doc(node, src: bytes) -> Optional[str]:
    parts = []
    prev = node.prev_sibling
    while prev is not None and (prev.type in ("documentation_comment", "annotation") or
                                (prev.type == "comment" and text(prev, src).startswith("///"))):
        if prev.type != "annotation":
            parts.append(text(prev, src))
        prev = prev.prev_sibling
    return doc_summary("\n".join(reversed(parts))) if parts else None


class _Dart:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        children = root.named_children
        for i, node in enumerate(children):
            kind = node.type
            if kind == "import_or_export":
                self.directive(node)
            elif kind in ("part_directive", "part_of_directive"):
                uri = next((c for c in walk(node) if c.type == "string_literal"), None)
                if uri is not None:
                    self.facts.imports.append(Import(line=line_of(node), target=self.t(uri).strip("'\""),
                                                     static=kind == "part_of_directive", wildcard=True, alias="part"))
            elif kind in _CLASSES:
                self.class_decl(node)
            elif kind in ("function_signature", "getter_signature"):
                body = children[i + 1] if i + 1 < len(children) and children[i + 1].type == "function_body" else None
                self.function(node, body, None, doc_node=node)
            elif kind == "top_level_variable_declaration" or kind == "static_final_declaration_list":
                self.calls(node, None)

    def directive(self, node) -> None:
        for spec in walk(node):
            if spec.type not in ("import_specification", "library_export"):
                continue
            uri = next((c for c in walk(spec) if c.type == "string_literal"), None)
            if uri is None:
                continue
            imp = Import(line=line_of(spec), target=self.t(uri).strip("'\""), reexport=spec.type == "library_export")
            for child in spec.named_children:
                if child.type == "identifier":  # `as prefix`
                    imp.alias = self.t(child)
                elif child.type == "combinator":
                    show = any(not c.is_named and self.t(c) == "show" for c in child.children)
                    names = [self.t(c) for c in child.named_children if c.type == "identifier"]
                    if show:
                        imp.names.extend((n, n) for n in names)
                    else:
                        imp.static = True  # `hide`: everything but these names (kept simple: still all)
            self.facts.imports.append(imp)

    def class_decl(self, node) -> None:
        name = node.child_by_field_name("name") or next((c for c in node.named_children if c.type == "identifier"), None)
        on_type = None
        if node.type == "extension_declaration":
            on = next((c for c in node.named_children if c.type in ("type_identifier", "type")), None)
            on_type = base_type_name(self.t(on)) if on is not None else None
        if name is None and on_type is None:
            return
        cname = self.t(name) if name is not None else f"extension on {on_type}"
        decl = Decl(name=cname, kind="class", line_start=line_of(node), line_end=end_line_of(node),
                    doc=_doc(node, self.src), interface=node.type == "mixin_declaration",
                    exported=not cname.startswith("_"), receiver=on_type,
                    signature=f"extension on {on_type}" if on_type and name is not None else None)
        for field in ("superclass", "interfaces"):
            holder = node.child_by_field_name(field)
            for tnode in walk(holder) if holder is not None else []:
                if tnode.type == "type_identifier":
                    decl.bases.append((self.t(tnode), line_of(tnode)))
        for child in node.named_children:  # `mixin M on Base` / `with` lists outside the superclass node
            if child.type in ("mixins", "interfaces") and node.child_by_field_name("superclass") is None:
                for tnode in walk(child):
                    if tnode.type == "type_identifier":
                        decl.bases.append((self.t(tnode), line_of(tnode)))
        if node.type == "enum_declaration":
            decl.variants = [self.t(i) for c in walk(node) if c.type == "enum_constant"
                             for i in c.named_children if i.type == "identifier"][:200]
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body") or next((c for c in node.named_children
                                                         if c.type in ("class_body", "extension_body", "enum_body")), None)
        members = body.named_children if body is not None else []
        for i, member in enumerate(members):
            if member.type == "method_signature":
                body_node = members[i + 1] if i + 1 < len(members) and members[i + 1].type == "function_body" else None
                sig = next((c for c in member.named_children if c.type in _SIGNATURES), member)
                self.function(sig, body_node, decl, doc_node=member)
            elif member.type == "declaration":
                sig = next((c for c in member.named_children if c.type in _SIGNATURES), None)
                if sig is not None:
                    body_node = members[i + 1] if i + 1 < len(members) and members[i + 1].type == "function_body" else None
                    self.function(sig, body_node, decl, doc_node=member)
                    continue
                ftype = next((c for c in member.named_children if c.type == "type_identifier"), None)
                for ident in walk(member):
                    if ident.type in ("initialized_identifier", "static_final_declaration") and ftype is not None:
                        fname = next((c for c in ident.named_children if c.type == "identifier"), None)
                        if fname is not None:
                            decl.field_types[self.t(fname)] = self.t(ftype)
                self.calls(member, decl)

    def function(self, sig, body, cls: Optional[Decl], doc_node) -> None:
        name = sig.child_by_field_name("name") or next((c for c in sig.named_children if c.type == "identifier"), None)
        if sig.type in ("constructor_signature", "constant_constructor_signature", "factory_constructor_signature",
                        "redirecting_factory_constructor_signature"):
            idents = [self.t(c) for c in sig.named_children if c.type == "identifier"]
            fname = ".".join(idents) if idents else (cls.name if cls else "new")
        elif name is not None:
            fname = self.t(name)
        else:
            return
        params = next((c for c in sig.named_children if c.type == "formal_parameter_list"), None)
        ret = next((c for c in sig.named_children if c.type == "type_identifier"), None)
        sig_text = self.t(params) if params is not None else "()"
        if ret is not None and (name is None or ret.start_byte < name.start_byte):
            sig_text += ": " + self.t(ret)
        returns = self.t(ret) if ret is not None else None
        if returns in ("Future", "Stream"):
            args = next((c for c in walk(sig) if c.type == "type_arguments"), None)
            inner = next((c for c in walk(args) if c.type == "type_identifier"), None) if args is not None else None
            returns = self.t(inner) if inner is not None else None
        end = body if body is not None else sig
        decl = Decl(name=fname, kind="method" if cls is not None else "function", line_start=line_of(doc_node),
                    line_end=end_line_of(end), parent=cls, signature=squash(sig_text), doc=_doc(doc_node, self.src),
                    exported=not fname.startswith("_"), returns=returns)
        for p in walk(params) if params is not None else []:
            if p.type == "formal_parameter":
                ptype = next((c for c in p.named_children if c.type == "type_identifier"), None)
                pname = p.child_by_field_name("name") or next((c for c in p.named_children if c.type == "identifier"), None)
                if ptype is not None and pname is not None:
                    decl.var_types[self.t(pname)] = self.t(ptype)
        self.facts.decls.append(decl)
        if body is not None:
            self.locals(body, decl)
            self.calls(body, decl)

    def locals(self, body, decl: Decl) -> None:
        for node in walk(body):
            if node.type == "formal_parameter":  # closures: (WidgetTester tester) async { ... }
                ptype = next((c for c in node.named_children if c.type == "type_identifier"), None)
                pname = node.child_by_field_name("name") or next((c for c in node.named_children if c.type == "identifier"), None)
                if pname is not None:
                    decl.var_types.setdefault(self.t(pname), self.t(ptype) if ptype is not None else LOCAL)
                continue
            if node.type != "initialized_variable_definition":
                continue
            name = node.child_by_field_name("name")
            if name is None:
                continue
            tnode = next((c for c in node.named_children if c.type == "type_identifier" and c.start_byte < name.start_byte), None)
            if tnode is not None:
                decl.var_types[self.t(name)] = self.t(tnode)
                continue
            values = [c for c in node.named_children if c.start_byte > name.end_byte]
            if values and values[0].type in ("list_literal", "set_or_map_literal", "string_literal", "decimal_integer_literal",
                                             "decimal_floating_point_literal", "true", "false", "function_expression"):
                decl.var_types[self.t(name)] = UNTRACKED
                continue
            chain, called, after = self.sequence(values, follow=True)
            if chain and called:
                head = chain[0] if len(chain) == 1 and chain[0][:1].isupper() else None
                base = RESULT + ("type:" + head if head else ".".join(chain))
                decl.var_types[self.t(name)] = (head if head and not after else base) + "".join("|" + m for m in after)
            else:
                decl.var_types.setdefault(self.t(name), LOCAL)

    def sequence(self, nodes, follow: bool = False):
        """[identifier, selector(.a), selector(args)] -> (["x", "a"], True); with `follow`, also the methods called
        on the result afterwards: `a.b().c().d()` -> (["a", "b"], True, ["c", "d"])."""
        chain: List[str] = []
        called = False
        after: List[str] = []
        pending: Optional[str] = None
        for node in nodes:
            if not chain:
                if node.type in ("identifier", "type_identifier"):
                    chain = [self.t(node)]
                elif node.type == "this":
                    chain = ["this"]
                elif node.type == "super":
                    chain = ["super"]
                else:
                    return (None, False, []) if follow else (None, False)
                continue
            if node.type != "selector":
                break
            access = next((c for c in node.named_children if c.type in ("unconditional_assignable_selector",
                                                                         "conditional_assignable_selector")), None)
            is_call = any(c.type == "argument_part" for c in node.named_children)
            if called:  # a member of a call's result
                if not follow:
                    return (None, False, []) if follow else (None, False)
                if access is not None:
                    ident = next((c for c in access.named_children if c.type == "identifier"), None)
                    pending = self.t(ident) if ident is not None else None
                elif is_call and pending:
                    after.append(pending)
                    pending = None
                continue
            if access is not None:
                ident = next((c for c in access.named_children if c.type == "identifier"), None)
                if ident is None:
                    return (None, False, []) if follow else (None, False)
                chain.append(self.t(ident))
            elif is_call:
                called = True
        if follow:
            return (chain or None), called, after
        return (chain or None), called

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            kids = node.named_children
            for i, child in enumerate(kids):
                if child.type not in ("identifier", "type_identifier", "this", "super"):
                    continue
                if i + 1 >= len(kids) or kids[i + 1].type != "selector":
                    continue
                if i > 0 and kids[i - 1].type == "selector":
                    continue  # part of a longer chain that started earlier
                chain: List[str] = []
                j = i
                current = [child]
                while j + 1 < len(kids) and kids[j + 1].type == "selector":
                    j += 1
                    current.append(kids[j])
                    part, called = self.sequence(current)
                    if part is None:
                        break
                    if called:
                        self.facts.calls.append(Call(line=line_of(kids[j]), caller=caller, chain=part,
                                                     new=len(part) == 1 and part[0][:1].isupper()))
                        break
            if node.type in ("new_expression", "const_object_expression"):
                tnode = next((c for c in node.named_children if c.type == "type_identifier"), None)
                if tnode is not None:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=[self.t(tnode)], new=True))


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    tree = parser_for("dart").parse(src)
    facts = FileFacts(rel_path=rel_path, language="dart", line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _Dart(src, facts).run(tree.root_node)
    return facts
