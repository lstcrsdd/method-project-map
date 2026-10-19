"""Rust -> FileFacts. Module paths come from the file layout; `impl` blocks attach methods to their type."""

from __future__ import annotations

from typing import List, Optional

from pmap.langs.base import (RESULT, UNTRACKED, Call, Decl, FileFacts, Import, base_type_name, end_line_of, line_of, parser_for,
                             squash, text, walk)

BUILTINS = {"Some", "None", "Ok", "Err", "Box", "Vec", "String", "Option", "Result", "Rc", "Arc", "RefCell", "Cell",
            "HashMap", "HashSet", "BTreeMap", "BTreeSet", "Mutex", "RwLock", "Default", "Into", "From", "drop",
            "std", "core", "alloc", "format", "panic", "Self", "self", "super", "crate", "Pin", "Cow", "PhantomData",
            "u8", "u16", "u32", "u64", "u128", "usize", "i8", "i16", "i32", "i64", "i128", "isize", "f32", "f64",
            "bool", "char", "str", "Duration", "Instant", "Ordering", "Iterator", "IntoIterator", "Some", "Poll"}


def is_comment(node) -> bool:
    return node.type in ("line_comment", "block_comment")


def _doc_before(node, src: bytes) -> Optional[str]:
    """`///` and `/** */` comments right above an item (attributes like #[derive] may sit in between)."""
    from pmap.langs.base import doc_summary

    parts = []
    prev = node.prev_sibling
    while prev is not None and (is_comment(prev) or prev.type == "attribute_item"):
        if is_comment(prev):
            raw = text(prev, src)
            if raw.startswith("///") or raw.startswith("/**"):
                parts.append(raw)
            elif parts or not raw.startswith("//"):
                break
        prev = prev.prev_sibling
    return doc_summary("\n".join(reversed(parts))) if parts else None


