"""Turn the FileFacts of a project into map nodes and edges, with the same confidence rules as for Python.

- `resolved`: the target is proven: an import that points at a project file, a call of an imported or same-package
  function, `new X()` of a project class, a method of a variable's declared type (the compiler checks that type)
  or of `this`, as long as no subclass overrides it.
- `inferred`: such a call when a subclass overrides the method, or the type is an interface / protocol / trait;
  `candidates` lists the possible targets.
- `unresolved`: the map does not know the target; it is reported, never guessed.
Calls into libraries and the standard library get no edge: the import already records that dependency.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pmap.langs import cfamily, csharp, dart, golang, javascript, jvm, rust, swift
from pmap.langs.base import LOCAL, PASS_THROUGH, RESULT, UNTRACKED, Call, Decl, FileFacts, Import, base_type_name
from pmap.models import ActualEdge, ActualNode, CodeLocation, EdgeLocation

FAMILY = {"typescript": "js", "javascript": "js", "go": "go", "java": "jvm", "kotlin": "jvm", "csharp": "cs",
          "rust": "rust", "swift": "swift", "dart": "dart", "c": "c", "cpp": "c"}
BUILTINS = {"js": javascript.BUILTINS, "go": golang.BUILTINS, "jvm": jvm.BUILTINS, "cs": csharp.BUILTINS,
            "rust": rust.BUILTINS, "swift": swift.BUILTINS, "dart": dart.BUILTINS, "c": cfamily.BUILTINS}
IMPLICIT_THIS = ("jvm", "cs", "swift", "dart", "c")  # a bare `m()` inside a class may be `this.m()`
JS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")

Target = Tuple[str, str]  # ("sym", node id) | ("mod", rel path) | ("pkg", family:namespace) | ("ext", ext id)


class Resolver:
    def __init__(self, repo_root: Path, files: List[FileFacts], hashes: Dict[str, str]) -> None:
        self.root = repo_root
        self.files = {f.rel_path: f for f in files}
        self.hashes = hashes
        self.nodes: Dict[str, ActualNode] = {}
        self.edges: List[ActualEdge] = []
        self._edge_keys: Set[tuple] = set()
        self.decl_by_id: Dict[str, Decl] = {}
        self.file_of: Dict[str, str] = {}  # node id -> rel path
        self.top: Dict[str, Dict[str, str]] = {}  # rel -> top-level name -> id
        self.ns_index: Dict[str, Dict[str, List[str]]] = {}  # "family:namespace" -> name -> ids
        self.ns_files: Dict[str, List[str]] = {}
        self.methods: Dict[str, Dict[str, List[str]]] = {}  # class id -> method name -> ids
        self.fields: Dict[str, Dict[str, str]] = {}  # class id -> field -> type as written
        self.bases: Dict[str, List[str]] = {}  # class id -> base class ids
        self.children: Dict[str, List[str]] = {}
        self.class_file: Dict[str, str] = {}
        self.global_usings: List[Import] = []
        self.receiver_of: Dict[str, str] = {}  # impl / extension block id -> the type it extends
        self.foreign: Set[str] = set()  # classes with a library (or unknown) base: they have methods we cannot see
        self.nested: Optional[Dict[str, List[str]]] = None
        self.c_paths = sorted(rel for rel, f in self.files.items() if FAMILY[f.language] == "c")
        self.js = _JsModules(repo_root, set(self.files))
        self.go_modules = _go_modules(repo_root)
        self.rust = _RustCrates(repo_root)
        self.dart = _DartPackages(repo_root, set(self.files))

    # ================================================================== build

    def build(self) -> Tuple[List[ActualNode], List[ActualEdge]]:
        for facts in self.files.values():
            self._declare(facts)
        self._attach_receivers()
        self._index_classes()
        self.global_usings = [imp for f in self.files.values() if f.language == "csharp" for imp in f.imports
                              if imp.is_global]
        envs = []
        self.envs: Dict[str, "_FileEnv"] = {}
        for facts in self.files.values():
            env = _FileEnv(self, facts)
            self.envs[facts.rel_path] = env
            envs.append(env)
        for env in envs:  # pass 1: names (imports may look into other files' imports) and the class hierarchy
            env.ensure_imports()
        for env in envs:
            for decl in env.f.decls:
                if decl.kind == "class":
                    env.bases(decl)
        for env in envs:  # pass 2: calls, then the files each package import really uses
            env.types_used()
            for call in env.f.calls:
                env.call(call)
            env.finish()
        return list(self.nodes.values()), self.edges

    def _declare(self, facts: FileFacts) -> None:
        rel = facts.rel_path
        mod_id = f"mod:{rel}"
        fhash = self.hashes.get(rel, "")
        stem = posixpath.basename(rel).split(".")[0]
        tops = [d for d in facts.decls if d.parent is None and d.doc]
        main = next((d for d in tops if d.name == stem), tops[0] if len(tops) == 1 else None)
        self.nodes[mod_id] = ActualNode(id=mod_id, kind="module", name=posixpath.basename(rel), qualified_name=rel,
                                        docstring_summary=facts.doc or (main.doc if main is not None else None),
                                        location=CodeLocation(rel, 1, facts.line_end, fhash))
        family = FAMILY[facts.language]
        ns_key = self.ns_key(facts)
        if ns_key is not None:
            self.ns_files.setdefault(ns_key, []).append(rel)
        taken: Dict[str, int] = {}
        top = self.top.setdefault(rel, {})
        if facts.language == "go":  # a method sits inside its receiver type when both are in this file
            types = {d.name: d for d in facts.decls if d.kind == "class" and d.parent is None}
            for decl in facts.decls:
                if decl.receiver and decl.parent is None and decl.receiver in types:
                    decl.parent = types[decl.receiver]
        sep = "::" if family == "c" else "."
        for decl in facts.decls:  # ids first: a parent may be declared after its children (Go methods)
            parts = self.id_parts(decl)
            base = f"sym:{rel}:{'.'.join(parts)}"
            taken[base] = taken.get(base, 0) + 1
            decl.node_id = base if taken[base] == 1 else f"{base}#{taken[base]}"
        for decl in facts.decls:
            node_id = decl.node_id
            parts = self.id_parts(decl)
            self.decl_by_id[node_id] = decl
            self.file_of[node_id] = rel
            parent_id = decl.parent.node_id if decl.parent is not None else mod_id
            # a method defined away from its type (Go, C++ `Type::m`) is shown as `Type.m` / `Type::m`
            name = decl.name if not (decl.receiver and decl.parent is None and decl.kind != "class") \
                else f"{decl.receiver}{sep}{decl.name}"
            self.nodes[node_id] = ActualNode(
                id=node_id, kind=decl.kind, name=name, qualified_name=self.qualified(facts, decl, parts),
                parent_id=parent_id, signature=decl.signature, docstring_summary=decl.doc,
                location=CodeLocation(rel, decl.line_start, decl.line_end, fhash))
            self.edge(parent_id, node_id, "contains", "resolved", rel, decl.line_start, facts)
            if decl.parent is None and not decl.receiver:
                top.setdefault(decl.name, node_id)
                key = self.decl_ns_key(facts, decl, ns_key)
                if key is not None:
                    self.ns_index.setdefault(key, {}).setdefault(decl.name, []).append(node_id)
                    self.ns_files.setdefault(key, [])
                    if rel not in self.ns_files[key]:
                        self.ns_files[key].append(rel)

    @staticmethod
    def id_parts(decl: Decl) -> List[str]:
        if decl.kind == "class" and decl.receiver:  # Rust `impl`, Swift / Dart `extension`: a block of the type
            return [f"{decl.receiver.split('::')[-1]}@{'impl' if decl.name.startswith('impl') else 'ext'}"]
        if decl.parent is not None and decl.parent.kind == "class" and decl.parent.receiver:
            return [decl.parent.receiver.split("::")[-1], decl.name]
        if decl.receiver and decl.parent is None:  # Go / C++ method of a type in another file; attached later
            return [decl.receiver.split("::")[-1], decl.name]
        return decl.qualified_parts()

    def ns_key(self, facts: FileFacts) -> Optional[str]:
        family = FAMILY[facts.language]
        if family == "go":
            return f"go:{posixpath.dirname(facts.rel_path)}"
        if family == "jvm":
            return f"jvm:{facts.namespace or ''}"
        if family == "cs":
            return f"cs:{facts.namespace or ''}"
        if family == "rust":
            return self.rust.module_key(facts.rel_path)
        if family == "swift":
            return f"swift:{_swift_module(facts.rel_path)}"
        if family == "c":
            return "c:"
        return None

    def decl_ns_key(self, facts: FileFacts, decl: Decl, ns_key: Optional[str]) -> Optional[str]:
        family = FAMILY[facts.language]
        if family == "cs":
            return f"cs:{decl.namespace or ''}"
        if family == "c":
            return f"c:{decl.namespace or ''}"
        if family == "rust" and decl.namespace:  # an inline `mod x { ... }`
            return f"{ns_key}::{decl.namespace}"
        return ns_key

    def qualified(self, facts: FileFacts, decl: Decl, parts: List[str]) -> str:
        family = FAMILY[facts.language]
        if family in ("jvm", "cs", "c"):
            root = decl
            while root.parent is not None:
                root = root.parent
            ns = root.namespace or facts.namespace
            sep = "::" if family == "c" else "."
            return sep.join(([ns] if ns else []) + parts)
        if family == "go":
            return ".".join(([facts.namespace] if facts.namespace else []) + parts)
        if family == "rust":
            module = self.rust.module_key(facts.rel_path)[len("rust:"):]
            return "::".join([p for p in [module] if p] + ([decl.namespace] if decl.namespace and decl.parent is None else []) + parts)
        if family == "swift":
            module = _swift_module(facts.rel_path)
            return ".".join(([module] if module else []) + parts)
        stem = re.sub(r"\.(d\.ts|tsx?|mts|cts|jsx?|mjs|cjs|dart)$", "", facts.rel_path)
        return f"{stem}." + ".".join(parts)

    def _attach_receivers(self) -> None:
        """Methods declared away from their type: Go methods, C++ `Type::m` definitions, Rust `impl` blocks and
        Swift / Dart `extension`s. They join the type's method table; the type may live in another file."""
        for facts in self.files.values():
            for decl in facts.decls:
                if not decl.receiver or decl.parent is not None:
                    continue
                cls = self.receiver_type(facts, decl)
                if cls is None or cls == decl.node_id:
                    continue
                if decl.kind == "class":
                    self.receiver_of[decl.node_id] = cls
                else:
                    self.methods.setdefault(cls, {}).setdefault(decl.name, []).append(decl.node_id)
                    decl.parent_type = cls  # type: ignore[attr-defined]

    def receiver_type(self, facts: FileFacts, decl: Decl) -> Optional[str]:
        family = FAMILY[facts.language]
        parts = decl.receiver.split("::")
        name = parts[-1]

        def classes(key: Optional[str]) -> List[str]:
            ids = self.ns_index.get(key, {}).get(name, []) if key else []
            return [i for i in ids if self.decl_by_id[i].kind == "class" and not self.decl_by_id[i].receiver]

        if family == "c":
            outer = (decl.namespace or "").split("::") if decl.namespace else []
            for i in range(len(outer), -1, -1):  # `vpn::Manager` from inside `namespace vpn`, or from outside
                found = classes("c:" + "::".join(outer[:i] + parts[:-1]))
                if found:
                    return found[0]
        elif family == "rust":
            crate = self.rust.module_key(facts.rel_path).split("::")[0]
            own = classes(self.decl_ns_key(facts, decl, self.ns_key(facts))) or classes(self.ns_key(facts))
            if own:  # the type in the same (inline) module first
                return own[0]
            found = [i for key in self.ns_index if key == crate or key.startswith(crate + "::") for i in classes(key)]
            return found[0] if len(found) == 1 else None
        elif family in ("go", "swift"):
            found = classes(self.ns_key(facts))
            if found:
                return found[0]
        elif family == "dart":
            same = [d.node_id for d in facts.decls if d.kind == "class" and d.name == name and not d.receiver]
            if same:
                return same[0]
            found = [i for i, d in self.decl_by_id.items() if d.kind == "class" and d.name == name and not d.receiver
                     and FAMILY[self.files[self.file_of[i]].language] == "dart"]
            return found[0] if len(found) == 1 else None
        return None

    def _index_classes(self) -> None:
        for node_id, decl in self.decl_by_id.items():
            if decl.kind == "class":
                self.class_file[node_id] = self.file_of[node_id]
                self.fields.setdefault(node_id, {}).update(decl.field_types)
            elif decl.kind == "method" and decl.parent is not None:
                self.methods.setdefault(decl.parent.node_id, {}).setdefault(decl.name, []).append(node_id)
                owner = self.receiver_of.get(decl.parent.node_id)
                if owner is not None:  # a method of an impl / extension block is a method of the type
                    self.methods.setdefault(owner, {}).setdefault(decl.name, []).append(node_id)

    # ================================================================== edges

    def edge(self, src: str, dst: str, kind: str, confidence: str, path: str, line: int, facts: FileFacts,
             target: Optional[str] = None, candidates: Optional[List[str]] = None, type_only: bool = False) -> None:
        key = (src, dst, kind, line, type_only)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(ActualEdge(from_id=src, to_id=dst, kind=kind, confidence=confidence,
                                     source=f"tree-sitter-{facts.language}", target_symbol=target,
                                     location=EdgeLocation(path, line), candidates=candidates, type_only=type_only))

    def external(self, name: str) -> str:
        ext_id = f"ext:{name}"
        if ext_id not in self.nodes:
            self.nodes[ext_id] = ActualNode(id=ext_id, kind="module", name=name, qualified_name=name, external=True)
        return ext_id

    # ================================================================== class hierarchy

    def ancestors(self, cls: str) -> List[str]:
        out, stack, seen = [], list(self.bases.get(cls, [])), {cls}
        while stack:
            c = stack.pop(0)
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            stack.extend(self.bases.get(c, []))
        return out

    def descendants(self, cls: str) -> List[str]:
        out, stack, seen = [], list(self.children.get(cls, [])), {cls}
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            stack.extend(self.children.get(c, []))
        return out

    def pick(self, ids: List[str]) -> str:
        """Of several declarations with one name: a class, else a definition rather than a header declaration."""
        classes = [i for i in ids if self.decl_by_id[i].kind == "class"]
        defined = [i for i in ids if not self.decl_by_id[i].declaration_only]
        return (classes or defined or ids)[0]

    def find_method(self, cls: str, name: str) -> List[str]:
        for c in [cls] + self.ancestors(cls):
            found = self.methods.get(c, {}).get(name)
            if found:
                defined = [m for m in found if not self.decl_by_id[m].declaration_only]
                return defined or found  # C++: the definition, not the declaration in the header
        return []

    def overrides(self, cls: str, name: str) -> List[str]:
        return [m for d in self.descendants(cls) for m in self.methods.get(d, {}).get(name, [])]

    def has_foreign_base(self, cls: str) -> bool:
        return any(c in self.foreign for c in [cls] + self.ancestors(cls))

    def field_type(self, cls: str, name: str) -> Optional[str]:
        for c in [cls] + self.ancestors(cls):
            if name in self.fields.get(c, {}):
                return self.fields[c][name]
        return None

    def enclosing_class(self, decl: Optional[Decl]) -> Optional[str]:
        while decl is not None:
            if decl.kind == "class":
                return self.receiver_of.get(decl.node_id, decl.node_id)
            if getattr(decl, "parent_type", None):
                return decl.parent_type  # type: ignore[attr-defined]
            decl = decl.parent
        return None


