"""C# -> FileFacts (namespaces, block and file-scoped; using, using static, global using, aliases)."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of, parser_for,
                             preceding_comment, result_type_name, squash, text, walk)

BUILTINS = {"nameof", "typeof", "sizeof", "default", "base", "this", "var", "dynamic", "object", "string", "int",
            "long", "bool", "double", "decimal", "float", "char", "byte", "Console", "Math", "Task", "String",
            "Convert", "Guid", "DateTime", "TimeSpan", "Enumerable", "List", "Dictionary", "Exception",
            "ArgumentException", "ArgumentNullException", "InvalidOperationException", "NotImplementedException",
            "Array", "Object", "Environment", "Path", "File", "Directory"}
_TYPES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration",
          "record_struct_declaration", "enum_declaration"}


def is_comment(node) -> bool:
    return node.type == "comment"


class _CSharp:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        self.members(root.named_children, None, None)

    def members(self, nodes, namespace: Optional[str], parent: Optional[Decl]) -> None:
        current_ns = namespace
        for node in nodes:
            kind = node.type
            if kind == "using_directive":
                self.using(node)
            elif kind == "global_attribute_list" or kind == "extern_alias_directive":
                continue
            elif kind == "namespace_declaration":
                name = self.t(node.child_by_field_name("name"))
                ns = f"{namespace}.{name}" if namespace else name
                self.facts.namespace = self.facts.namespace or ns
                body = node.child_by_field_name("body")
                self.members(body.named_children if body is not None else [], ns, None)
            elif kind == "file_scoped_namespace_declaration":
                current_ns = self.t(node.child_by_field_name("name"))
                self.facts.namespace = self.facts.namespace or current_ns
                self.members(node.named_children, current_ns, None)  # newer grammars nest the members
            elif kind in _TYPES:
                self.type_decl(node, current_ns, parent)
            elif kind == "global_statement":  # top-level statements (Program.cs)
                self.locals(node, self.facts.var_types)
                self.calls(node, None)

    def using(self, node) -> None:
        raw = self.t(node)
        name_node = node.child_by_field_name("name")
        alias_at = (name_node.start_byte, name_node.end_byte) if name_node is not None else None
        target = next((c for c in node.named_children if c.type in ("qualified_name", "identifier", "generic_name")
                       and (c.start_byte, c.end_byte) != alias_at), None)
        if target is None:
            return
        self.facts.imports.append(Import(
            line=line_of(node), target=self.t(target), alias=self.t(name_node) if name_node is not None else None,
            wildcard=name_node is None, static=" static " in f" {raw} ", is_global=raw.lstrip().startswith("global")))

    def type_decl(self, node, namespace: Optional[str], parent: Optional[Decl]) -> None:
        name = node.child_by_field_name("name")
        if name is None:
            return
        decl = Decl(name=self.t(name), kind="class", line_start=line_of(node), line_end=end_line_of(node),
                    parent=parent, doc=preceding_comment(node, self.src, is_comment), namespace=namespace,
                    exported="private" not in self.modifiers(node), interface=node.type == "interface_declaration")
        for child in node.named_children:
            if child.type == "base_list":
                for base in child.named_children:
                    tnode = base.named_children[0] if base.type == "primary_constructor_base_type" and base.named_children else base
                    if tnode.type in ("identifier", "qualified_name", "generic_name"):
                        decl.bases.append((base_type_name(self.t(tnode)), line_of(tnode)))
            elif child.type == "parameter_list":  # record / primary constructor parameters
                for p in child.named_children:
                    ptype, pname = p.child_by_field_name("type"), p.child_by_field_name("name")
                    if ptype is not None and pname is not None:
                        decl.field_types[self.t(pname)] = base_type_name(self.t(ptype))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            kind = member.type
            if kind in _TYPES:
                self.type_decl(member, namespace, decl)
            elif kind in ("method_declaration", "constructor_declaration", "operator_declaration",
                          "local_function_statement"):
                self.method(member, decl)
            elif kind == "property_declaration":
                ptype, pname = member.child_by_field_name("type"), member.child_by_field_name("name")
                if ptype is not None and pname is not None:
                    decl.field_types[self.t(pname)] = base_type_name(self.t(ptype))
                self.calls(member, decl)
            elif kind == "field_declaration":
                var = next((c for c in member.named_children if c.type == "variable_declaration"), None)
                ftype = var.child_by_field_name("type") if var is not None else None
                for declarator in var.named_children if var is not None else []:
                    if declarator.type == "variable_declarator" and ftype is not None:
                        fname = declarator.child_by_field_name("name") or next(
                            (c for c in declarator.named_children if c.type == "identifier"), None)
                        if fname is not None:
                            decl.field_types[self.t(fname)] = base_type_name(self.t(ftype))
                self.calls(member, decl)

    def modifiers(self, node) -> str:
        return " ".join(self.t(c) for c in node.named_children if c.type == "modifier")

    def method(self, node, cls: Decl) -> None:
        name = node.child_by_field_name("name")
        params = node.child_by_field_name("parameters")
        returns = node.child_by_field_name("returns") or node.child_by_field_name("type")
        sig = self.t(params) if params is not None else "()"
        if returns is not None:
            sig += ": " + self.t(returns)
        decl = Decl(name=self.t(name) if name is not None else cls.name, kind="method", line_start=line_of(node),
                    line_end=end_line_of(node), parent=cls, signature=squash(sig),
                    doc=preceding_comment(node, self.src, is_comment), exported="private" not in self.modifiers(node),
                    returns=result_type_name(self.t(returns)) if returns is not None else None)
        for p in params.named_children if params is not None else []:
            ptype, pname = p.child_by_field_name("type"), p.child_by_field_name("name")
            if ptype is not None and pname is not None:
                decl.var_types[self.t(pname)] = base_type_name(self.t(ptype))
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            self.locals(body, decl.var_types)
            self.calls(body, decl)

    def locals(self, root, types) -> None:
        for local in walk(root):
            if local.type != "variable_declaration":
                continue
            vtype = local.child_by_field_name("type")
            for declarator in local.named_children:
                if declarator.type != "variable_declarator":
                    continue
                vname = declarator.child_by_field_name("name") or next(
                    (c for c in declarator.named_children if c.type == "identifier"), None)
                tname = base_type_name(self.t(vtype)) if vtype is not None else ""
                if tname == "var":
                    value = next((c for c in walk(declarator) if c.type in ("object_creation_expression",
                                                                             "invocation_expression")), None)
                    if value is not None and value.type == "object_creation_expression":
                        tname = base_type_name(self.t(value.child_by_field_name("type")))
                    elif value is not None:
                        chain = self.chain(value.child_by_field_name("function"))
                        tname = RESULT + ".".join(chain) if chain else ""
                    else:
                        tname = ""
                if vname is not None and tname and tname != "var":
                    types[self.t(vname)] = tname

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            if node.type == "invocation_expression":
                chain = self.chain(node.child_by_field_name("function"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain))
            elif node.type == "object_creation_expression":
                tnode = node.child_by_field_name("type")
                if tnode is not None:
                    chain = base_type_name(self.t(tnode)).split(".")
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))

    def chain(self, node) -> Optional[List[str]]:
        if node is None:
            return None
        if node.type == "identifier":
            return [self.t(node)]
        if node.type == "generic_name":
            ident = next((c for c in node.named_children if c.type == "identifier"), None)
            return [self.t(ident)] if ident is not None else None
        if node.type in ("this_expression", "this"):
            return ["this"]
        if node.type in ("base_expression", "base"):
            return ["super"]
        if node.type == "member_access_expression":
            head = self.chain(node.child_by_field_name("expression"))
            name = self.chain(node.child_by_field_name("name"))
            return head + name if head and name else None
        if node.type == "qualified_name":
            return self.t(node).split(".")
        return None


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    tree = parser_for("csharp").parse(src)
    facts = FileFacts(rel_path=rel_path, language="csharp", line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _CSharp(src, facts).run(tree.root_node)
    return facts