class _Rust:
    def __init__(self, src: bytes, facts: FileFacts) -> None:
        self.src = src
        self.facts = facts

    def t(self, node) -> str:
        return text(node, self.src)

    def run(self, root) -> None:
        inner = [text(c, self.src) for c in root.named_children if is_comment(c) and text(c, self.src).startswith("//!")]
        if inner:
            from pmap.langs.base import doc_summary

            self.facts.doc = doc_summary("\n".join(line.replace("//!", "///", 1) for line in inner))
        self.items(root.named_children, [])

    def items(self, nodes, mod_path: List[str]) -> None:
        ns = "::".join(mod_path) or None
        for node in nodes:
            kind = node.type
            if kind == "use_declaration":
                arg = node.child_by_field_name("argument")
                for target, alias, glob in _use_targets(self, arg, []):
                    self.facts.imports.append(Import(line=line_of(node), target=target, alias=alias, wildcard=glob,
                                                     scope=ns))
            elif kind == "mod_item":
                name = node.child_by_field_name("name")
                body = node.child_by_field_name("body")
                if body is None:  # `mod tax;` -> tax.rs / tax/mod.rs
                    self.facts.imports.append(Import(line=line_of(node), target=f"mod:{self.t(name)}", scope=ns))
                else:  # inline module: its items are namespaced
                    self.items(body.named_children, mod_path + [self.t(name)])
            elif kind in ("struct_item", "enum_item", "union_item", "trait_item", "type_item"):
                name = node.child_by_field_name("name")
                if name is None:
                    continue
                decl = Decl(name=self.t(name), kind="class", line_start=line_of(node), line_end=end_line_of(node),
                            doc=_doc_before(node, self.src), namespace=ns, interface=kind == "trait_item",
                            exported=self.t(node).startswith("pub"))
                self.facts.decls.append(decl)
                body = node.child_by_field_name("body")
                if kind == "enum_item" and body is not None:
                    decl.variants = [self.t(v.child_by_field_name("name")) for v in body.named_children
                                     if v.type == "enum_variant" and v.child_by_field_name("name") is not None]
                if kind == "struct_item" and body is not None:
                    for field in body.named_children:
                        if field.type == "field_declaration":
                            fname, ftype = field.child_by_field_name("name"), field.child_by_field_name("type")
                            if fname is not None and ftype is not None:
                                decl.field_types[self.t(fname)] = _type_name(self, ftype)
                if kind == "trait_item":
                    for bound in node.named_children:
                        if bound.type == "trait_bounds":  # trait Sub: Super
                            for b in bound.named_children:
                                decl.bases.append((_type_name(self, b), line_of(b)))
                    for member in body.named_children if body is not None else []:
                        if member.type in ("function_item", "function_signature_item"):
                            self.function(member, decl, ns)
            elif kind == "impl_item":
                self.impl(node, ns)
            elif kind == "function_item":
                self.function(node, None, ns)
            elif kind in ("const_item", "static_item"):
                self.calls(node, None)

    def impl(self, node, ns: Optional[str]) -> None:
        tnode = node.child_by_field_name("type")
        trait = node.child_by_field_name("trait")
        type_name = _type_name(self, tnode) if tnode is not None else ""
        if not type_name:
            return
        # an `impl` block is a class-like node for the type; the resolver merges it with the type's declaration
        title = f"impl {_type_name(self, trait)} for {type_name}" if trait is not None else f"impl {type_name}"
        holder = Decl(name=title, kind="class", line_start=line_of(node), line_end=end_line_of(node),
                      doc=_doc_before(node, self.src), namespace=ns, receiver=type_name, signature=None)
        if trait is not None:
            holder.bases.append((_type_name(self, trait), line_of(trait)))
        self.facts.decls.append(holder)
        body = node.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            if member.type == "function_item":
                self.function(member, holder, ns, self_type=type_name)

    def bounds(self, node) -> dict:
        """`fn f<P: Provider>(p: &P)` / `where P: Provider` -> {"P": "Provider"}: a generic is its trait."""
        out = {}
        for holder in [node.child_by_field_name("type_parameters")] + [c for c in node.named_children
                                                                       if c.type == "where_clause"]:
            for item in walk(holder) if holder is not None else []:
                if item.type in ("type_parameter", "constrained_type_parameter", "where_predicate"):
                    left = item.child_by_field_name("name") or item.child_by_field_name("left") or (
                        item.named_children[0] if item.named_children else None)
                    bound = next((c for c in walk(item) if c.type == "trait_bounds"), None)
                    first = bound.named_children[0] if bound is not None and bound.named_children else None
                    if left is not None and first is not None:
                        out[self.t(left)] = _type_name(self, first)
        return out

    def function(self, node, parent: Optional[Decl], ns: Optional[str], self_type: Optional[str] = None) -> None:
        name = node.child_by_field_name("name")
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")
        generics = self.bounds(node)
        returns = _type_name(self, ret) if ret is not None else None
        returns = generics.get(returns, returns)
        if returns == "Self" and self_type:
            returns = self_type
        sig = self.t(params) if params is not None else "()"
        if ret is not None:
            sig += " -> " + self.t(ret)
        decl = Decl(name=self.t(name), kind="method" if parent is not None else "function", line_start=line_of(node),
                    line_end=end_line_of(node), parent=parent, signature=squash(sig), namespace=ns,
                    doc=_doc_before(node, self.src), exported=self.t(node).startswith("pub"), returns=returns or None)
        for p in params.named_children if params is not None else []:
            if p.type == "parameter":
                pat, ptype = p.child_by_field_name("pattern"), p.child_by_field_name("type")
                if pat is not None and pat.type == "identifier" and ptype is not None:
                    tname = _type_name(self, ptype) or UNTRACKED  # &str, u8, slices: library methods
                    tname = generics.get(tname, tname)
                    if tname == "Self" and self_type:
                        tname = self_type
                    decl.var_types[self.t(pat)] = tname
        self.facts.decls.append(decl)
        body = node.child_by_field_name("body")
        if body is not None:
            self.locals(body, decl, self_type)
            self.calls(body, decl)

    def locals(self, body, decl: Decl, self_type: Optional[str]) -> None:
        for node in walk(body):
            if node.type != "let_declaration":
                continue
            pat, ltype, value = (node.child_by_field_name(f) for f in ("pattern", "type", "value"))
            if pat is None or pat.type != "identifier":
                continue
            tname = (_type_name(self, ltype) or UNTRACKED) if ltype is not None else ""
            if not tname and value is not None:
                tname = self.value_type(value)
            if tname == "Self" and self_type:
                tname = self_type
            if tname:
                decl.var_types[self.t(pat)] = tname

    def value_type(self, value) -> str:
        """The type of an initial value as a RESULT chain: `Foo::new(x)?.with_y().build()` -> "()Foo.new|with_y|build"."""
        while value.type in ("try_expression", "reference_expression", "await_expression", "parenthesized_expression") \
                and value.named_children:
            value = value.named_children[0]
        if value.type == "struct_expression":
            return _type_name(self, value.child_by_field_name("name"))
        if value.type == "field_expression":  # let g = &mut alice.group: the field's declared type
            chain = self.chain(value)
            return RESULT + "path:" + ".".join(chain) if chain and len(chain) > 1 else ""
        if value.type != "call_expression":
            return ""
        fn = value.child_by_field_name("function")
        if fn is not None and fn.type == "field_expression":
            inner = fn.child_by_field_name("value")
            method = self.t(fn.child_by_field_name("field"))
            while inner is not None and inner.type in ("try_expression", "await_expression") and inner.named_children:
                inner = inner.named_children[0]
            if inner is not None and inner.type in ("call_expression", "struct_expression"):
                base = self.value_type(inner)
                if not base:
                    return ""
                base = base if base.startswith(RESULT) else RESULT + "type:" + base
                return base + "|" + method
        chain = self.chain(fn)
        return RESULT + ".".join(chain) if chain else ""

    def calls(self, root, caller: Optional[Decl]) -> None:
        for node in walk(root):
            if node.type == "call_expression":
                chain = self.chain(node.child_by_field_name("function"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain))
            elif node.type == "struct_expression":
                chain = self.chain(node.child_by_field_name("name"))
                if chain:
                    self.facts.calls.append(Call(line=line_of(node), caller=caller, chain=chain, new=True))

    def path_head(self, node) -> Optional[List[str]]:
        return _path_head(self, node)

    def chain(self, node) -> Optional[List[str]]:
        if node is None:
            return None
        kind = node.type
        if kind in ("identifier", "type_identifier", "field_identifier"):
            return [self.t(node)]
        if kind in ("self", "super", "crate"):
            return [kind]
        if kind in ("scoped_identifier", "scoped_type_identifier"):
            path, name = node.child_by_field_name("path"), node.child_by_field_name("name")
            head = self.path_head(path) if path is not None else []
            return head + [self.t(name)] if head is not None and name is not None else None
        if kind == "field_expression":
            head = self.chain(node.child_by_field_name("value"))
            field = node.child_by_field_name("field")
            return head + [self.t(field)] if head and field is not None else None
        if kind == "generic_function":
            return self.chain(node.child_by_field_name("function"))
        if kind == "generic_type":
            return self.chain(node.child_by_field_name("type"))
        return None