class _FileEnv:
    """Names visible in one file and the edges that come out of it."""

    def __init__(self, r: Resolver, facts: FileFacts) -> None:
        self.r = r
        self.f = facts
        self.family = FAMILY[facts.language]
        self.mod_id = f"mod:{facts.rel_path}"
        self.env: Dict[str, Target] = {}
        self.search_ns: List[str] = []  # extra namespaces searched for simple names (wildcards, usings)
        self.external_wildcards = False  # names may come from a library namespace we cannot see
        self.package_imports: List[Tuple[Import, str]] = []  # (import, ns key): edges decided at the end
        self.used_files: Dict[str, Set[str]] = {}  # ns key -> files whose declarations this file uses
        self.dart_imports: List[Tuple[str, Optional[Set[str]]]] = []  # imported Dart libraries (file, `show` names)
        self.import_state = "new"
        for name, node_id in r.top.get(facts.rel_path, {}).items():
            self.env[name] = ("sym", node_id)

    # ---------------------------------------------------------------- imports

    def ensure_imports(self) -> None:
        if self.import_state == "new":
            self.import_state = "running"
            self.imports()
            self.import_state = "done"

    def imports(self) -> None:
        handler = getattr(self, f"_imports_{self.family}")
        imports = list(self.f.imports)
        if self.family == "cs":
            imports = [i for i in self.r.global_usings if i not in imports] + imports
        for imp in imports:
            handler(imp)
        if self.family == "cs":
            ns = self.f.namespace or ""
            parts = ns.split(".") if ns else []
            for i in range(len(parts), -1, -1):  # own namespace and its parents
                key = "cs:" + ".".join(parts[:i])
                if key not in self.search_ns:
                    self.search_ns.append(key)
        if self.family == "jvm":
            own = f"jvm:{self.f.namespace or ''}"
            if own not in self.search_ns:
                self.search_ns.insert(0, own)
            self.external_wildcards = True  # java.lang / kotlin.* are always in scope
        if self.family in ("go", "swift", "rust"):
            own = self.r.ns_key(self.f)
            if own not in self.search_ns:
                self.search_ns.insert(0, own)
        if self.family == "rust":
            own = self.r.ns_key(self.f)
            self.env.setdefault("@self", ("pkg", own))
            self.env.setdefault("@crate", ("pkg", own.split("::")[0]))
            self.env.setdefault("@super", ("pkg", own.rsplit("::", 1)[0] if "::" in own else own))
        if self.family == "c":  # the namespaces this file declares, their parents, then the global one
            for decl in self.f.decls:
                parts = decl.namespace.split("::") if decl.namespace else []
                for i in range(len(parts), 0, -1):
                    key = "c:" + "::".join(parts[:i])
                    if key not in self.search_ns:
                        self.search_ns.append(key)
            if "c:" not in self.search_ns:
                self.search_ns.append("c:")

    # Rust: `mod x;` declares a child module; `use a::b::{C, d as e, f::*}` binds names
    def _imports_rust(self, imp: Import) -> None:
        own = self.r.ns_key(self.f) + (f"::{imp.scope}" if imp.scope else "")
        if imp.target.startswith("mod:"):
            name = imp.target[4:]
            key = f"{own}::{name}"
            files = self.r.ns_files.get(key) or [rel for rel in self.r.files if self.r.rust.module_key(rel) == key]
            if not files:
                self.r.edge(self.mod_id, f"unresolved:import:mod {name}", "imports", "unresolved", self.f.rel_path,
                            imp.line, self.f, f"mod {name}")
                return
            self.env[name] = ("pkg", key)
            for rel in files:
                if self.r.rust.module_key(rel) == key:
                    self.r.edge(self.mod_id, f"mod:{rel}", "imports", "resolved", self.f.rel_path, imp.line, self.f,
                                f"mod {name}")
            return
        parts = [p for p in imp.target.split("::") if p]
        if not parts:
            return
        head = parts[0]
        crate = own.split("::")[0]
        if head == "crate":
            target: Optional[Target] = ("pkg", crate)
        elif head == "self":
            target = ("pkg", own)
        elif head == "super":
            target = ("pkg", own.rsplit("::", 1)[0] if "::" in own else own)
        elif f"{own}::{head}" in self.r.ns_files:
            target = ("pkg", f"{own}::{head}")
        elif self.r.rust.crate_key(head) in self.r.ns_files:
            target = ("pkg", self.r.rust.crate_key(head))
        elif head in self.env:
            target = self.env[head]
        else:  # std, serde, tokio...: a library crate
            ext = self.r.external(head)
            self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, imp.target)
            if not imp.wildcard:
                self.env[imp.alias or parts[-1]] = ("ext", ext)
            self.external_wildcards = True
            return
        for part in parts[1:]:
            if target is None:
                break
            if part == "self":
                continue
            if part == "super" and target[0] == "pkg":
                target = ("pkg", target[1].rsplit("::", 1)[0] if "::" in target[1] else target[1])
                continue
            target = self.member(target, part)
        if target is None:
            self.r.edge(self.mod_id, f"unresolved:import:{imp.target}", "imports", "unresolved", self.f.rel_path,
                        imp.line, self.f, imp.target)
            return
        if imp.wildcard:
            if target[0] == "pkg":
                self.search_ns.append(target[1])
                self.package_imports.append((imp, target[1]))
            return
        local = imp.alias or (parts[-2] if parts[-1] == "self" and len(parts) > 1 else parts[-1])
        self.env[local] = target
        if target[0] == "sym":
            self.r.edge(self.mod_id, f"mod:{self.r.file_of[target[1]]}", "imports", "resolved", self.f.rel_path,
                        imp.line, self.f, imp.target)
        elif target[0] == "pkg":
            self.package_imports.append((imp, target[1]))

    # Swift: every file of a module sees the others; `import X` opens another module of the project or a library
    def _imports_swift(self, imp: Import) -> None:
        key = f"swift:{imp.target}"
        if key in self.r.ns_files and key != self.r.ns_key(self.f):
            self.search_ns.append(key)
            self.package_imports.append((imp, key))
            return
        if key == self.r.ns_key(self.f):
            return
        self.external_wildcards = True
        self.r.edge(self.mod_id, self.r.external(imp.target), "imports", "resolved", self.f.rel_path, imp.line,
                    self.f, imp.target)

    # Dart: `import 'package:app/x.dart' as p show A`, relative imports, `export`, `part`
    def _imports_dart(self, imp: Import) -> None:
        target_rel = self.r.dart.resolve(self.f.rel_path, imp.target)
        if imp.alias == "part":  # a part file shares its library's names
            if target_rel is not None:
                self.dart_imports.append((target_rel, None))
                if not imp.static:
                    self.r.edge(self.mod_id, f"mod:{target_rel}", "imports", "resolved", self.f.rel_path, imp.line,
                                self.f, imp.target)
            return
        if target_rel is not None:
            self.r.edge(self.mod_id, f"mod:{target_rel}", "imports", "resolved", self.f.rel_path, imp.line, self.f,
                        imp.target)
            if imp.reexport:
                return
            if imp.alias:
                self.env[imp.alias] = ("mod", target_rel)
            else:
                self.dart_imports.append((target_rel, {n for n, _ in imp.names} or None))
            return
        if not (imp.target.startswith("package:") or imp.target.startswith("dart:")):
            self.r.edge(self.mod_id, f"unresolved:import:{imp.target}", "imports", "unresolved", self.f.rel_path,
                        imp.line, self.f, imp.target)
            return
        name = imp.target.split("/")[0].replace("package:", "")
        ext = self.r.external(name)
        self.external_wildcards = True
        self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, imp.target)
        if imp.alias:
            self.env[imp.alias] = ("ext", ext)

    # C / C++: `#include "x.h"` (a project file) or `<x.h>` (a system or library header); `using namespace`
    def _imports_c(self, imp: Import) -> None:
        if imp.wildcard:  # using namespace x;
            key = "c:" + imp.target.replace(" ", "")
            if key in self.r.ns_files:
                self.search_ns.append(key)
            else:
                self.external_wildcards = True
            return
        target_rel = None if imp.static else _c_include(self.f.rel_path, imp.target, self.r.c_paths)
        if target_rel is not None:
            self.r.edge(self.mod_id, f"mod:{target_rel}", "imports", "resolved", self.f.rel_path, imp.line, self.f,
                        imp.target)
            return
        self.external_wildcards = True
        name = imp.target.split("/")[0]
        self.r.edge(self.mod_id, self.r.external(name), "imports", "resolved", self.f.rel_path, imp.line, self.f,
                    imp.target)

    def _imports_js(self, imp: Import) -> None:
        target_rel = self.r.js.resolve(self.f.rel_path, imp.target)
        if target_rel is not None:
            self.r.edge(self.mod_id, f"mod:{target_rel}", "imports", "resolved", self.f.rel_path, imp.line, self.f,
                        imp.target, type_only=imp.type_only)
            if imp.reexport:
                return
            if imp.default:
                self.env[imp.default] = ("export", f"{target_rel}\0default")
            if imp.alias:
                self.env[imp.alias] = ("mod", target_rel)
            for name, local in imp.names:
                self.env[local] = ("export", f"{target_rel}\0{name}")
            return
        if self.r.js.is_relative(imp.target):
            self.r.edge(self.mod_id, f"unresolved:import:{imp.target}", "imports", "unresolved", self.f.rel_path,
                        imp.line, self.f, imp.target)
            return
        ext = self.r.external(_npm_package(imp.target))
        self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, imp.target,
                    type_only=imp.type_only)
        for local in [imp.default, imp.alias] + [l for _, l in imp.names]:
            if local:
                self.env[local] = ("ext", ext)

    def _imports_go(self, imp: Import) -> None:
        directory = _go_dir(self.r.go_modules, imp.target)
        files = self.r.ns_files.get(f"go:{directory}") if directory is not None else None
        if files:
            key = f"go:{directory}"
            name = imp.alias or self._go_package_name(files) or imp.target.rsplit("/", 1)[-1]
            if name == ".":
                self.search_ns.append(key)
            elif name != "_":
                self.env[name] = ("pkg", key)
            self.package_imports.append((imp, key))
            return
        if directory is not None and _looks_internal_go(self.r.go_modules, imp.target):
            self.r.edge(self.mod_id, f"unresolved:import:{imp.target}", "imports", "unresolved", self.f.rel_path,
                        imp.line, self.f, imp.target)
            return
        ext = self.r.external(_go_external(imp.target))
        self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, imp.target)
        name = imp.alias or imp.target.rsplit("/", 1)[-1]
        if name not in (".", "_"):
            self.env[name] = ("ext", ext)

    def _go_package_name(self, files: List[str]) -> Optional[str]:
        for rel in files:
            ns = self.r.files[rel].namespace
            if ns and not ns.endswith("_test"):
                return ns
        return None

    def _imports_jvm(self, imp: Import) -> None:
        target = imp.target
        if imp.wildcard:
            key = f"jvm:{target}"
            if key in self.r.ns_files:
                self.search_ns.append(key)
                self.package_imports.append((imp, key))
            else:
                owner = self._jvm_class(target)  # import a.b.C.* (nested types / static members)
                if owner is None:
                    self.r.edge(self.mod_id, self.r.external(_dotted_external(target)), "imports", "resolved",
                                self.f.rel_path, imp.line, self.f, target + ".*")
            return
        pkg, _, name = target.rpartition(".")
        local = imp.alias or name
        cls = self._jvm_class(target)
        if cls is not None:
            self.env[local] = ("sym", cls)
            self.r.edge(self.mod_id, f"mod:{self.r.file_of[cls]}", "imports", "resolved", self.f.rel_path, imp.line,
                        self.f, target)
            return
        owner = self._jvm_class(pkg)  # static import of a member, or Kotlin import of a top-level function
        members = self.r.methods.get(owner, {}).get(name, []) if owner else []
        top = self.r.ns_index.get(f"jvm:{pkg}", {}).get(name, [])
        found = members or top
        if found:
            self.env[local] = ("sym", found[0])
            self.r.edge(self.mod_id, f"mod:{self.r.file_of[found[0]]}", "imports", "resolved", self.f.rel_path,
                        imp.line, self.f, target)
            return
        if self._jvm_internal_prefix(target):
            self.r.edge(self.mod_id, f"unresolved:import:{target}", "imports", "unresolved", self.f.rel_path,
                        imp.line, self.f, target)
            return
        ext = self.r.external(_dotted_external(target))
        self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, target)
        self.env[local] = ("ext", ext)

    def _jvm_class(self, dotted: str) -> Optional[str]:
        """`a.b.C` or `a.b.C.Inner` -> class id, when it is a project class."""
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            ids = self.r.ns_index.get("jvm:" + ".".join(parts[:i]), {}).get(parts[i], [])
            cls = next((x for x in ids if self.r.decl_by_id[x].kind == "class"), None)
            for inner in parts[i + 1:]:
                if cls is None:
                    break
                cls = next((x for x in _nested(self.r, cls) if self.r.decl_by_id[x].name == inner), None)
            if cls is not None:
                return cls
        return None

    def _jvm_internal_prefix(self, dotted: str) -> bool:
        return any(k[4:] and (dotted + ".").startswith(k[4:] + ".") for k in self.r.ns_files if k.startswith("jvm:"))

    def _imports_cs(self, imp: Import) -> None:
        key = f"cs:{imp.target}"
        if imp.alias:
            cls = self._cs_type(imp.target.split("."), [])
            if cls is not None:
                self.env[imp.alias] = ("sym", cls)
                self.r.edge(self.mod_id, f"mod:{self.r.file_of[cls]}", "imports", "resolved", self.f.rel_path,
                            imp.line, self.f, imp.target)
            elif key in self.r.ns_files:
                self.env[imp.alias] = ("pkg", key)
                self.package_imports.append((imp, key))
            else:
                ext = self.r.external(imp.target)
                self.env[imp.alias] = ("ext", ext)
                self.r.edge(self.mod_id, ext, "imports", "resolved", self.f.rel_path, imp.line, self.f, imp.target)
            return
        if imp.static:
            cls = self._cs_type(imp.target.split("."), [])
            if cls is not None:
                for name, ids in self.r.methods.get(cls, {}).items():
                    self.env.setdefault(name, ("sym", ids[0]))
            return
        if key in self.r.ns_files:
            self.search_ns.append(key)
            self.package_imports.append((imp, key))
        elif self._cs_internal_prefix(imp.target):
            pass  # a parent of project namespaces that declares no types itself
        else:
            self.external_wildcards = True
            if not imp.is_global or imp in self.f.imports:
                self.r.edge(self.mod_id, self.r.external(imp.target), "imports", "resolved", self.f.rel_path,
                            imp.line, self.f, imp.target)

    def _cs_internal_prefix(self, dotted: str) -> bool:
        return any(k[3:] and (k[3:] + ".").startswith(dotted + ".") for k in self.r.ns_files if k.startswith("cs:"))

    def _cs_type(self, parts: List[str], search: List[str]) -> Optional[str]:
        """Resolve `Ns.Sub.Type` or `Type` (searched in `search`) to a project class id."""
        for i in range(len(parts) - 1, -1, -1):
            spaces = [f"cs:{'.'.join(parts[:i])}"] if i else search
            for key in spaces:
                ids = self.r.ns_index.get(key, {}).get(parts[i], [])
                cls = next((x for x in ids if self.r.decl_by_id[x].kind == "class"), None)
                for inner in parts[i + 1:]:
                    if cls is None:
                        break
                    cls = next((x for x in _nested(self.r, cls) if self.r.decl_by_id[x].name == inner), None)
                if cls is not None:
                    return cls
            if i == 0:
                break
        return None

    # ---------------------------------------------------------------- name lookup

    def lookup(self, name: str) -> Optional[Target]:
        if name in self.env:
            target = self.env[name]
            if target[0] == "export":
                rel, exported = target[1].split("\0")
                return self.r.js.lookup_export(self.r, rel, exported)
            return target
        found: List[str] = []
        for key in self.search_ns:
            ids = self.r.ns_index.get(key, {}).get(name, [])
            if ids:
                found = ids
                break
        if found:
            return ("sym", self.r.pick(found))
        if self.family == "dart":
            for rel, show in self.dart_imports:
                if show is not None and name not in show:
                    continue
                hit = self.r.dart.lookup_export(self.r, rel, name)
                if hit is not None:
                    return hit
        if self.family in ("c", "cs", "rust"):  # a namespace / module used as a qualifier: util::Format()
            sep = "::" if self.family in ("c", "rust") else "."
            for key in [f"{self.family}:{name}"] + [f"{k}{sep}{name}" for k in self.search_ns if k.endswith(":") is False]:
                if key in self.r.ns_files:
                    return ("pkg", key)
        return None

    def note_use(self, node_id: str) -> None:
        rel = self.r.file_of.get(node_id)
        if rel is None:
            return
        for _imp, key in self.package_imports:
            if rel in self.r.ns_files.get(key, []):
                self.used_files.setdefault(key, set()).add(rel)

    # ---------------------------------------------------------------- inheritance

    def bases(self, decl: Decl) -> None:
        owner = self.r.receiver_of.get(decl.node_id, decl.node_id)  # `impl Trait for Type` gives Type a base
        for written, line in decl.bases:
            target = self.resolve_type(written)
            if target is not None and target[0] == "sym" and self.r.decl_by_id[target[1]].kind == "class":
                if target[1] not in (decl.node_id, owner):
                    self.r.bases.setdefault(owner, []).append(target[1])
                    self.r.children.setdefault(target[1], []).append(owner)
                    self.r.edge(decl.node_id, target[1], "inherits", "resolved", self.f.rel_path, line, self.f, written)
                    self.note_use(target[1])
            else:
                self.r.foreign.add(owner)  # a library base (or an unknown one) brings methods the map cannot see
                if target is None and not self.quiet(written.split(".")[0]):
                    self.r.edge(decl.node_id, f"unresolved:{written}", "inherits", "unresolved", self.f.rel_path, line,
                                self.f, written)

    def resolve_type(self, written: str) -> Optional[Target]:
        parts = base_type_name(written).split(".")
        if self.family == "cs" and len(parts) > 1:
            cls = self._cs_type(parts, self.search_ns)
            if cls is not None:
                return ("sym", cls)
        if self.family == "jvm" and len(parts) > 1:
            cls = self._jvm_class(".".join(parts))
            if cls is not None:
                return ("sym", cls)
        target = self.lookup(parts[0])
        for part in parts[1:]:
            if target is None:
                return None
            target = self.member(target, part)
        return target

    def member(self, target: Target, name: str) -> Optional[Target]:
        kind, value = target
        if kind == "mod":
            if self.family == "dart":
                return self.r.dart.lookup_export(self.r, value, name)
            return self.r.js.lookup_export(self.r, value, name)
        if kind == "pkg":
            if name == "@super" and value.startswith("rust:"):
                return ("pkg", value.rsplit("::", 1)[0] if "::" in value else value)
            ids = self.r.ns_index.get(value, {}).get(name, [])
            if ids:
                return ("sym", self.r.pick(ids))
            if value.startswith("rust:"):  # `pub use inner::Thing;` makes `module::Thing` work
                for rel in self.r.rust.files_of(value, self.r.files):
                    env = self.r.envs.get(rel)
                    if env is None or env is self:
                        continue
                    env.ensure_imports()
                    if name in env.env and not name.startswith("@"):
                        return env.env[name]
            sep = "." if value.startswith("cs:") else "::"
            if value.startswith(("cs:", "c:", "rust:")):
                sub = f"{value}{sep}{name}" if not value.endswith(":") else f"{value}{name}"
                return ("pkg", sub) if sub in self.r.ns_files else None
            return None
        if kind == "sym":
            decl = self.r.decl_by_id[value]
            if decl.kind == "class":
                nested = next((x for x in _nested(self.r, value) if self.r.decl_by_id[x].name == name), None)
                if nested is not None:
                    return ("sym", nested)
                found = self.r.find_method(value, name)
                if found:
                    return ("sym", found[0])
                if name in decl.variants:  # Kind::Variant(..) makes a Kind
                    return ("sym", value)
                return None
        if kind == "ext":
            return target
        return None

    def quiet(self, head: str) -> bool:
        """Names that are not worth an `unresolved` report: builtins, or anything a library namespace may supply."""
        if head in BUILTINS[self.family]:
            return True
        if self.family in ("jvm", "cs", "swift", "dart", "rust") and self.external_wildcards and head[:1].isupper():
            return True
        if self.family == "c" and self.external_wildcards:  # Win32 / libc / library headers declare it
            return True
        return False

    # ---------------------------------------------------------------- calls

    def call(self, call: Call) -> None:
        caller = call.caller.node_id if call.caller is not None else self.mod_id
        chain, line, expr = call.chain, call.line, ".".join(call.chain)
        head = chain[0]
        cls = self.r.enclosing_class(call.caller)
        var_type = call.caller.var_types.get(head) if call.caller is not None else None
        if var_type is None and head not in self.env:
            var_type = self.f.var_types.get(head)
        if var_type == UNTRACKED:
            return  # a string, array, number...: library methods, not project links
        if var_type == LOCAL:  # a local of unknown type: the map cannot tell
            return self.unresolved(caller, expr, line) if len(chain) > 1 else None
        type_env = self
        if var_type is not None and var_type.startswith(RESULT):
            var_type, type_env = self.result_type(var_type[len(RESULT):], call.caller)
            if var_type == UNTRACKED:
                return
            if var_type is None:
                if not self.quiet(head):
                    self.unresolved(caller, expr, line)
                return
        if self.family == "go" and var_type == golang.THIS:
            head, var_type = "this", None
        if head == "Self" and cls is not None and len(chain) >= 2:  # Rust / Swift: the enclosing type
            target = ("sym", cls)
            for part in chain[1:]:
                target = self.member(target, part) if target is not None else None
            if target is not None and target[0] == "sym":
                return self.resolved(caller, target[1], line, expr)
            return self.unresolved(caller, expr, line)

        if head == "this" or head == "self":
            if cls is None or len(chain) < 2:
                return
            if len(chain) == 2:
                return self.method_call(caller, cls, chain[1], line, expr, certain=True)
            ftype = self.r.field_type(cls, chain[1])
            if ftype is not None and len(chain) == 3:
                return self.typed_call(caller, ftype, chain[2], line, expr, self.r.envs.get(self.r.class_file[cls], self))
            if ftype is not None and len(chain) > 3:
                written, env = self.through_fields(ftype, self.r.envs.get(self.r.class_file[cls], self), chain[2:-1])
                if written is not None:
                    return self.typed_call(caller, written, chain[-1], line, expr, env)
            return self.unresolved(caller, expr, line)
        if head == "super":
            if cls is None or len(chain) != 2:
                return
            found = [m for base in self.r.bases.get(cls, []) for m in self.r.find_method(base, chain[1])][:1]
            return self.resolved(caller, found[0], line, expr) if found else None

        if var_type is not None:  # a parameter or local with a declared type
            if len(chain) == 2:
                return self.typed_call(caller, var_type, chain[1], line, expr, type_env)
            if len(chain) > 2:  # v.field.method(): follow the declared field types
                written, env = self.through_fields(var_type, type_env, chain[1:-1])
                if written is not None:
                    return self.typed_call(caller, written, chain[-1], line, expr, env)
                return self.unresolved(caller, expr, line)
            return None
        if cls is not None and head not in self.env and self.r.field_type(cls, head) is not None:
            ftype = self.r.field_type(cls, head)  # Java/Kotlin/C#: a field used without `this.`
            if len(chain) == 2:
                return self.typed_call(caller, ftype, chain[1], line, expr)
        if cls is not None and len(chain) == 1 and self.family in IMPLICIT_THIS and head not in self.env:
            if self.r.find_method(cls, head):  # implicit this.m()
                return self.method_call(caller, cls, head, line, expr, certain=True)

        target = self.lookup(head)
        if target is None:
            if self.family == "jvm" or self.family == "cs":
                cls_q = self.resolve_type(expr) if len(chain) > 1 else None
                if cls_q is not None and cls_q[0] == "sym":
                    return self.resolved(caller, cls_q[1], line, expr)
            from_library = self.external_wildcards and (len(chain) == 1 or self.family == "dart") \
                and self.family in ("dart", "swift", "rust", "c")
            if not self.quiet(head) and not from_library:
                self.unresolved(caller, expr, line)  # a name not declared here may come from a library import
            return
        if target[0] == "mod" and len(chain) == 1:  # const Repo = require("./repo"); new Repo()
            target = self.r.js.lookup_export(self.r, target[1], "default") or target
        for i, part in enumerate(chain[1:], 1):
            if target[0] == "ext":
                return
            last = i == len(chain) - 1
            nxt = self.member(target, part)
            if not last and target[0] == "sym" and self.r.decl_by_id[target[1]].kind == "class":
                # Foo.instance.bar(): a static field or getter in the middle of the chain has a type
                written = self.r.field_type(target[1], part)
                env = self.r.envs.get(self.r.class_file[target[1]], self)
                if written is None and nxt is not None and nxt[0] == "sym" and self.r.decl_by_id[nxt[1]].returns:
                    written, env = self.r.decl_by_id[nxt[1]].returns, self.r.envs.get(self.r.file_of[nxt[1]], self)
                if written is not None:
                    if written in (UNTRACKED, golang.THIS) or written.startswith(RESULT):
                        return
                    typ = env.resolve_type(written)
                    if typ is None or typ[0] != "sym" or self.r.decl_by_id[typ[1]].kind != "class":
                        return  # a library type: not a project link
                    if i == len(chain) - 2:
                        return self.typed_call(caller, written, chain[-1], line, expr, env)
                    nxt = typ
            if nxt is None:
                if target[0] == "sym" and self.r.decl_by_id[target[1]].kind == "class" \
                        and self.r.has_foreign_base(target[1]):
                    return
                return self.unresolved(caller, expr, line)
            target = nxt
        if target[0] == "sym":
            self.resolved(caller, target[1], line, expr)

    def method_call(self, caller: str, cls: str, name: str, line: int, expr: str, certain: bool) -> None:
        found = self.r.find_method(cls, name)
        if not found:
            if self.r.has_foreign_base(cls):
                return  # inherited from a library type or trait: not a project link
            return self.unresolved(caller, expr, line)
        # Go has no virtual dispatch: a method of a struct is exactly that method
        overrides = [] if self.family == "go" else [m for m in self.r.overrides(cls, name) if m not in found]
        if certain and not overrides and len(found) == 1:
            return self.resolved(caller, found[0], line, expr)
        candidates = list(dict.fromkeys(found + overrides))
        self.inferred(caller, candidates, line, expr)

    def through_fields(self, written: str, env: "_FileEnv", fields: List[str]) -> Tuple[Optional[str], "_FileEnv"]:
        """The declared type of `v.a.b` given v's type: each step reads a field type in the class's own file."""
        for name in fields:
            if written in (UNTRACKED, golang.THIS, LOCAL) or written.startswith(RESULT):
                return None, env
            typ = env.resolve_type(written)
            if typ is None or typ[0] != "sym" or self.r.decl_by_id[typ[1]].kind != "class":
                return None, env
            nxt = self.r.field_type(typ[1], name)
            if nxt is None:
                return None, env
            written, env = nxt, self.r.envs.get(self.r.class_file[typ[1]], env)
        return written, env

    def result_type(self, callee: str, caller_decl: Optional[Decl], depth: int = 0) -> Tuple[Optional[str], "_FileEnv"]:
        """`x := f()` / `const x = svc.make()` / `let x = Foo::new()?.with_y().build()`: the declared result type, in
        the scope of the file that declares it (that is where its type names mean something)."""
        start, *methods = callee.split("|")
        if start.startswith("type:"):
            written, env = start[len("type:"):], self
        elif start.startswith("path:"):  # a field of a variable: v.a.b
            parts = start[len("path:"):].split(".")
            var_types = caller_decl.var_types if caller_decl is not None else self.f.var_types
            if parts[0] in ("this", "self"):
                cls = self.r.enclosing_class(caller_decl)
                written = self.r.field_type(cls, parts[1]) if cls else None
                env = self.r.envs.get(self.r.class_file.get(cls, ""), self) if cls else self
                parts = parts[1:]
            elif parts[0] in var_types and depth < 4:
                written, env = var_types[parts[0]], self
                if written.startswith(RESULT):
                    written, env = self.result_type(written[len(RESULT):], caller_decl, depth + 1)
            else:
                written, env = None, self
            if written is not None and len(parts) > 1:
                written, env = self.through_fields(written, env, parts[1:])
        else:
            written, env = self._callee_result(start, caller_decl, depth)
        for method in methods:
            if written is None or written == UNTRACKED:
                return None, self
            if method in PASS_THROUGH:  # unwrap(), clone(), ?: the same type
                continue
            typ = env.resolve_type(written)
            if typ is None or typ[0] != "sym" or self.r.decl_by_id[typ[1]].kind != "class":
                return None, self
            found = self.r.find_method(typ[1], method)
            decl = self.r.decl_by_id[found[0]] if found else None
            if decl is None or not decl.returns:
                return None, self
            written, env = decl.returns, self.r.envs.get(self.r.file_of[found[0]], env)
        return written, env

    def _callee_result(self, callee: str, caller_decl: Optional[Decl], depth: int) -> Tuple[Optional[str], "_FileEnv"]:
        parts = callee.split(".")
        target: Optional[Target] = None
        var_types = caller_decl.var_types if caller_decl is not None else self.f.var_types
        if parts[0] in ("this", "self") and len(parts) == 2:
            cls = self.r.enclosing_class(caller_decl)
            found = self.r.find_method(cls, parts[1]) if cls else []
            target = ("sym", found[0]) if found else None
        elif len(parts) == 2 and parts[0] in var_types and depth < 4:  # c := ts.Client(); res := c.Get()
            written, env = var_types[parts[0]], self
            if written.startswith(RESULT):
                written, env = self.result_type(written[len(RESULT):], caller_decl, depth + 1)
            if written in (None, UNTRACKED, golang.THIS, LOCAL):
                cls = self.r.enclosing_class(caller_decl) if written == golang.THIS else None
            else:
                typ = env.resolve_type(written)
                cls = typ[1] if typ is not None and typ[0] == "sym" else None
            found = self.r.find_method(cls, parts[1]) if cls else []
            target = ("sym", found[0]) if found else None
        else:
            if parts[0] in BUILTINS[self.family]:  # String::new(), fetch(), Vec::with_capacity(): a library value
                return UNTRACKED, self
            target = self.lookup(parts[0])
            for part in parts[1:]:
                if target is not None and target[0] == "ext":
                    break
                target = self.member(target, part) if target is not None else None
        if target is not None and target[0] == "ext":
            return UNTRACKED, self  # made by a library: its methods are not project links
        if target is None or target[0] != "sym":
            return None, self
        decl = self.r.decl_by_id[target[1]]
        if decl.kind == "class":  # Kotlin `val x = Invoice()` style constructor calls
            return decl.name, self.r.envs.get(self.r.file_of[target[1]], self)
        if not decl.returns:
            return None, self
        return decl.returns, self.r.envs.get(self.r.file_of[target[1]], self)

    def typed_call(self, caller: str, type_written: str, name: str, line: int, expr: str,
                   type_env: Optional["_FileEnv"] = None) -> None:
        target = (type_env or self).resolve_type(type_written)
        if target is None or target[0] != "sym" or self.r.decl_by_id[target[1]].kind != "class":
            return  # a library or global type: its methods are not part of the project's map
        cls = target[1]
        self.note_use(cls)
        # The compiler checks the declared type, so the call lands in that type's method -- unless a subclass
        # overrides it or the type is an interface / protocol / trait (then: possible, with the candidates).
        if not self.r.decl_by_id[cls].interface:
            return self.method_call(caller, cls, name, line, expr, certain=True)
        found = self.r.find_method(cls, name)
        candidates = list(dict.fromkeys(found + self.r.overrides(cls, name)))
        if not candidates:
            if self.r.has_foreign_base(cls):
                return
            return self.unresolved(caller, expr, line)
        self.inferred(caller, candidates, line, expr)

    def resolved(self, caller: str, target: str, line: int, expr: str) -> None:
        self.note_use(target)
        self.r.edge(caller, target, "calls", "resolved", self.f.rel_path, line, self.f, expr)

    def inferred(self, caller: str, candidates: List[str], line: int, expr: str) -> None:
        to_id = candidates[0] if len(candidates) == 1 else f"ambiguous:{expr}"
        self.r.edge(caller, to_id, "calls", "inferred", self.f.rel_path, line, self.f, expr, candidates=candidates)

    def unresolved(self, caller: str, expr: str, line: int) -> None:
        self.r.edge(caller, f"unresolved:{expr}", "calls", "unresolved", self.f.rel_path, line, self.f, expr)

    def types_used(self) -> None:
        """Types named in parameters, locals and fields count as uses (for the files a package import links to)."""
        for decl in self.f.decls:
            for written in list(decl.var_types.values()) + list(decl.field_types.values()):
                if written and written not in (golang.THIS, UNTRACKED, LOCAL) and not written.startswith(RESULT):
                    target = self.resolve_type(written)
                    if target is not None and target[0] == "sym":
                        self.note_use(target[1])

    # ---------------------------------------------------------------- package imports

    def finish(self) -> None:
        """An import of a whole package links to the files this file actually uses from it."""
        for imp, key in self.package_imports:
            files = sorted(self.used_files.get(key, set()))
            if not files and self.family == "go":
                files = [f for f in self.r.ns_files.get(key, []) if not f.endswith("_test.go")]
            for rel in files:
                if rel != self.f.rel_path:
                    self.r.edge(self.mod_id, f"mod:{rel}", "imports", "resolved", self.f.rel_path, imp.line, self.f,
                                imp.target)


