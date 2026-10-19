"""Java and Kotlin -> FileFacts. Both share packages, so one project may mix them freely."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of, parser_for,
                             preceding_comment, result_type_name, squash, text, walk)

# java.lang and the Kotlin standard library are imported implicitly
BUILTINS = {
    "Object", "String", "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short", "Math",
    "System", "Thread", "Runnable", "Exception", "RuntimeException", "Error", "Throwable", "Iterable", "Comparable",
    "StringBuilder", "Enum", "Record", "Number", "Void", "Class", "IllegalArgumentException",
    "IllegalStateException", "NullPointerException", "UnsupportedOperationException", "Override", "Deprecated",
    "println", "print", "listOf", "mutableListOf", "mapOf", "mutableMapOf", "setOf", "mutableSetOf", "emptyList",
    "emptyMap", "emptySet", "arrayOf", "require", "requireNotNull", "check", "checkNotNull", "error", "TODO",
    "lazy", "run", "let", "also", "apply", "with", "repeat", "maxOf", "minOf", "Pair", "Triple", "Any", "Unit",
    "Int", "Unit", "List", "Map", "Set", "MutableList", "MutableMap", "Array", "Sequence", "buildList", "buildString",
    "super", "this",
}
_JAVA_TYPES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}


def is_comment(node) -> bool:
    return node.type in ("comment", "line_comment", "block_comment")


# ====================================================================== Java


class _Java:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        for node in root.named_children:
            if node.type == "package_declaration":
                name = next((c for c in node.named_children if c.type in ("scoped_identifier", "identifier")), None)
                self.facts.namespace = self.t(name) if name is not None else None
                self.facts.doc = preceding_comment(node, self.src, is_comment)
            elif node.type == "import_declaration":
                name = next((c for c in node.named_children if c.type in ("scoped_identifier", "identifier")), None)
                if name is None:
                    continue
                raw = self.t(node)
                self.facts.imports.append(Import(line=line_of(node), target=self.t(name),
                                                 wildcard=any(c.type == "asterisk" for c in node.named_children),
                                                 static=" static " in f" {raw} "))
            elif node.type in _JAVA_TYPES:
                self.type_decl(node, None)

    def type_decl(self, node, parent: Optional[Decl]) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        decl = Decl(name=self.t(name), kind="class", line_start=line_of(node), line_end=end_line_of(node),
                    parent=parent, doc=preceding_comment(node, self.src, is_comment), namespace=self.facts.namespace,
                    exported="private" not in self.modifiers(node), interface=node.type == "interface_declaration")
        for field in ("superclass", "interfaces"):
            holder = node.child_by_field_name(field)
            for tnode in walk(holder) if holder is not None else []:
                if tnode.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    decl.bases.append((base_type_name(self.t(tnode)), line_of(tnode)))
                    if tnode.type == "generic_type":
                        break
        for ext in node.named_children:
            if ext.type == "extends_interfaces":
                for tnode in walk(ext):
                    if tnode.type in ("type_identifier", "scoped_type_identifier"):
                        decl.bases.append((base_type_name(self.t(tnode)), line_of(tnode)))
        self.facts.decls.append(decl)
        if node.type == "record_declaration":
            params = node.child_by_field_name("parameters")
            for p in params.named_children if params is not None else []:
                ptype, pname = p.child_by_field_name("type"), p.child_by_field_name("name")
                if ptype is not None and pname is not None:
                    decl.field_types[self.t(pname)] = base_type_name(self.t(ptype))
        body = node.child_by_field_name("body")
        for member in _members(body):
            kind = member.type
            if kind in _JAVA_TYPES:
                self.type_decl(member, decl)
            elif kind in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
                self.method(member, decl)
            elif kind == "field_declaration":
                ftype = member.child_by_field_name("type")
                for declarator in member.named_children:
                    if declarator.type == "variable_declarator" and ftype is not None:
                        fname = declarator.child_by_field_name("name")
                        decl.field_types[self.t(fname)] = base_type_name(self.t(ftype))
                        value = declarator.child_by_field_name("value")
                        if value is not None:
                            self.calls(value, decl)
            elif kind == "enum_constant":
                self.calls(member, decl)

    def modifiers(self, node) -> str:
        mods = next((c for c in node.children if c.type == "modifiers"), None)
        return self.t(mods) if mods is not None else ""

    def method(self, node, cls: Decl) -> None:
        name = node.child_by_field_name("name")
        mname = self.t(name) if name is not None else cls.name
        params = node.child_by_field_name("parameters")
        rtype = node.child_by_field_name("type")
        sig = self.t(params) if params is not None else "()"
        if rtype is not None:
            sig += ": " + self.t(rtype)
        decl = Decl(name=mname, kind="method", line_start=line_of(node), line_end=end_line_of(node), parent=cls,
                    signature=squash(sig), doc=preceding_comment(node, self.src, is_comment),
                    exported="private" not in self.modifiers(node),
                    returns=result_type_name(self.t(rtype)) if rtype is not None else None)
        for p in params.named_children if params is not None else []:
            ptype, pname = p.child_by_field_name("type"), p.child_by_field_name("name")
            if ptype is not None and pname is not None:
                decl.var_types[self.t(pname)] = base_type_name(self.t(ptype))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            for local in walk(body):
                if local.type == "local_variable_declaration":
                    ltype = local.child_by_field_name("type")
                    for declarator in local.named_children:
                        if declarator.type != "variable_declarator":
                            continue
                        lname = declarator.child_by_field_name("name")
                        value = declarator.child_by_field_name("value")
                        tname = base_type_name(self.t(ltype)) if ltype is not None else ""
                        if tname == "var" and value is not None and value.type == "object_creation_expression":
                            tname = base_type_name(self.t(value.child_by_field_name("type")))
                        elif tname == "var" and value is not None and value.type == "method_invocation":
                            obj, mname_node = value.child_by_field_name("object"), value.child_by_field_name("name")
                            head = self.chain(obj) if obj is not None else []
                            tname = RESULT + ".".join(head + [self.t(mname_node)]) if head is not None else ""
                        if lname is not None and tname and tname != "var":
                            decl.var_types[self.t(lname)] = tname
            self.calls(body, decl)

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            if node.type == "method_invocation":
                name = node.child_by_field_name("name")
                obj = node.child_by_field_name("object")
                head = self.chain(obj) if obj is not None else []
                if head is not None and name is not None:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=head + [self.t(name)]))
            elif node.type == "object_creation_expression":
                tnode = node.child_by_field_name("type")
                chain = base_type_name(self.t(tnode)).split(".") if tnode is not None else None
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))

    def chain(self, node) -> Optional[List[str]]:
        if node.type in ("identifier", "type_identifier"):
            return [self.t(node)]
        if node.type in ("this", "super"):
            return [node.type]
        if node.type == "field_access":
            head = self.chain(node.child_by_field_name("object"))
            field = node.child_by_field_name("field")
            return head + [self.t(field)] if head and field is not None else None
        if node.type == "scoped_identifier":
            return self.t(node).split(".")
        return None


def _members(body):
    if body is None:
        return []
    out = []
    for child in body.named_children:
        if child.type == "enum_body_declarations":
            out.extend(child.named_children)
        else:
            out.append(child)
    return out


# ====================================================================== Kotlin


class _Kotlin:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        for node in root.named_children:
            kind = node.type
            if kind == "package_header":
                qi = next((c for c in node.named_children if c.type == "qualified_identifier"), None)
                self.facts.namespace = self.t(qi).replace(" ", "") if qi is not None else None
                self.facts.doc = preceding_comment(node, self.src, is_comment)
            elif kind == "import":
                qi = next((c for c in node.named_children if c.type == "qualified_identifier"), None)
                if qi is None:
                    continue
                alias = [c for c in node.named_children if c.type == "identifier"]
                raw = self.t(node).replace(" ", "")
                self.facts.imports.append(Import(line=line_of(node), target=self.t(qi).replace(" ", ""),
                                                 wildcard=raw.rstrip(";").endswith(".*"),
                                                 alias=self.t(alias[-1]) if alias else None))
            elif kind in ("class_declaration", "object_declaration"):
                self.type_decl(node, None)
            elif kind == "function_declaration":
                self.function(node, None)
            elif kind == "property_declaration":
                self.calls(node, None)

    def type_decl(self, node, parent: Optional[Decl], fold_into: Optional[Decl] = None) -> None:
        name = node.child_by_field_name("name")
        if fold_into is not None:  # companion object: its members are the class's "static" members
            decl = fold_into
        elif name is None:
            return
        else:
            decl = Decl(name=self.t(name), kind="class", line_start=line_of(node), line_end=end_line_of(node),
                        parent=parent, doc=preceding_comment(node, self.src, is_comment),
                        namespace=self.facts.namespace, exported="private" not in self.modifiers(node),
                        interface=any(not c.is_named and self.t(c) == "interface" for c in node.children))
            self.facts.decls.append(decl)
        for child in node.named_children:
            if child.type == "delegation_specifiers":
                for spec in child.named_children:
                    ut = next((n for n in walk(spec) if n.type == "user_type"), None)
                    if ut is not None:
                        decl.bases.append((base_type_name(self.t(ut)), line_of(ut)))
            elif child.type == "primary_constructor":
                for param in walk(child):
                    if param.type == "class_parameter":
                        ident = next((c for c in param.named_children if c.type == "identifier"), None)
                        ut = next((c for c in param.named_children if c.type in ("user_type", "nullable_type")), None)
                        if ident is not None and ut is not None:
                            decl.field_types[self.t(ident)] = base_type_name(self.t(ut))
            elif child.type in ("class_body", "enum_class_body"):
                for member in child.named_children:
                    if member.type == "function_declaration":
                        self.function(member, decl)
                    elif member.type in ("class_declaration", "object_declaration"):
                        self.type_decl(member, decl)
                    elif member.type == "companion_object":
                        self.type_decl(member, parent, fold_into=decl)
                    elif member.type == "property_declaration":
                        self.property(member, decl)
                        self.calls(member, decl)
                    elif member.type in ("anonymous_initializer", "secondary_constructor"):
                        self.calls(member, decl)

    def modifiers(self, node) -> str:
        mods = next((c for c in node.named_children if c.type == "modifiers"), None)
        return self.t(mods) if mods is not None else ""

    def property(self, node, cls: Decl) -> None:
        var = next((c for c in node.named_children if c.type == "variable_declaration"), None)
        if var is None:
            return
        ident = next((c for c in var.named_children if c.type == "identifier"), None)
        ut = next((c for c in var.named_children if c.type in ("user_type", "nullable_type")), None)
        tname = base_type_name(self.t(ut)) if ut is not None else self.constructed_type(node)
        if ident is not None and tname:
            cls.field_types[self.t(ident)] = tname

    def constructed_type(self, node) -> str:
        """`val x = Invoice(...)`: a capitalised callee is (almost always) a constructor; `val x = f()` is f's result."""
        call = next((c for c in node.named_children if c.type == "call_expression"), None)
        callee = call.named_children[0] if call is not None and call.named_children else None
        if callee is not None and callee.type == "identifier" and self.t(callee)[:1].isupper():
            return self.t(callee)
        chain = self.chain(callee) if callee is not None else None
        return RESULT + ".".join(chain) if chain else ""

    def function(self, node, cls: Optional[Decl]) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        params = next((c for c in node.named_children if c.type == "function_value_parameters"), None)
        sig = self.t(params) if params is not None else "()"
        ret = None
        seen_params = False
        for child in node.named_children:
            if child.type == "function_value_parameters":
                seen_params = True
            elif seen_params and child.type in ("user_type", "nullable_type", "function_type"):
                ret = child
                break
        if ret is not None:
            sig += ": " + self.t(ret)
        decl = Decl(name=self.t(name), kind="method" if cls is not None else "function", line_start=line_of(node),
                    line_end=end_line_of(node), parent=cls, signature=squash(sig),
                    doc=preceding_comment(node, self.src, is_comment), namespace=self.facts.namespace,
                    exported="private" not in self.modifiers(node),
                    returns=result_type_name(self.t(ret)) if ret is not None else None)
        for param in params.named_children if params is not None else []:
            ident = next((c for c in param.named_children if c.type == "identifier"), None)
            ut = next((c for c in param.named_children if c.type in ("user_type", "nullable_type")), None)
            if ident is not None and ut is not None:
                decl.var_types[self.t(ident)] = base_type_name(self.t(ut))
        self.facts.decls.append(decl)
        body = next((c for c in node.named_children if c.type == "function_body"), None)
        if body is not None:
            for local in walk(body):
                if local.type == "property_declaration":
                    var = next((c for c in local.named_children if c.type == "variable_declaration"), None)
                    ident = next((c for c in var.named_children if c.type == "identifier"), None) if var else None
                    ut = next((c for c in var.named_children if c.type in ("user_type", "nullable_type")), None) if var else None
                    tname = base_type_name(self.t(ut)) if ut is not None else self.constructed_type(local)
                    if ident is not None and tname:
                        decl.var_types[self.t(ident)] = tname
            self.calls(body, decl)

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            if node.type == "call_expression" and node.named_children:
                chain = self.chain(node.named_children[0])
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain,
                                                 new=len(chain) == 1 and chain[0][:1].isupper()))

    def chain(self, node) -> Optional[List[str]]:
        if node.type == "identifier":
            return [self.t(node)]
        if node.type == "this_expression":
            return ["this"]
        if node.type == "super_expression":
            return ["super"]
        if node.type == "navigation_expression":
            parts = node.named_children
            if len(parts) < 2:
                return None
            head = self.chain(parts[0])
            last = parts[-1]
            name = next((c for c in walk(last) if c.type == "identifier"), None) if last.type != "identifier" else last
            return head + [self.t(name)] if head and name is not None else None
        return None


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    kotlin = ext in (".kt", ".kts")
    tree = parser_for("kotlin" if kotlin else "java").parse(src)
    facts = FileFacts(rel_path=rel_path, language="kotlin" if kotlin else "java",
                      line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    (_Kotlin if kotlin else _Java)(src, facts).run(tree.root_node)
    return facts