def _path_head(ex: "_Rust", node) -> Optional[List[str]]:
    """In a path, `self::` / `super::` / `crate::` name modules ("@self", "@super", "@crate"), not the receiver."""
    if node.type in ("self", "super", "crate"):
        return ["@" + node.type]
    if node.type in ("scoped_identifier", "scoped_type_identifier"):
        path, name = node.child_by_field_name("path"), node.child_by_field_name("name")
        head = _path_head(ex, path) if path is not None else []
        if head is None or name is None:
            return None
        return head + (["@super"] if name.type == "super" else [ex.t(name)])
    return ex.chain(node)


def _use_targets(ex: _Rust, node, prefix: List[str]):
    """`use a::b::{C, d as e, f::*}` -> ("a::b::C", None, False), ("a::b::d", "e", False), ("a::b::f", None, True)."""
    if node is None:
        return
    kind = node.type
    if kind in ("identifier", "crate", "self", "super"):
        yield "::".join(prefix + [ex.t(node)]), None, False
    elif kind == "scoped_identifier":
        yield "::".join(prefix + [ex.t(node).replace(" ", "")]), None, False
    elif kind == "use_as_clause":
        path, alias = node.child_by_field_name("path"), node.child_by_field_name("alias")
        yield "::".join(prefix + [ex.t(path).replace(" ", "")]), ex.t(alias), False
    elif kind == "use_wildcard":
        inner = node.named_children[0] if node.named_children else None
        yield "::".join(prefix + ([ex.t(inner).replace(" ", "")] if inner is not None else [])), None, True
    elif kind == "scoped_use_list":
        path, lst = node.child_by_field_name("path"), node.child_by_field_name("list")
        sub = prefix + ([ex.t(path).replace(" ", "")] if path is not None else [])
        for item in lst.named_children if lst is not None else []:
            yield from _use_targets(ex, item, sub)
    elif kind == "use_list":
        for item in node.named_children:
            yield from _use_targets(ex, item, prefix)


def _type_name(ex: _Rust, node) -> str:
    if node is None:
        return ""
    if node.type in ("reference_type", "pointer_type") :
        inner = node.child_by_field_name("type")
        return _type_name(ex, inner)
    if node.type == "generic_type":
        base = ex.t(node.child_by_field_name("type"))
        if base in ("Box", "Rc", "Arc", "Option", "RefCell", "Mutex", "RwLock", "Result", "Cow", "Pin"):  # wrappers
            args = node.child_by_field_name("type_arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            return _type_name(ex, first) if first is not None else ""
        return base_type_name(base.replace("::", "."))
    if node.type in ("dynamic_type", "abstract_type"):  # dyn Trait / impl Trait
        return _type_name(ex, node.child_by_field_name("trait") or (node.named_children[0] if node.named_children else None))
    if node.type in ("type_identifier", "scoped_type_identifier", "identifier"):
        return base_type_name(ex.t(node).replace("::", "."))
    return ""


def extract(src: bytes, rel_path: str, ext: str) -> FileFacts:
    tree = parser_for("rust").parse(src)
    facts = FileFacts(rel_path=rel_path, language="rust", line_end=max(1, tree.root_node.end_point[0] + 1))
    facts.syntax_errors = 1 if tree.root_node.has_error else 0
    _Rust(src, facts).run(tree.root_node)
    return facts