def _nested(r: Resolver, cls: str) -> List[str]:
    if r.nested is None:
        r.nested = {}
        for i, d in r.decl_by_id.items():
            if d.parent is not None and d.kind == "class":
                r.nested.setdefault(d.parent.node_id, []).append(i)
    return r.nested.get(cls, [])


# ====================================================================== JS / TS module resolution


class _JsModules:
    def __init__(self, root: Path, rels: Set[str]) -> None:
        self.root = root
        self.rels = rels
        self.configs = _tsconfigs(root)  # dir -> (baseUrl dir, paths)
        self.workspaces = _workspace_packages(root)  # package name -> dir
        self._export_cache: Dict[Tuple[str, str], Optional[Target]] = {}

    @staticmethod
    def is_relative(spec: str) -> bool:
        return spec.startswith(".") or spec.startswith("/")

    def resolve(self, from_rel: str, spec: str) -> Optional[str]:
        base_dir = posixpath.dirname(from_rel)
        if self.is_relative(spec):
            return self._file(posixpath.normpath(posixpath.join(base_dir, spec)) if not spec.startswith("/") else spec.lstrip("/"))
        config = self._config_for(base_dir)
        if config is not None:
            base_url, paths = config
            for pattern, targets in paths:
                star = pattern.find("*")
                if star < 0 and spec != pattern:
                    continue
                if star >= 0 and not (spec.startswith(pattern[:star]) and spec.endswith(pattern[star + 1:])):
                    continue
                middle = spec[star:len(spec) - (len(pattern) - star - 1)] if star >= 0 else ""
                for tgt in targets:
                    found = self._file(posixpath.normpath(posixpath.join(base_url, tgt.replace("*", middle))))
                    if found:
                        return found
            if base_url is not None:
                found = self._file(posixpath.normpath(posixpath.join(base_url, spec)))
                if found:
                    return found
        name = _npm_package(spec)
        if name in self.workspaces:
            rest = spec[len(name):].lstrip("/")
            pkg_dir = self.workspaces[name]
            for candidate in ([posixpath.join(pkg_dir, rest), posixpath.join(pkg_dir, "src", rest)] if rest else
                              [posixpath.join(pkg_dir, "src", "index"), posixpath.join(pkg_dir, "index"),
                               *_package_entry(self.root, pkg_dir)]):
                found = self._file(posixpath.normpath(candidate))
                if found:
                    return found
        return None

    def _file(self, path: str) -> Optional[str]:
        path = path[2:] if path.startswith("./") else path
        if path in self.rels:
            return path
        stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", path)  # TS ESM imports "./x.js" for "./x.ts"
        for candidate in (path, stem):
            for ext in JS_EXTS:
                if candidate + ext in self.rels:
                    return candidate + ext
            for ext in JS_EXTS:
                if f"{candidate}/index{ext}" in self.rels:
                    return f"{candidate}/index{ext}"
        return None

    def _config_for(self, directory: str):
        d = directory
        while True:
            if d in self.configs:
                return self.configs[d]
            if d in ("", "."):
                return self.configs.get("")
            d = posixpath.dirname(d)

    def lookup_export(self, r: Resolver, rel: str, name: str, guard: Optional[Set[Tuple[str, str]]] = None) -> Optional[Target]:
        key = (rel, name)
        if key in self._export_cache:
            return self._export_cache[key]
        guard = guard or set()
        if key in guard:
            return None
        guard.add(key)
        result = self._lookup_export(r, rel, name, guard)
        self._export_cache[key] = result
        return result

    def _lookup_export(self, r: Resolver, rel: str, name: str, guard: Set[Tuple[str, str]]) -> Optional[Target]:
        facts = r.files.get(rel)
        if facts is None:
            return None
        imported: Dict[str, Tuple[Import, str]] = {}
        for imp in facts.imports:
            if imp.reexport:
                continue
            if imp.default:
                imported[imp.default] = (imp, "default")
            for orig, local in imp.names:
                imported[local] = (imp, orig)
            if imp.alias:
                imported[imp.alias] = (imp, "*")

        def local_name(local: str) -> Optional[Target]:
            for decl in facts.decls:
                if decl.parent is None and decl.name == local:
                    return ("sym", decl.node_id)
            if local in imported:
                imp, orig = imported[local]
                target = self.resolve(rel, imp.target)
                if target is None:
                    return ("ext", r.external(_npm_package(imp.target))) if not self.is_relative(imp.target) else None
                return ("mod", target) if orig == "*" else self.lookup_export(r, target, orig, guard)
            return None

        if name == "default":
            for decl in facts.decls:
                if decl.default_export and decl.parent is None:
                    return ("sym", decl.node_id)
            if facts.default_name:
                return local_name(facts.default_name)
        for decl in facts.decls:
            if decl.parent is None and decl.exported and decl.name == name and not decl.default_export:
                return ("sym", decl.node_id)
        for local, exported in facts.local_exports:
            if exported == name:
                return local_name(local)
        for imp in facts.imports:
            if not imp.reexport:
                continue
            target = self.resolve(rel, imp.target)
            if target is None:
                continue
            for orig, exported in imp.names:
                if exported == name:
                    return self.lookup_export(r, target, orig, guard)
            if imp.alias == name:
                return ("mod", target)
        for imp in facts.imports:  # export * from "./x"
            if imp.reexport and imp.wildcard and name != "default":
                target = self.resolve(rel, imp.target)
                if target is not None:
                    found = self.lookup_export(r, target, name, guard)
                    if found is not None:
                        return found
        if facts.default_name and name != "default":  # module.exports = { ... } / an object exported as default
            return None
        return None


