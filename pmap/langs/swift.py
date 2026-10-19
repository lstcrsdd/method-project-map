"""Swift -> FileFacts. Files of one module see each other without imports; extensions add methods to a type."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of, parser_for,
                             preceding_comment, squash, text, walk)

BUILTINS = {"print", "debugPrint", "fatalError", "precondition", "assert", "assertionFailure", "min", "max", "abs",
            "zip", "stride", "type", "String", "Int", "Double", "Float", "Bool", "Array", "Dictionary", "Set",
            "Optional", "Data", "Date", "URL", "UUID", "DispatchQueue", "NotificationCenter", "UserDefaults",
            "Task", "Result", "Error", "self", "super", "Self"}
_TYPES = ("class_declaration", "protocol_declaration")


def is_comment(node) -> bool:
    return node.type in ("comment", "multiline_comment")


class _Swift:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        for node in root.named_children:
            kind = node.type
            if kind == "import_declaration":
                ident = next((c for c in node.named_children if c.type == "identifier"), None)
                if ident is not None:
                    self.facts.imports.append(Import(line=line_of(node), target=self.t(ident).replace(" ", "")))
            elif kind in _TYPES:
                self.type_decl(node, None)
            elif kind == "function_declaration":
                self.function(node, None)
            elif kind == "property_declaration":  # top-level code (main.swift): its variables keep their types
                vname, vtype = self.binding(node)
                if vname and vtype:
                    self.facts.var_types[vname] = vtype
                self.calls(node, None)
            elif not is_comment(node):  # other top-level statements (main.swift, scripts)
                self.calls(node, None)

    def keyword(self, node) -> str:
        for child in node.children:
            if not child.is_named and self.t(child) in ("class", "struct", "enum", "extension", "actor", "protocol"):
                return self.t(child)
        return "class"

    def type_decl(self, node, parent: Optional[Decl]) -> None:
        kw = "protocol" if node.type == "protocol_declaration" else self.keyword(node)
        name = node.child_by_field_name("name")
        if name is None:
            return
        type_name = base_type_name(self.t(name))
        decl = Decl(name=f"extension {type_name}" if kw == "extension" else type_name, kind="class",
                    line_start=line_of(node), line_end=end_line_of(node), parent=parent,
                    doc=preceding_comment(node, self.src, is_comment), interface=kw == "protocol",
                    receiver=type_name if kw == "extension" else None,
                    exported="private" not in self.modifiers(node))
        for spec in node.named_children:
            if spec.type == "inheritance_specifier":
                ut = next((c for c in walk(spec) if c.type == "user_type"), None)
                if ut is not None:
                    decl.bases.append((base_type_name(self.t(ut)), line_of(ut)))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if kw == "enum" and body is not None:
            decl.variants = [self.t(n) for e in body.named_children if e.type == "enum_entry"
                             for n in e.named_children if n.type == "simple_identifier"]
        for member in body.named_children if body is not None else []:
            kind = member.type
            if kind in _TYPES:
                self.type_decl(member, decl)
            elif kind in ("function_declaration", "init_declaration", "protocol_function_declaration"):
                self.function(member, decl)
            elif kind == "property_declaration":
                pname, ptype = self.binding(member)
                if pname and ptype:
                    decl.field_types[pname] = ptype
                self.calls(member, decl)

    def modifiers(self, node) -> str:
        mods = next((c for c in node.named_children if c.type == "modifiers"), None)
        return self.t(mods) if mods is not None else ""

    def binding(self, node):
        """`let x: T = ...` / `var x = T(...)` -> ("x", "T")."""
        pat = node.child_by_field_name("name")
        ident = next((c for c in walk(pat) if c.type == "simple_identifier"), None) if pat is not None else None
        ann = next((c for c in node.named_children if c.type == "type_annotation"), None)
        tname = ""
        if ann is not None:
            ut = next((c for c in walk(ann) if c.type == "user_type"), None)
            tname = base_type_name(self.t(ut)) if ut is not None else "?"
        else:
            value = node.child_by_field_name("value")
            tname = self.value_type(value) if value is not None else ""
        return (self.t(ident) if ident is not None else None), tname

    def value_type(self, value) -> str:
        while value.type in ("try_expression", "await_expression") and value.named_children:
            value = value.named_children[-1]
        if value.type == "call_expression" and value.named_children:
            chain = self.chain(value.named_children[0])
            if chain and len(chain) == 1 and chain[0][:1].isupper():
                return chain[0]  # `Foo(...)`: an initializer
            return RESULT + ".".join(chain) if chain else ""
        return ""

    def function(self, node, cls: Optional[Decl]) -> None:
        if node.type == "init_declaration":
            fname = "init"
        else:
            name = node.child_by_field_name("name")
            if name is None:
                return
            fname = self.t(name)
        params = [c for c in node.named_children if c.type == "parameter"]
        ret = None
        seen = False
        for child in node.named_children:  # the return type is the type node after the parameters
            if child.type == "parameter":
                seen = True
            elif seen and child.type in ("user_type", "optional_type", "array_type", "dictionary_type", "tuple_type"):
                ret = child
                break
        sig = "(" + ", ".join(self.t(p) for p in params) + ")"
        if ret is not None:
            sig += " -> " + self.t(ret)
        returns = None
        if ret is not None:
            ut = next((c for c in walk(ret) if c.type == "user_type"), None)
            returns = base_type_name(self.t(ut)) if ut is not None else None
        decl = Decl(name=fname, kind="method" if cls is not None else "function", line_start=line_of(node),
                    line_end=end_line_of(node), parent=cls, signature=squash(sig), returns=returns,
                    doc=preceding_comment(node, self.src, is_comment), exported="private" not in self.modifiers(node))
        for p in params:
            idents = [c for c in p.named_children if c.type == "simple_identifier"]
            ut = next((c for c in walk(p) if c.type == "user_type"), None)
            if idents and ut is not None:
                decl.var_types[self.t(idents[-1])] = base_type_name(self.t(ut))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            for local in walk(body):
                if local.type == "property_declaration":
                    lname, ltype = self.binding(local)
                    if lname and ltype:
                        decl.var_types[lname] = ltype
            self.calls(body, decl)

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            if node.type == "call_expression" and node.named_children:
                chain = self.chain(node.named_children[0])
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain,
                                                 new=len(chain) == 1 and chain[0][:1].isupper()))

    def chain(self, node) -> Optional[List[str]]:
        kind = node.type
        if kind in ("simple_identifier", "type_identifier"):
            return [self.t(node)]
        if kind == "self_expression":
            return ["self"]
        if kind == "super_expression":
            return ["super"]
        if kind == "user_type":
            return base_type_name(self.t(node)).split(".")
        if kind == "navigation_expression":
            target = node.child_by_field_name("target")
            suffix = node.child_by_field_name("suffix")
            head = self.chain(target) if target is not None else None
            name = next((c for c in walk(suffix) if c.type == "simple_identifier"), None) if suffix is not None else None
            return head + [self.t(name)] if head and name is not None else None
        return None


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    tree = parser_for("swift").parse(src)
    facts = FileFacts(rel_path=rel_path, language="swift", line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _Swift(src, facts).run(tree.root_node)
    return facts
