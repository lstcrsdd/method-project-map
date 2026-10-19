"""What a language extractor returns for one file, plus small tree-sitter helpers shared by all extractors."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# grammar name -> (python module, function returning the language pointer)
GRAMMARS: Dict[str, Tuple[str, str]] = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "javascript": ("tree_sitter_javascript", "language"),
    "go": ("tree_sitter_go", "language"),
    "java": ("tree_sitter_java", "language"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "swift": ("tree_sitter_swift", "language"),
    "dart": ("tree_sitter_dart", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
}

_parsers: Dict[str, object] = {}


def grammar_available(grammar: str) -> bool:
    try:
        importlib.import_module("tree_sitter")
        importlib.import_module(GRAMMARS[grammar][0])
        return True
    except ImportError:
        return False


def parser_for(grammar: str):
    """A cached tree-sitter parser; ImportError when tree-sitter or the grammar is not installed."""
    if grammar not in _parsers:
        from tree_sitter import Language, Parser

        module_name, func = GRAMMARS[grammar]
        language = Language(getattr(importlib.import_module(module_name), func)())
        _parsers[grammar] = Parser(language)
    return _parsers[grammar]


# ------------------------------------------------------------------ facts


@dataclass
class Decl:
    """A class-like type, a function or a method declared in a file."""

    name: str
    kind: str  # "class" (also interface, struct, enum, record, object) | "function" | "method"
    line_start: int
    line_end: int
    parent: Optional["Decl"] = None  # enclosing type of a method or nested type
    signature: Optional[str] = None
    doc: Optional[str] = None
    exported: bool = True
    default_export: bool = False  # JS/TS `export default`
    namespace: Optional[str] = None  # Java/Kotlin package, C# namespace, Go package directory
    bases: List[Tuple[str, int]] = field(default_factory=list)  # (type as written, line): extends/implements/embeds
    var_types: Dict[str, str] = field(default_factory=dict)  # parameters and locals with a declared or `new` type
    field_types: Dict[str, str] = field(default_factory=dict)  # class fields / properties with a type
    receiver: Optional[str] = None  # Go: the receiver type of a method before it is attached
    interface: bool = False  # an interface (calls through it are never certain)
    returns: Optional[str] = None  # declared result type (first result in Go; Promise<T> / Task<T> unwrapped)
    declaration_only: bool = False  # C/C++: declared here (a header), defined elsewhere
    variants: List[str] = field(default_factory=list)  # enum cases: `Kind::A(..)` / `.a` make a value of the enum
    node_id: str = ""  # filled in by the resolver

    def qualified_parts(self) -> List[str]:
        parts, d = [], self
        while d is not None:
            parts.append(d.name)
            d = d.parent
        return parts[::-1]


@dataclass
class Import:
    line: int
    target: str  # JS/TS module specifier, Go import path, Java/Kotlin/C# dotted name
    names: List[Tuple[str, str]] = field(default_factory=list)  # (imported name, local name)
    default: Optional[str] = None  # JS/TS default import
    alias: Optional[str] = None  # JS/TS `* as x`, Go package alias, C#/Kotlin alias
    wildcard: bool = False  # Java/Kotlin `.*`; C# `using Namespace;`
    static: bool = False  # Java `import static`, C# `using static`
    reexport: bool = False  # JS/TS `export {...} from` / `export * from`
    type_only: bool = False  # TS `import type`
    is_global: bool = False  # C# `global using`
    scope: Optional[str] = None  # Rust: the inline module (`mod x { use ...; }`) the import sits in


@dataclass
class Call:
    line: int
    caller: Optional[Decl]  # innermost declaration the call is made in; None = top level of the file
    chain: List[str]  # callee as a dotted chain: ["tax", "compute"], ["this", "repo", "save"], ["Invoice"]
    new: bool = False  # `new X()`, Go composite literal `X{}`, JSX `<X />`


@dataclass
class FileFacts:
    rel_path: str
    language: str  # "typescript" | "javascript" | "go" | "java" | "kotlin" | "csharp"
    line_end: int
    doc: Optional[str] = None
    namespace: Optional[str] = None  # package / namespace the file declares (first one for C#)
    decls: List[Decl] = field(default_factory=list)
    imports: List[Import] = field(default_factory=list)
    calls: List[Call] = field(default_factory=list)
    var_types: Dict[str, str] = field(default_factory=dict)  # top-level variables with a declared or `new` type
    local_exports: List[Tuple[str, str]] = field(default_factory=list)  # JS/TS `export { local as exported }`
    default_name: Optional[str] = None  # JS/TS `export default someName`
    syntax_errors: int = 0


# ------------------------------------------------------------------ helpers


def text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace") if node is not None else ""


def line_of(node) -> int:
    return node.start_point[0] + 1


def end_line_of(node) -> int:
    return node.end_point[0] + 1


def walk(node) -> Iterator:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def squash(s: str, limit: int = 220) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


_DOC_NOISE = re.compile(r"^(copyright|license|licensed|spdx|eslint|prettier|@ts-|tslint|jshint|go:build|\+build|nolint)",
                        re.IGNORECASE)


def doc_summary(comment: str) -> Optional[str]:
    """First sentence of a doc comment (`/** */`, `//`, `///`, `#`), without markup."""
    lines = []
    for raw in comment.splitlines():
        line = raw.strip()
        line = re.sub(r"^(/\*\*?|\*/|\*|///?|//)", "", line).strip()
        line = re.sub(r"\*/$", "", line).strip()
        if line.startswith("@"):  # JSDoc / KDoc / Javadoc tags end the summary
            break
        lines.append(line)
    body = " ".join(part for part in lines if part)
    body = re.sub(r"</?(summary|remarks|para|c|code|see[^>]*|paramref[^>]*)/?>", " ", body)
    body = re.sub(r"\{@(link|code)\s+([^}]*)\}", r"\2", body)
    body = re.sub(r"\s+", " ", body).strip()
    if not body or _DOC_NOISE.match(body):
        return None
    match = re.match(r"(.+?[.!?])(\s|$)", body)
    return squash(match.group(1) if match else body, 200)


def preceding_comment(node, src: bytes, is_comment: Callable[[object], bool]) -> Optional[str]:
    """Doc comment right above a declaration (adjacent comment siblings, no blank line in between)."""
    parts = []
    prev = node.prev_sibling
    expected_line = node.start_point[0]
    while prev is not None and is_comment(prev) and prev.end_point[0] >= expected_line - 1:
        parts.append(text(prev, src))
        expected_line = prev.start_point[0]
        prev = prev.prev_sibling
    if not parts:
        return None
    return doc_summary("\n".join(reversed(parts)))


UNTRACKED = "?"  # var_types value: typed, but with a builtin / array / union type the map does not follow
LOCAL = "~"  # var_types value: a local variable whose type the map does not know (calls on it stay unresolved)
RESULT = "()"  # var_types prefix: the variable holds the result of calling this chain ("()" + "pkg.NewFoo")
# After RESULT: "<callee>|m1|m2" = the callee's result, then the results of calling m1, m2 on it in turn;
# "type:<T>" as the callee means "a value of type T" (a struct literal, `new T()` ...).
PASS_THROUGH = {"unwrap", "expect", "unwrap_or_default", "unwrap_or", "unwrap_or_else", "clone", "to_owned", "as_ref",
                "as_mut", "borrow", "borrow_mut", "lock", "read", "write", "deref", "deref_mut", "get_or_insert_with",
                "copied", "cloned", "await", "first", "last", "single", "firstWhere", "requireNonNull"}


def result_type_name(type_text: Optional[str]) -> Optional[str]:
    """Declared result type as a plain type name: `Promise<Order>` / `Task<Order>` -> Order, `(T, error)` -> T."""
    if not type_text:
        return None
    t = type_text.strip().lstrip(":").strip()
    if t.startswith("(") and t.endswith(")"):  # Go multiple results
        t = t[1:-1].split(",")[0].strip().split()[-1] if t[1:-1].strip() else ""
    m = re.match(r"^(Promise|Task|ValueTask|Future|CompletableFuture|Deferred|Observable|Optional)\s*<\s*(.+)\s*>$", t)
    if m:
        t = m.group(2)
    name = base_type_name(t)
    return name if name and re.match(r"^[A-Za-z_][\w.]*$", name) else None


def base_type_name(type_text: str) -> str:
    """`List<Foo>` -> `List`, `pkg.Base[T]` -> `pkg.Base`, `*Foo` -> `Foo`, `Foo?` -> `Foo`."""
    t = type_text.strip()
    t = re.sub(r"<.*>|\[.*\]", "", t, flags=re.DOTALL)
    t = t.lstrip("*&").rstrip("?!").strip()
    t = re.sub(r"\s+", "", t)
    return t