def _read_jsonc(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", raw, flags=re.DOTALL)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    return json.loads(raw)


def _tsconfigs(root: Path) -> Dict[str, Tuple[Optional[str], List[Tuple[str, List[str]]]]]:
    out = {}
    for path in _project_files(root, ("tsconfig.json", "jsconfig.json", "tsconfig.base.json")):
        rel_dir = posixpath.dirname(path.relative_to(root).as_posix())
        try:
            data = _read_jsonc(path)
        except (ValueError, OSError):
            continue
        options = data.get("compilerOptions", {}) or {}
        base = options.get("baseUrl")
        if base is None and isinstance(data.get("extends"), str) and data["extends"].startswith("."):
            try:  # one level of `extends`, the usual monorepo layout
                parent = (path.parent / data["extends"]).resolve()
                parent = parent if parent.suffix == ".json" else parent.with_suffix(".json")
                pdata = _read_jsonc(parent).get("compilerOptions", {}) or {}
                if pdata.get("baseUrl") is not None:
                    base = posixpath.relpath(posixpath.join(posixpath.dirname(parent.relative_to(root).as_posix()),
                                                            pdata["baseUrl"]), rel_dir or ".")
                options = {**pdata, **options}
            except (ValueError, OSError):
                pass
        paths = options.get("paths") or {}
        base_dir = posixpath.normpath(posixpath.join(rel_dir, base)) if base is not None else (rel_dir or "")
        if base is None and not paths:
            continue
        out[rel_dir if rel_dir != "." else ""] = (
            "" if base_dir == "." else base_dir,
            [(pattern, list(targets)) for pattern, targets in paths.items() if isinstance(targets, list)],
        )
    return out


def _workspace_packages(root: Path) -> Dict[str, str]:
    out = {}
    for path in _project_files(root, ("package.json",)):
        try:
            name = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("name")
        except (ValueError, OSError):
            continue
        if isinstance(name, str) and name:
            out[name] = posixpath.dirname(path.relative_to(root).as_posix())
    return out


def _package_entry(root: Path, pkg_dir: str) -> List[str]:
    try:
        data = json.loads((root / pkg_dir / "package.json").read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return []
    return [posixpath.join(pkg_dir, re.sub(r"\.(d\.ts|js|mjs|cjs)$", "", data[k]))
            for k in ("types", "typings", "module", "main", "source") if isinstance(data.get(k), str)]


def _npm_package(spec: str) -> str:
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0].replace("node:", "") or spec


# ====================================================================== Rust crates


class _RustCrates:
    """Cargo crates: the module path of a file comes from where it sits under the crate's src/."""

    def __init__(self, root: Path) -> None:
        self.crates: List[Tuple[str, str]] = []  # (crate dir, crate name), longest dir first
        for path in _project_files(root, ("Cargo.toml",)):
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"\[package\][^\[]*?^\s*name\s*=\s*\"([^\"]+)\"", raw, re.MULTILINE | re.DOTALL)
            if m:
                crate_dir = posixpath.dirname(path.relative_to(root).as_posix())
                self.crates.append((crate_dir, m.group(1).replace("-", "_")))
        self.crates.sort(key=lambda c: -len(c[0]))
        self.has_lib = {d for d, _ in self.crates if (root / d / "src" / "lib.rs").is_file()}
        self._cache: Dict[str, str] = {}

    def files_of(self, key: str, files) -> List[str]:
        if not hasattr(self, "_by_key"):
            self._by_key: Dict[str, List[str]] = {}
            for rel, facts in files.items():
                if facts.language == "rust":
                    self._by_key.setdefault(self.module_key(rel), []).append(rel)
        return self._by_key.get(key, [])

    def crate_key(self, name: str) -> str:
        return f"rust:{name.replace('-', '_')}"

    def module_key(self, rel: str) -> str:
        if rel in self._cache:
            return self._cache[rel]
        crate_dir, name = next(((d, n) for d, n in self.crates if not d or rel.startswith(d + "/")), ("", ""))
        rest = rel[len(crate_dir) + 1:] if crate_dir else rel
        parts = rest[:-3].split("/") if rest.endswith(".rs") else rest.split("/")
        if parts and parts[0] == "src":
            parts = parts[1:]
            if parts and parts[0] == "bin":  # each binary is a crate of its own
                name, parts = f"{name}/bin/{parts[1] if len(parts) > 1 else 'main'}", parts[2:]
            elif parts == ["main"] and crate_dir in self.has_lib:  # src/main.rs next to lib.rs: the binary
                name, parts = f"{name}/bin/main", []
        elif parts and parts[0] in ("tests", "examples", "benches"):
            name, parts = f"{name}/{parts[0]}/{parts[1] if len(parts) > 1 else ''}", parts[2:]
        if parts and parts[-1] in ("mod", "lib", "main"):
            parts = parts[:-1]
        key = "rust:" + "::".join([name or "crate"] + parts)
        self._cache[rel] = key
        return key


# ====================================================================== Swift modules


def _swift_module(rel: str) -> str:
    """SwiftPM: Sources/<Target>/...; otherwise the top folder (an Xcode target), "" for files at the root."""
    parts = rel.split("/")
    for i, part in enumerate(parts[:-1]):
        if part in ("Sources", "Tests") and i + 1 < len(parts) - 1:
            return parts[i + 1]
    return parts[0] if len(parts) > 1 else ""


# ====================================================================== Dart packages


class _DartPackages:
    def __init__(self, root: Path, rels: Set[str]) -> None:
        self.rels = rels
        self.packages: Dict[str, str] = {}
        for path in _project_files(root, ("pubspec.yaml",)):
            try:
                m = re.search(r"^name:\s*([A-Za-z0-9_]+)", path.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
            except OSError:
                continue
            if m:
                self.packages[m.group(1)] = posixpath.dirname(path.relative_to(root).as_posix())
        self._cache: Dict[Tuple[str, str], Optional[Target]] = {}

    def resolve(self, from_rel: str, uri: str) -> Optional[str]:
        if uri.startswith("dart:"):
            return None
        if uri.startswith("package:"):
            name, _, path = uri[len("package:"):].partition("/")
            if name not in self.packages:
                return None
            rel = posixpath.normpath(posixpath.join(self.packages[name], "lib", path))
        else:
            rel = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), uri))
        rel = rel[2:] if rel.startswith("./") else rel
        return rel if rel in self.rels else None

    def lookup_export(self, r: Resolver, rel: str, name: str, guard: Optional[Set[str]] = None) -> Optional[Target]:
        key = (rel, name)
        if key in self._cache:
            return self._cache[key]
        guard = guard or set()
        if rel in guard or name.startswith("_"):
            return None
        guard.add(rel)
        facts = r.files.get(rel)
        result: Optional[Target] = None
        if facts is not None:
            found = [d.node_id for d in facts.decls if d.parent is None and not d.receiver and d.name == name]
            if found:
                result = ("sym", r.pick(found))
            for imp in facts.imports if result is None else []:
                target = self.resolve(rel, imp.target)
                if target is None:
                    continue
                if imp.alias == "part" and not imp.static:  # names declared in a part file
                    result = self.lookup_export(r, target, name, guard)
                elif imp.reexport and (not imp.names or name in {n for n, _ in imp.names}):
                    result = self.lookup_export(r, target, name, guard)
                if result is not None:
                    break
        self._cache[key] = result
        return result


# ====================================================================== C / C++ includes


def _c_include(from_rel: str, target: str, paths: List[str]) -> Optional[str]:
    """`#include "x/y.h"`: next to the including file, else the project file whose path ends with it."""
    near = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), target))
    if near in paths:
        return near
    suffix = "/" + target.lstrip("./")
    matches = [p for p in paths if p == target or p.endswith(suffix)]
    if not matches:
        return None
    here = posixpath.dirname(from_rel).split("/")
    return max(matches, key=lambda p: (len(posixpath.commonprefix([here, p.split("/")])), -len(p)))


# ====================================================================== Go modules


def _go_modules(root: Path) -> Dict[str, str]:
    """module path -> directory (relative POSIX) for every go.mod in the project."""
    out = {}
    for path in _project_files(root, ("go.mod",)):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("module "):
                    out[line.split(None, 1)[1].strip().strip('"')] = posixpath.dirname(path.relative_to(root).as_posix())
                    break
        except OSError:
            continue
    return out


def _go_dir(modules: Dict[str, str], import_path: str) -> Optional[str]:
    best = None
    for mod, directory in modules.items():
        if import_path == mod or import_path.startswith(mod + "/"):
            if best is None or len(mod) > len(best[0]):
                best = (mod, directory)
    if best is None:
        return None
    rest = import_path[len(best[0]):].lstrip("/")
    return posixpath.normpath(posixpath.join(best[1], rest)) if rest or best[1] else ""


def _looks_internal_go(modules: Dict[str, str], import_path: str) -> bool:
    return any(import_path == m or import_path.startswith(m + "/") for m in modules)


def _go_external(import_path: str) -> str:
    parts = import_path.split("/")
    if "." in parts[0] and len(parts) >= 3:  # github.com/org/repo/sub -> github.com/org/repo
        return "/".join(parts[:3])
    return import_path


def _dotted_external(dotted: str) -> str:
    parts = dotted.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else dotted


# ====================================================================== files


_SKIP = {"node_modules", ".git", "dist", "build", "out", ".next", ".nuxt", "coverage", "vendor", "target", ".gradle",
         "bin", "obj", ".venv", "venv", ".pmap", "__pycache__", ".idea", ".vscode", ".turbo", ".cache"}


def _project_files(root: Path, names: Iterable[str]) -> List[Path]:
    wanted = set(names)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not d.startswith("._")]
        out.extend(Path(dirpath) / f for f in filenames if f in wanted)
    return sorted(out)
