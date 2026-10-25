"""Links between languages that no compiler checks: Flutter platform channels, the C ABI, JNI, flutter_rust_bridge,
UniFFI and Tauri commands, and hand-offs through the system (Apple NetworkExtension, Android services).

A Flutter app reaches Swift, Kotlin and C++ through a channel name and a method name, both plain strings; Dart,
Swift, C++ and Kotlin reach Rust or C through symbol names. Neither compiler sees the other side, so neither does
an IDE, and a rename on one side breaks the app only at run time. This pass runs after the resolver and turns such
links into `calls` edges with a `bridge` record, e.g.
    {"via": "channel", "name": "audio/player", "method": "pause", "to": {"path": "...", "line": 88}}
- `resolved`: the names match exactly one target on that side;
- `inferred`: several targets match, or the system picks the target (NetworkExtension, Android intents);
- `unresolved` with `"missing": "<platform>"`: a channel method that a platform handling the channel does not handle.

Where both ends are known for certain, the two sides are also compared, and an edge whose sides differ carries
`"mismatch": {"kind", "detail"}` (see `mismatches`): a Kotlin `external fun` and its JNI function with another number
of parameters or a type that cannot match (`String` against `jboolean`); a C ABI function whose Dart binding, call
site or bound header declaration has another number of parameters than its definition (a header declaration is no
call, so it is named in the detail of the function's links, `"declarations"`); a Dart channel name that no code of a
platform folder of the same package registers while that platform registers a near name of the namespace that no
Dart code uses (an unresolved edge to that registration). A gap whose platform handles a method no Dart code sends
names it in `"orphan"`. Anything uncertain (macros, variadics, overloads, unknown type aliases, names built at run
time, files the parser could not read fully) is skipped, never reported.
"""

from __future__ import annotations

import os
import posixpath
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pmap.langs.base import Decl, FileFacts
from pmap.models import ActualEdge, ActualMap, EdgeLocation

SOURCE = "pmap-bridge"
LANGUAGE_NAMES = {"dart": "Dart", "swift": "Swift", "kotlin": "Kotlin", "java": "Java", "cpp": "C/C++", "c": "C",
                  "rust": "Rust", "go": "Go", "typescript": "TypeScript", "javascript": "JavaScript",
                  "csharp": "C#", "python": "Python"}
VIA_NAMES = {"channel": "channel", "event-channel": "event channel", "message-channel": "message channel", "pigeon": "Pigeon",
             "ffi": "C ABI", "jni": "JNI", "frb": "flutter_rust_bridge", "uniffi": "UniFFI", "tauri": "Tauri command",
             "network-extension": "NetworkExtension", "android-intent": "Android intent"}
PLATFORM_DIRS = ("ios", "macos", "android", "windows", "linux", "web")
# A file is read for links only when it mentions one of these (most files of a big project do not).
_HINTS = (b"Channel", b"MethodCall", b"lookup", b"@Native", b"crateApi", b"uniffi_", b"no_mangle", b"export_name",
          b"extern", b"external", b"native", b"tauri", b"startVPNTunnel", b"stopVPNTunnel", b"startTunnel",
          b"sendProviderMessage", b"Intent", b"_cdecl", b"//export", b"JNIEXPORT", b"__TAURI__")
_C_EXPORT_MARKS = re.compile(r'extern\s+"C"|JNIEXPORT|FFI_PLUGIN_EXPORT|dllexport|EMSCRIPTEN_KEEPALIVE|'
                             r'visibility\s*\(\s*"default"\s*\)')


def wants_source(language: str, rel: str, src: bytes) -> bool:
    if language == "cpp" and rel.endswith(".c"):
        return True  # every function a C file defines is a symbol other languages may call
    return any(hint in src for hint in _HINTS)


# ====================================================================== text helpers

_STR = re.compile(r"""(?P<q>['"])(?P<s>(?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)""")
_PREFIXED_STR = re.compile(r"""(?:r|u8|u|U|L|@)?(?P<q>['"])(?P<s>(?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)""")


# Comments and string literals in one pass, so `//` inside a string is never taken for a comment. A `'` that does not
# close on its line (a Rust lifetime) is left alone.
_COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*.*?(?:\*/|\Z)|"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''
                                r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.DOTALL)


def _blank_comments(text: str) -> str:
    """Comments become spaces (newlines kept), so patterns never match inside them and lines keep their numbers."""
    def blank(m: re.Match) -> str:
        token = m.group(0)
        return token if token[0] in "\"'" else re.sub(r"[^\n]", " ", token)

    return _COMMENT_OR_STRING.sub(blank, text)


def _literal(arg: str) -> Optional[str]:
    m = _PREFIXED_STR.fullmatch(arg.strip())
    return m.group("s") if m else None


def _method_literal(arg: str, lang: str) -> Optional[str]:
    """A method name written as one plain string; None when it is not one or when it is built at run time: Dart and
    Kotlin `$x` / `${x}` (not in a Dart `r'...'` string), Swift `\\(x)`."""
    arg = arg.strip()
    value = _literal(arg)
    if value is None:
        return None
    if lang == "swift" and "\\(" in value:
        return None
    if lang in ("dart", "kotlin") and not (lang == "dart" and arg[:1] == "r"):
        i = 0
        while i < len(value):
            if value[i] == "\\":
                i += 2
                continue
            if value[i] == "$":
                return None
            i += 1
    return value


def _clean_channel(name: str) -> str:
    """Interpolated suffixes (Pigeon's `\\(channelSuffix)`, `$suffix`) are not part of the shared name."""
    return re.split(r"\\\(|\$\{|\$[A-Za-z_]", name)[0]


def _open_paren(text: str, pos: int) -> int:
    """Position of the `(` after pos once spaces and generic arguments `<...>` are skipped (also the `>` that closes
    an enclosing template, as in `make_unique<X<Y>>(...)`); -1 when anything else comes first."""
    n, i = len(text), pos
    while i < n:
        c = text[i]
        if c.isspace() or c == ">":
            i += 1
        elif c == "<":
            depth = 0
            while i < n:
                if text[i] == "<":
                    depth += 1
                elif text[i] == ">":
                    depth -= 1
                    if depth == 0:
                        break
                elif text[i] in ";{}":
                    return -1
                i += 1
            i += 1
        elif c == "(":
            return i
        else:
            return -1
    return -1


def _args(text: str, open_pos: int) -> Tuple[List[str], int]:
    """Top-level arguments of the call whose `(` is at open_pos, and the position after its `)`."""
    depth, i, n, start, args = 0, open_pos, len(text), open_pos + 1, []
    while i < n:
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return [a for a in args if a.strip()], i + 1
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        elif c in "\"'":
            m = _STR.match(text, i)
            if m:
                i = m.end()
                continue
        i += 1
    return [], -1


def _block(text: str, open_brace: int) -> int:
    """Position just after the `}` matching the `{` at open_brace (strings skipped)."""
    depth, i, n = 0, open_brace, len(text)
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c in "\"'":
            m = _STR.match(text, i)
            if m:
                i = m.end()
                continue
        i += 1
    return n


def _parameters(signature: str) -> Tuple[List[str], Set[str]]:
    """Positional and named parameter names of a Dart signature `(String method, [Map a = const {}], {int? b})`."""
    start = signature.find("(")
    if start < 0:
        return [], set()
    inner = signature[start + 1:]
    depth, end = 1, len(inner)
    for i, c in enumerate(inner):
        depth += c in "(<[{"
        depth -= c in ")>]}"
        if depth == 0:
            end = i
            break
    positional: List[str] = []
    named: Set[str] = set()
    depth, part, in_named = 0, "", False
    for c in inner[:end] + ",":
        if c in "<([":
            depth += 1
        elif c in ">)]":
            depth -= 1
        if c == "{" and depth == 0:
            in_named = True
            continue
        if c == "}" and depth == 0:
            continue
        if c == "," and depth == 0:
            name = re.findall(r"[A-Za-z_]\w*", part.split("=")[0].replace("[", " ").replace("]", " "))
            if name:
                (named.add(name[-1]) if in_named else positional.append(name[-1]))
            part = ""
            continue
        part += c
    return positional, named


def _argument(args: List[str], index: Optional[int], name: str) -> Optional[str]:
    for arg in args:
        m = re.match(r"\s*(\w+)\s*:(?!:)", arg)
        if m and m.group(1) == name:
            return arg[m.end():]
    positional = [a for a in args if not re.match(r"\s*\w+\s*:(?!:)", a)]
    return positional[index] if index is not None and index < len(positional) else None


def _lower_camel(parts: List[str]) -> str:
    words = [w for p in parts for w in p.split("_") if w]
    return words[0][:1].lower() + words[0][1:] + "".join(w[:1].upper() + w[1:] for w in words[1:]) if words else ""


def _jni_escape(s: str) -> str:
    out = []
    for ch in s:
        if ch == "/":
            out.append("_")
        elif ch == "_":
            out.append("_1")
        elif ch == ";":
            out.append("_2")
        elif ch == "[":
            out.append("_3")
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("_0%04x" % ord(ch))
    return "".join(out)


def _family(language: str) -> str:
    """Languages that link into one binary: C with C++ (.c files are "c", the rest "cpp"), Java with Kotlin."""
    return {"cpp": "c", "kotlin": "jvm", "java": "jvm"}.get(language, language)


def platform_of(path: str, language: str) -> str:
    """`android`, `ios`, `macos`, `windows`, `linux` from a Flutter-style folder, else the language. Folders below a
    source root are packages, not platforms (`src/main/java/com/acme/web/`, `Sources/Kit/linux/`)."""
    for part in path.split("/")[:-1]:
        if part in PLATFORM_DIRS:
            return part
        if part in _SOURCE_ROOTS:
            break
    return language


_SOURCE_ROOTS = ("java", "kotlin", "Sources")


def platform_name(platform: str) -> str:
    return _PLATFORM_NAMES.get(platform) or LANGUAGE_NAMES.get(platform, platform)


_PLATFORM_NAMES = {"ios": "iOS", "macos": "macOS", "android": "Android", "windows": "Windows", "linux": "Linux",
                   "web": "Web"}


# ====================================================================== comparing the two sides


def _group(text: str, start: int, quotes: str = "\"'", angles: bool = True) -> Optional[Tuple[List[str], int]]:
    """The top-level comma-separated parts inside the bracket at `start` (a trailing comma allowed) and the position
    after its closing bracket; None when brackets or strings do not balance. `->` and `=>` are no brackets; with
    angles=False `<` and `>` are left to the caller."""
    stack: List[str] = []
    parts: List[str] = []
    i, n, begin = start, len(text), start + 1
    while i < n:
        c = text[i]
        if c in quotes:
            m = _STR.match(text, i)
            if m is None:
                return None
            i = m.end()
            continue
        if c in "([{" or (angles and c == "<"):
            stack.append(c)
        elif c in ")]}" or (angles and c == ">" and text[i - 1] not in "-="):
            if not stack or "([{<".index(stack.pop()) != ")]}>".index(c):
                return None
            if not stack:
                parts.append(text[begin:i])
                parts = [p.strip() for p in parts]
                if parts[-1] == "":
                    parts.pop()
                return (parts, i + 1) if all(parts) else None
        elif c == "," and len(stack) == 1:
            parts.append(text[begin:i])
            begin = i + 1
        i += 1
    return None


_MACRO_LIKE = re.compile(r"[A-Z_][A-Z0-9_]*")
_DEFINE = re.compile(r"(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)")
# What may stand before a function's name on the line that declares or defines it (`static inline int32_t `,
# `extern int ZEXPORT `, `void *`); `return x = f(` and `if (f(` are calls.
_TYPE_HEAD = re.compile(r"[ \t]*(?:[A-Za-z_][\w:]*(?:[ \t]*<[^<>;()\n]*>)?[ \t\*&]+)+")
_NOT_TYPES = {"return", "if", "while", "for", "switch", "else", "case", "do", "sizeof", "goto", "throw", "new",
              "delete", "typedef", "co_return", "co_await", "co_yield"}


def _params(text: Optional[str], lang: str, rel: str) -> Optional[List[str]]:
    """The parameters the first `(...)` of a signature declares (`(void)` is none); None when that is not certain:
    cut short, variadic, a macro, a parameter present only under a condition (Rust `#[cfg(...)]`, C `#if`), or `()`
    in a C header or C file (no prototype)."""
    if not text:
        return None
    start = text.find("(")
    got = _group(text, start, '"' if lang in ("rust", "swift") else "\"'") if start >= 0 else None
    if got is None:
        return None
    parts = got[0]
    if lang == "rust" and any(re.search(r"#\s*\[\s*cfg", p) for p in parts):
        return None
    if lang in ("cpp", "c"):
        if any("#" in p for p in parts):
            return None
        if parts == ["void"]:
            return []
        if not parts and rel.endswith((".c", ".h")):
            return None
        if any(_MACRO_LIKE.fullmatch(p) for p in parts):
            return None
    if any("..." in p for p in parts) and lang not in ("kotlin", "java"):
        return None  # variadic (`...`, Go `...int`, Swift `Int...`); Java `String...` is one array parameter
    return parts


def _distance(a: str, b: str) -> int:
    """Edits from a to b: a letter added, dropped or changed, or two neighbours swapped (`foucs` / `focus` is 1)."""
    before: List[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, before[j - 2] + 1)
            cur.append(cost)
        before, prev = prev, cur
    return prev[-1]


def _pair(missing: List[str], orphans: List[Tuple[str, object]], near=None) -> Dict[str, Tuple[str, object, int]]:
    """missing name -> (closest orphan name, its payload, how many other orphans went to it). Each orphan goes to the
    missing name closest to it, so one orphan never stands beside two missing names; with `near(missing, orphan)`
    an orphan goes only to a name near it."""
    given: Dict[str, List[Tuple[int, str, object]]] = {}
    for name, payload in orphans:
        options = [m for m in missing if near is None or near(m, name)]
        if not options:
            continue
        best = min(options, key=lambda m: (_distance(m, name), m))
        given.setdefault(best, []).append((_distance(best, name), name, payload))
    out = {}
    for m, items in given.items():
        _d, name, payload = min(items, key=lambda t: (t[0], t[1]))
        out[m] = (name, payload, len(items) - 1)
    return out


def _near_name(a: str, b: str) -> bool:
    """Two channel names of one namespace that differ by a slip: case, `_` / `-` / `.`, or a few letters for their
    length (`levels` / `level`, `media_library` / `medialibrary`); `levels` / `carplay_state` are two channels."""
    x, y = (re.sub(r"[_\-.]", "", n.rpartition("/")[2].lower()) for n in (a, b))
    return _distance(x, y) <= max(1, max(len(x), len(y)) // 4)


def _natural(text: str) -> tuple:
    """A sort key that orders numbers inside text by value (`:9` before `:10`, `Parameter2` before `Parameter10`)."""
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", text))


def _quoted(name: str, text: str) -> bool:
    """`name` appears in text as a whole string literal (`'audio/levels'` is not `'audio/level'`)."""
    return name in text and re.search("['\"]" + re.escape(name) + "['\"]", text) is not None


# JNI: what a Kotlin / Java parameter type is on the JVM, and what a JNI parameter type can receive. A category is
# ("prim", width) or ("ref", "object" | "string" | "class" | "throwable" | "array" | "array:<element>"). A primitive is
# compared by its size and whether it is an integer or a float (`i4`, `f8`): `Boolean` against `i8` or `Short`
# against `u16` passes the same bits, `Int` against `jlong` or `jfloat` does not.
_JVM_PRIMITIVES = ("boolean", "byte", "char", "short", "int", "long", "float", "double")
_KOTLIN_PRIMITIVES = {p.capitalize(): p for p in _JVM_PRIMITIVES}
_WIDTHS = {"boolean": "i1", "byte": "i1", "char": "i2", "short": "i2", "int": "i4", "long": "i8", "float": "f4",
           "double": "f8"}
_JNI_TYPES: Dict[str, Tuple[str, str]] = {"jsize": ("prim", "i4"), "jarray": ("ref", "array")}
for _p in _JVM_PRIMITIVES:
    _JNI_TYPES["j" + _p] = ("prim", _WIDTHS[_p])
    _JNI_TYPES["j" + _p + "Array"] = _JNI_TYPES["J" + _p.capitalize() + "Array"] = ("ref", "array:" + _p)
for _names, _cat in ((("jobject", "JObject"), ("ref", "object")), (("jclass", "JClass"), ("ref", "class")),
                     (("jstring", "JString"), ("ref", "string")), (("jthrowable", "JThrowable"), ("ref", "throwable")),
                     (("jobjectArray", "JObjectArray"), ("ref", "array:object"))):
    for _n in _names:
        _JNI_TYPES[_n] = _cat
# Fixed-size native types (Rust `i64` is `jlong`); `usize`, `bool`, C `int` and `long` are left out.
for _bits, _width in ((8, "i1"), (16, "i2"), (32, "i4"), (64, "i8")):
    for _n in (f"i{_bits}", f"u{_bits}", f"int{_bits}_t", f"uint{_bits}_t"):
        _JNI_TYPES[_n] = ("prim", _width)
for _names, _width in ((("f32", "float"), "f4"), (("f64", "double"), "f8")):
    for _n in _names:
        _JNI_TYPES[_n] = ("prim", _width)


def _jvm_param(part: str, lang: str) -> Tuple[str, Optional[str]]:
    """(name, type as written) of a Kotlin `name: Type = x` or Java `final Type name`; type None when unknown."""
    part = re.sub(r"@[\w.:]+(?:\s*\([^()]*\))?\s*", "", part).split("=", 1)[0].strip()
    if lang == "kotlin":
        name, sep, typ = part.partition(":")
        name = name.split()[-1] if name.split() else ""
        if not sep or re.search(r"\bvararg\b", part):
            return name, None
        return name, typ.strip()
    words = [w for w in part.split() if w != "final"]
    if len(words) < 2 or words[-1].endswith("]"):
        return (words[-1] if words else ""), None
    return words[-1], " ".join(words[:-1])


def _jvm_category(typ: Optional[str], lang: str) -> Optional[Tuple[str, str]]:
    if not typ:
        return None
    t = re.sub(r"\s+", "", typ)
    if lang == "kotlin":  # any other name may be a typealias of anything: unknown
        nullable = t.endswith("?")
        t = t.rstrip("?")
        t = t[len("kotlin."):] if t.startswith("kotlin.") else t
        if t in _KOTLIN_PRIMITIVES:
            return ("ref", "object") if nullable else ("prim", _WIDTHS[_KOTLIN_PRIMITIVES[t]])
        if t == "String":
            return ("ref", "string")
        if t.endswith("Array") and t[:-5] in _KOTLIN_PRIMITIVES:
            return ("ref", "array:" + _KOTLIN_PRIMITIVES[t[:-5]])
        if t.startswith("Array<"):
            return ("ref", "array:object")
        return None
    t = t[len("java.lang."):] if t.startswith("java.lang.") else t
    for suffix in ("[]", "..."):
        if t.endswith(suffix):
            element = t[:-len(suffix)]
            return ("ref", "array:" + (element if element in _JVM_PRIMITIVES else "object"))
    if t in _JVM_PRIMITIVES:
        return ("prim", _WIDTHS[t])
    if t == "String":
        return ("ref", "string")
    if re.fullmatch(r"[A-Za-z_$][\w.$]*(?:<.*>)?", t):  # Java has no aliases: any other name is a class
        return ("ref", "object")
    return None


def _native_param(part: str, lang: str) -> Tuple[str, Optional[str]]:
    """(name, type) of a Rust `name: Type` or C `Type name` parameter."""
    if lang == "rust":
        name, sep, typ = part.partition(":")
        return re.sub(r"\bmut\b", "", name).strip(), (typ.strip() if sep else None)
    words = re.findall(r"[A-Za-z_]\w*|[*&\[]", re.sub(r"/\*.*?\*/", " ", part))
    if any(w in ("*", "&", "[") for w in words):
        return part, None
    words = [w for w in words if w not in ("const", "volatile")]
    if len(words) == 2:
        return words[1], words[0]
    return (part, words[0]) if len(words) == 1 else (part, None)


def _jni_category(typ: Optional[str], lang: str) -> Optional[Tuple[str, str]]:
    if not typ or "*" in typ or "&" in typ:
        return None
    t = typ.strip()
    if lang == "rust":
        t = t.split("<", 1)[0].strip().rsplit("::", 1)[-1].strip()
    return _JNI_TYPES.get(t)


def _contradicts(a: Optional[Tuple[str, str]], b: Optional[Tuple[str, str]]) -> bool:
    """Only what can never be the same value: a primitive against a reference or a primitive of another size or
    kind, a string against an array, arrays of different elements. A plain object reference matches any reference."""
    if a is None or b is None:
        return False
    if a[0] != b[0]:
        return True
    if a[0] == "prim":
        return a[1] != b[1]
    x, y = a[1], b[1]
    if "object" in (x, y):
        return False
    fx, fy = x.split(":")[0], y.split(":")[0]
    if fx != fy:
        return True
    return fx == "array" and x != y and ":" in x and ":" in y


def _count(n: object, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# ====================================================================== one file


class _Src:
    def __init__(self, facts: FileFacts, raw: str) -> None:
        self.f = facts
        self.rel = facts.rel_path
        self.lang = facts.language
        self.raw = raw  # with comments: only for quick "may this file matter" checks
        self._text: Optional[str] = None
        self._starts: Optional[List[int]] = None
        self.mod_id = f"mod:{facts.rel_path}"
        self._spans: Optional[list] = None

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = _blank_comments(self.raw)
        return self._text

    @property
    def starts(self) -> List[int]:
        if self._starts is None:
            self._starts = [0] + [m.end() for m in re.finditer("\n", self.raw)]
        return self._starts

    @property
    def spans(self) -> list:
        if self._spans is None:
            self._spans = sorted(((d.line_start, d.line_end, d) for d in self.f.decls if d.node_id),
                                 key=lambda s: (s[0], -s[1]))
        return self._spans

    def line(self, offset: int) -> int:
        return bisect_right(self.starts, offset)

    def line_text(self, line: int) -> str:
        start = self.starts[line - 1] if 0 < line <= len(self.starts) else 0
        end = self.starts[line] - 1 if line < len(self.starts) else len(self.text)
        return self.text[start:end]

    def prefix(self, offset: int) -> str:
        """The text of offset's line before offset."""
        return self.text[self.starts[self.line(offset) - 1]:offset]

    def decl_at(self, line: int) -> Optional[Decl]:
        best = None
        for start, end, decl in self.spans:
            if start > line:
                break
            if end >= line:
                best = decl  # sorted by start, so the last one that contains the line is the innermost
        return best

    def owner(self, line: int) -> str:
        decl = self.decl_at(line)
        return decl.node_id if decl is not None else self.mod_id

    def decl_after(self, line: int, within: int = 3) -> Optional[Decl]:
        for start, _end, decl in self.spans:
            if line < start <= line + within:
                return decl
        return None


@dataclass
class _Site:
    """One end of a link: where it sits and the declaration that owns it."""
    src: _Src
    line: int
    owner: str


@dataclass
class _Channel:
    kind: str  # "Method" | "Event" | "BasicMessage"
    name: str
    site: _Site
    var: Optional[str]
    end: int  # offset after the constructor call's `)`
    start: int
    exact: bool = True  # False when the name is only the fixed part (`"simple_view/" + id`, `'audio/$id'`)


@dataclass
class _Call:
    kind: str
    channel: Optional[str]
    certain: bool  # the channel is known for sure (bound variable or the file's only channel)
    method: str
    site: _Site


@dataclass
class _Forwarder:
    name: str
    index: Optional[int]  # position of the method-name parameter; None when it is a named parameter
    param: str
    channel: Optional[str]  # None: the channel is the receiver (an extension method on MethodChannel)
    certain: bool
    rel: str
    line: int = 0  # where the forwarder is declared


@dataclass
class _Handler:
    channel: Optional[str]
    certain: bool
    method: str
    site: _Site


@dataclass
class _Export:
    symbol: str
    node: str
    site: _Site


# ====================================================================== patterns

_CONST = re.compile(r"""\b([A-Za-z_]\w*)(?:\s*\[\s*\])?\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:r|u8|L|@)?(['"])([^'"\\\n]*)\2"""
                    r"""\s*[;,)\n]""")
_CHANNEL = re.compile(r"(?:\bnew\s+)?(?:\bflutter::)?\b(?:Flutter)?(?:Optional)?(Method|Event|BasicMessage)Channel\b")
_BIND = re.compile(r"(\w+)\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:const\s+|new\s+)?(?:std::make_(?:unique|shared)<\s*)?$")
# Each pattern starts at a literal (`.invokeMethod`, `switch`, `.method`), so scanning a big file stays cheap; the
# receiver of a call is read back from the text just before it.
_INVOKE = re.compile(r"(?:\?\.|!\.|\.|->)\s*(?:invokeMethod|invokeListMethod|invokeMapMethod|InvokeMethod)\b")
_LISTEN = re.compile(r"(?:\?\.|!\.|\.)\s*receiveBroadcastStream\b")
_SEND = re.compile(r"(?:\?\.|!\.|\.)\s*send\b")
_RECEIVER = re.compile(r"(\w+|\))\s*$")
_METHOD_ALIAS = re.compile(r"\b(?:let|var|val|final|const|auto|String)\s+(\w+)\s*(?::\s*\w+\??\s*)?=\s*\w+\s*\.\s*"
                           r"method(?:_name\s*\(\s*\))?(?![\w(])")
_BARE_INVOKE = re.compile(r"(?<![\w.$>])(?:invokeMethod|invokeListMethod|invokeMapMethod)\b")
_SUBJECT = r"\w+\s*\.\s*method(?:_name\s*\(\s*\))?(?![\w(])"
_SWITCH = re.compile(r"\b(?:switch|when)\b\s*\(?\s*" + _SUBJECT + r"\s*\)?\s*\{")
_CASE = re.compile(r"""\bcase\s+((?:(['"])[^'"\n]*\2\s*,\s*)*(['"])[^'"\n]*\3)\s*:""")
_WHEN_ENTRY = re.compile(r"""(?m)^\s*((?:"[^"\n]*"\s*,\s*)*"[^"\n]*")\s*->""")
_COMPARE = [  # `call.method == "x"`, `call.method.equals("x")`, `call.method_name().compare("x") == 0`, `"x" == call.method`
    re.compile(r"""\.\s*method(?:_name\s*\(\s*\))?(?![\w(])\s*(?:==\s*|\.\s*(?:equals|compare)\s*\(\s*)(['"])([^'"\n]+)\1"""),
    re.compile(r"""(['"])([^'"\n]+)\1\s*(?:==\s*|\.\s*equals\s*\(\s*)""" + _SUBJECT),
]
_PLATFORM_CHECK = re.compile(r"\bPlatform\.is(Android|IOS|MacOS|Windows|Linux|Fuchsia)\b|"
                             r"\bTargetPlatform\.(android|iOS|macOS|windows|linux|fuchsia)\b")
_PLATFORM_KEYS = {"ios": "ios", "macos": "macos", "android": "android", "windows": "windows", "linux": "linux"}
_STREAM_HANDLER = re.compile(r"setStreamHandler\s*\(\s*(?:self\.)?(\w+)\s*\(")
_PIGEON = re.compile(r"^dev\.flutter\.pigeon\.[\w$]+\.(\w+)\.(\w+)$")
_SET_HANDLER = re.compile(r"\b(?:setMethodCallHandler|SetMethodCallHandler)\b")
_MESSAGE_HANDLER = re.compile(r"\bsetMessageHandler\b")
_LOOKUP = re.compile(r"\b_?lookup(?:Function)?\b")
_NATIVE = re.compile(r"@Native\s*(?=<|\()")
_FRB = re.compile(r"\.\s*(crate[A-Z]\w*)\s*\(")
_UNIFFI = re.compile(r"\buniffi_([a-z0-9_]+?)_fn_(func|method|constructor)_(\w+)")
_TAURI_INVOKE = re.compile(r"""\binvoke\s*(?:<[^()]*?>)?\s*\(\s*(['"`])([\w.:-]+)\1""")
_TAURI_COMMAND = re.compile(r"#\[\s*tauri::command\b[^\]]*\]")
_RUST_EXPORT = re.compile(r'#\[\s*(?:unsafe\s*\(\s*)?(?:no_mangle|export_name\s*=\s*"([^"]+)")\s*\)?\s*\]')
_FN_AFTER = re.compile(r"\bfn\s+(\w+)")
_CDECL = re.compile(r'@_cdecl\s*\(\s*"(\w+)"\s*\)')
_FUNC_AFTER = re.compile(r"\bfunc\s+(\w+)")
_GO_EXPORT = re.compile(r"^//export\s+(\w+)", re.MULTILINE)
_EXTERN_C_BLOCK = re.compile(r'\bextern\s+"C"\s*\{')
_NE_CALLS = [(re.compile(r"\.\s*startVPNTunnel\b"), "startTunnel"),
             (re.compile(r"\.\s*startTunnel\s*\(\s*options"), "startTunnel"),
             (re.compile(r"\.\s*stopVPNTunnel\b"), "stopTunnel"),
             (re.compile(r"\.\s*stopTunnel\s*\(\s*\)"), "stopTunnel"),
             (re.compile(r"\.\s*sendProviderMessage\b"), "handleAppMessage")]
NE_PROVIDERS = {"NEPacketTunnelProvider", "NEAppProxyProvider", "NETunnelProvider", "NEDNSProxyProvider",
                "NEFilterDataProvider", "NEFilterControlProvider", "NETransparentProxyProvider", "NEProvider"}
_INTENT = re.compile(r"\bIntent\s*\(\s*[^,()]+,\s*(\w+)::class\.java\s*\)|\bnew\s+Intent\s*\(\s*[^,()]+,\s*(\w+)\.class\s*\)")
# Files read when a channel name is about to be reported as absent from a platform folder (`quoted_nearby`).
_NEARBY_EXTS = {".m", ".mm", ".h", ".hh", ".hpp", ".c", ".cc", ".cpp", ".cxx", ".swift", ".kt", ".kts", ".java",
                ".js", ".ts", ".rs", ".go", ".cs"}
_SKIP_DIRS = {"build", "Pods", "DerivedData", "node_modules", "ephemeral", "target"}
_INTENT_VERBS = {"startForegroundService": "onStartCommand", "startService": "onStartCommand", "bindService": "onBind",
                 "stopService": "onDestroy", "startActivityForResult": "onCreate", "startActivity": "onCreate"}


# ====================================================================== the pass


class _Linker:
    def __init__(self, resolver, sources: Dict[str, bytes]) -> None:
        self.r = resolver
        self.srcs: Dict[str, _Src] = {}
        for rel, raw in sources.items():
            facts = resolver.files.get(rel)
            if facts is not None:
                self.srcs[rel] = _Src(facts, raw.decode("utf-8", "replace"))
        self.edges: List[ActualEdge] = []
        self.keys: Set[tuple] = set()
        self.drop: Set[Tuple[str, str]] = set()  # (owner, target symbol) of unresolved edges a link replaces
        self.retarget: Dict[Tuple[str, int, str], ActualEdge] = {}
        self.unnamed: Set[Tuple[str, str]] = set()  # (platform or "dart", kind) of channels whose name is not known
        self.dynamic_sends: Set[Optional[str]] = set()  # Dart channels (None: unknown) sent a method name not literal
        self.abi_sites: Dict[str, List[tuple]] = {}  # C symbol -> (role, language, site, edge, count) of its users
        self.disk: Dict[str, Optional[_Src]] = {}

    # ---------------------------------------------------------------- edges

    def link(self, site: _Site, target: str, confidence: str, via: str, name: str, to: Optional[_Site] = None,
             method: Optional[str] = None, candidates: Optional[List[str]] = None, missing: Optional[str] = None,
             label: Optional[str] = None) -> Optional[ActualEdge]:
        key = (site.owner, target, site.line, via)
        if key in self.keys or site.owner == target:
            return None
        self.keys.add(key)
        record: Dict[str, object] = {"via": via, "name": name}
        if method is not None:
            record["method"] = method
        if to is not None:
            record["to"] = {"path": to.src.rel, "line": to.line}
        if missing is not None:
            record["missing"] = missing
        symbol = label or (f"{name}#{method}" if method else name)
        edge = ActualEdge(from_id=site.owner, to_id=target, kind="calls", confidence=confidence, source=SOURCE,
                          target_symbol=f"{via} {symbol}", location=EdgeLocation(site.src.rel, site.line),
                          candidates=candidates, bridge=record)
        self.edges.append(edge)
        return edge

    def site(self, src: _Src, offset: int) -> _Site:
        line = src.line(offset)
        return _Site(src, line, src.owner(line))

    def decl_site(self, decl: Decl) -> _Site:
        rel = self.r.file_of.get(decl.node_id, "")
        src = self.srcs.get(rel) or _Src(self.r.files[rel], "")
        return _Site(src, decl.line_start, decl.node_id)

    def run(self) -> List[ActualEdge]:
        self.consts: Dict[str, Dict[str, str]] = {}
        self.project_consts: Dict[Tuple[str, str], Optional[str]] = {}
        self.forwarders: Dict[Tuple[str, str], _Forwarder] = {}  # (file if library-private, name) -> forwarder
        self.channels()
        exports = self.exports()
        self.c_abi(exports)
        self.jni(exports)
        self.abi_contracts(exports)
        self.frb()
        self.uniffi()
        self.tauri()
        self.network_extension()
        self.android_intents()
        self.finish()
        return self.edges

    def finish(self) -> None:
        """Drop the unresolved edges the links replace; header declarations give way to the definition they bind."""
        kept = []
        for e in self.r.edges:
            if e.confidence == "unresolved" and (e.from_id, e.target_symbol or "") in self.drop:
                continue
            if e.location is not None:
                swap = self.retarget.get((e.from_id, e.location.line, e.to_id))
                if swap is not None:
                    kept.append(swap)
                    continue
            kept.append(e)
        self.r.edges[:] = kept

    # ---------------------------------------------------------------- platform channels

    def channel_name(self, src: _Src, args: List[str], offset: Optional[int] = None) -> Tuple[Optional[str], bool]:
        """The channel's name and whether it is the whole name (not the fixed part of one built at run time)."""
        for arg in args:
            arg = re.sub(r"^\s*name\s*:\s*", "", arg)
            value = _literal(arg)
            whole = value is not None
            if value is None:  # "simple_view/" + id: the fixed part names the channel
                m = _PREFIXED_STR.match(arg.strip())
                value = m.group("s") if m and arg.strip()[m.end():].lstrip().startswith("+") else None
            if value:
                name = _clean_channel(value)
                return name, whole and name == value
        for arg in args:
            ident = re.sub(r"^\s*name\s*:\s*", "", arg).strip().split(".")[-1]
            if re.fullmatch(r"[A-Za-z_]\w*", ident):
                value = self.local_value(src, ident, offset) if offset is not None else None
                value = value or self.constant(src, ident)
                if value:
                    name = _clean_channel(value)
                    return name, name == value
        return None, False

    def local_value(self, src: _Src, name: str, offset: int) -> Optional[str]:
        """The string last assigned to `name` above offset in the same declaration (`final channelName = '…'`)."""
        decl = src.decl_at(src.line(offset))
        start = src.starts[decl.line_start - 1] if decl is not None else max(0, offset - 2000)
        found = None
        for m in re.finditer(r"\b" + re.escape(name) + r"\s*(?::\s*[\w.<>?]+\s*)?=\s*(?:r|@)?(['\"])([^'\"\\\n]*)\1",
                             src.text[start:offset]):
            found = m.group(2)
        return found

    def constants(self, src: _Src) -> Dict[str, str]:
        if src.rel not in self.consts:
            self.consts[src.rel] = {m.group(1): m.group(3) for m in _CONST.finditer(src.text)}
        return self.consts[src.rel]

    def constant(self, src: _Src, name: str) -> Optional[str]:
        """A string constant of this file, else the one value the project's files give that name."""
        value = self.constants(src).get(name)
        if value is not None:
            return value
        key = (src.lang, name)
        if key not in self.project_consts:
            values = {self.constants(other).get(name) for other in self.srcs.values()
                      if other is not src and other.lang == src.lang and name in other.raw} - {None}
            self.project_consts[key] = next(iter(values)) if len(values) == 1 else None
        return self.project_consts[key]

    def channels(self) -> None:
        declared: List[_Channel] = []
        by_file: Dict[str, List[_Channel]] = {}
        for src in self.srcs.values():
            if src.lang not in ("dart", "swift", "kotlin", "java", "cpp") or "Channel" not in src.raw:
                continue
            for m in _CHANNEL.finditer(src.text):
                paren = _open_paren(src.text, m.end())
                if paren < 0:
                    continue
                args, end = _args(src.text, paren)
                name, whole = self.channel_name(src, args, m.start())
                if not name:
                    self.unnamed.add(("dart" if src.lang == "dart" else platform_of(src.rel, src.lang), m.group(1)))
                    continue
                bind = _BIND.search(src.prefix(m.start()))
                ch = _Channel(m.group(1), name, self.site(src, m.start()), bind.group(1) if bind else None, end,
                              m.start(), whole)
                declared.append(ch)
                by_file.setdefault(src.rel, []).append(ch)
        if not declared:
            return
        self.by_file = by_file
        bound: Dict[Tuple[str, str], Set[str]] = {}  # (language, variable) -> channel names
        for ch in declared:
            if ch.var:
                bound.setdefault((ch.site.src.lang, ch.var), set()).add(f"{ch.kind}:{ch.name}")

        def channel_of(src: _Src, receiver: str, offset: int, kind: str) -> Tuple[Optional[str], bool]:
            mine = [c for c in by_file.get(src.rel, []) if c.kind == kind]
            if receiver == ")":
                inline = [c for c in mine if c.end == offset]
                if inline:
                    return inline[0].name, True
            here = {c.name for c in mine if c.var == receiver}
            if len(here) == 1:
                return next(iter(here)), True
            if here:  # one variable name per method, as Pigeon writes it: the declaration just above the call
                above = [c for c in mine if c.var == receiver and c.start < offset]
                if above:
                    return above[-1].name, True
            if len({c.name for c in mine}) == 1:
                return mine[0].name, True
            names = {v.split(":", 1)[1] for v in bound.get((src.lang, receiver), set()) if v.startswith(kind + ":")}
            if len(names) == 1:
                return next(iter(names)), True
            return None, False

        calls: List[_Call] = []
        handlers: List[_Handler] = []
        for src in self.srcs.values():
            if src.lang not in ("dart", "swift", "kotlin", "java", "cpp"):
                continue
            for pattern, kind, hint in ((_INVOKE, "Method", "nvoke"), (_LISTEN, "Event", "receiveBroadcastStream"),
                                        (_SEND, "BasicMessage", "send")):
                if hint not in src.raw:
                    continue
                for m in pattern.finditer(src.text):
                    before = src.text[max(0, m.start() - 160):m.start()]
                    recv = _RECEIVER.search(before)
                    if recv is None:
                        continue
                    recv_start = m.start() - len(before) + recv.start(1)
                    name, certain = channel_of(src, recv.group(1), recv_start + len(recv.group(1)), kind)
                    method = "listen" if kind == "Event" else "send" if kind == "BasicMessage" else None
                    if kind == "Method":
                        paren = _open_paren(src.text, m.end())
                        args, _ = _args(src.text, paren) if paren >= 0 else ([], -1)
                        method = _method_literal(args[0], src.lang) if args else None
                        if method is None and src.lang == "dart":
                            fw = self.note_forwarder(src, recv_start, args[0], name, certain) if args else None
                            if fw is None or not fw.name.startswith("_"):
                                self.dynamic_sends.add(name if certain else None)  # the methods sent: not all known
                    elif name is None:
                        continue  # `.send(` and `.receiveBroadcastStream(` only count on a known channel
                    if method:
                        calls.append(_Call(kind, name, certain, method, self.site(src, recv_start)))
            if src.lang == "dart" and "nvokeMethod" in src.raw:  # `invokeMethod(method, …)` inside an extension
                for m in _BARE_INVOKE.finditer(src.text):
                    paren = _open_paren(src.text, m.end())
                    args, _ = _args(src.text, paren) if paren >= 0 else ([], -1)
                    if args and _method_literal(args[0], "dart") is None:
                        fw = self.note_forwarder(src, m.start(), args[0], None, False)
                        if fw is None or not fw.name.startswith("_"):
                            self.dynamic_sends.add(None)
            found = self.dispatch(src, by_file.get(src.rel, [])) if "MethodCall" in src.raw and "method" in src.raw \
                else []
            handlers.extend(found)
            # A handler that answers every call alike (`setMethodCallHandler { _, result in result(flavor) }`)
            # handles each method of its channel.
            dispatched = {h.channel for h in found}
            for ch in by_file.get(src.rel, []):
                if ch.kind != "Method" or ch.name in dispatched:
                    continue
                own = re.compile(r"\b" + re.escape(ch.var) + r"\s*(?:\?\.|!\.|\.|->)\s*$") if ch.var else None
                for m in _SET_HANDLER.finditer(src.text):  # this channel's handler: on its variable, or chained
                    chained = src.text[ch.end:m.start()].strip() in (".", "?.", "->")
                    on_var = own is not None and own.search(src.text[max(0, m.start() - 80):m.start()])
                    cleared = re.match(r"\s*\(\s*(?:nil|null|nullptr)\s*\)", src.text[m.end():m.end() + 16])
                    if (chained or on_var) and not cleared:
                        handlers.append(_Handler(ch.name, True, "*", self.site(src, m.start())))
                        dispatched.add(ch.name)
                        break
        calls.extend(self.forwarded_calls(channel_of))
        self.match_channels(declared, calls, handlers)

    def note_forwarder(self, src: _Src, offset: int, arg: str, channel: Optional[str], certain: bool
                       ) -> Optional[_Forwarder]:
        """`Future<void> _call(String method, …) => _channel.invokeMethod(method, …)`: the method name is a parameter,
        so each call of `_call('pause', …)` is a call of the channel method `pause`."""
        decl = src.decl_at(src.line(offset))
        param = arg.strip()
        if decl is None or not re.fullmatch(r"[A-Za-z_]\w*", param):
            return None
        positional, named = _parameters(decl.signature or "")
        if param not in positional and param not in named:
            return None
        index = positional.index(param) if param in positional else None
        key = (src.rel if decl.name.startswith("_") else "", decl.name)
        if key not in self.forwarders:
            self.forwarders[key] = _Forwarder(decl.name, index, param, channel, certain, src.rel, decl.line_start)
        return self.forwarders[key]

    def forwarded_calls(self, channel_of) -> List[_Call]:
        """Calls of forwarders with a literal method name; a forwarder may call another one (`_call` → `call`)."""
        out: List[_Call] = []
        done: Set[Tuple[str, str]] = set()
        for _round in range(4):
            todo = [(k, f) for k, f in self.forwarders.items() if k not in done]
            if not todo:
                break
            for key, fw in todo:
                done.add(key)
                pattern = re.compile(r"(?:(\w+|\))\s*(?:\?\.|!\.|\.)\s*)?(?<![\w$])" + re.escape(fw.name)
                                     + r"\s*(?:<[^()]*?>)?\s*\(")
                if key[0]:  # a private forwarder: all its uses are calls in its file (no tear-off, no `part` file)
                    home = self.srcs.get(key[0])
                    uses = re.findall(r"(?<![\w$])" + re.escape(fw.name) + r"(?![\w$])", home.text) if home else []
                    if home is None or len(uses) != len(pattern.findall(home.text)) \
                            or re.search(r"^\s*part\b", home.text, re.MULTILINE):
                        self.dynamic_sends.add(fw.channel if fw.certain else None)
                else:  # a public one may be called from anywhere, this project or not
                    self.dynamic_sends.add(fw.channel if fw.certain else None)
                for src in self.srcs.values():
                    if src.lang != "dart" or fw.name not in src.raw or (key[0] and src.rel != key[0]):
                        continue
                    for m in pattern.finditer(src.text):
                        paren = m.end() - 1
                        args, _ = _args(src.text, paren)
                        arg = _argument(args, fw.index, fw.param)
                        if arg is None or (src.rel == fw.rel and 0 <= src.line(m.start()) - fw.line <= 2
                                           and re.fullmatch(r"[\w<>?,\s\[\]]+\s+\w+", arg.strip())):
                            continue  # no method name, or the forwarder's own declaration (`String method`)
                        channel, certain = fw.channel, fw.certain
                        if channel is None:  # an extension on MethodChannel: the receiver is the channel
                            if not m.group(1):
                                continue
                            channel, certain = channel_of(src, m.group(1), m.start(1) + len(m.group(1)), "Method")
                            if channel is None:
                                continue
                        method = _method_literal(arg, "dart")
                        start = m.start(1) if m.group(1) else m.start()
                        if method is not None:
                            out.append(_Call("Method", channel, certain, method, self.site(src, start)))
                        else:
                            again = self.note_forwarder(src, start, arg, channel, certain)
                            if again is None or not again.name.startswith("_"):
                                self.dynamic_sends.add(channel if certain else None)
        return out

    def dispatch(self, src: _Src, mine: List[_Channel]) -> List[_Handler]:
        """`switch (call.method) { case "x": }`, `when (call.method) { "x" -> }`, `call.method == "x"` ..."""
        found: Dict[Tuple[str, int], int] = {}
        for m in _SWITCH.finditer(src.text):
            brace = m.end() - 1
            end = _block(src.text, brace)
            body = src.text[brace:end]
            for case in _CASE.finditer(body):
                for lit in _STR.finditer(case.group(1)):
                    found.setdefault((lit.group("s"), brace + case.start()), brace + case.start())
            if "when" in m.group(0):
                for entry in _WHEN_ENTRY.finditer(body):
                    for lit in _STR.finditer(entry.group(1)):
                        found.setdefault((lit.group("s"), brace + entry.start(1)), brace + entry.start(1))
        for pattern in _COMPARE:
            for m in pattern.finditer(src.text):
                found.setdefault((m.group(2), m.start()), m.start())
        for alias in set(_METHOD_ALIAS.findall(src.text)):  # `let method = call.method` ... `if method == "init"`
            a = re.escape(alias)
            for m in re.finditer(r"\b(?:switch|when)\b\s*\(?\s*" + a + r"\s*\)?\s*\{", src.text):
                brace = m.end() - 1
                body = src.text[brace:_block(src.text, brace)]
                for entry in list(_CASE.finditer(body)) + (list(_WHEN_ENTRY.finditer(body)) if "when" in m.group(0)
                                                           else []):
                    for lit in _STR.finditer(entry.group(1)):
                        found.setdefault((lit.group("s"), brace + entry.start(1)), brace + entry.start(1))
            for m in re.finditer(r"(?<![\w.])" + a + r"\s*(?:==\s*|\.\s*equals\s*\(\s*)(['\"])([^'\"\n]+)\1|"
                                 r"(['\"])([^'\"\n]+)\3\s*(?:==\s*|\.\s*equals\s*\(\s*)" + a + r"(?![\w(])",
                                 src.text):
                found.setdefault((m.group(2) or m.group(4), m.start()), m.start())
        method_channels = [c for c in mine if c.kind == "Method"]
        names = sorted({c.name for c in method_channels})
        wiring = self.handler_wiring(src, method_channels) if len(names) > 1 else {}
        out = []
        for (method, _), offset in found.items():
            if len(names) == 1:
                channel, certain = names[0], True
            elif names:  # several channels in one file: the one whose handler holds this branch
                channel = self.wired_channel(src, offset, wiring)
                certain = channel is not None
                if channel is None:  # else the nearest one declared above
                    above = [c for c in method_channels if c.start < offset]
                    channel = (above[-1] if above else method_channels[0]).name
            else:
                channel, certain = None, False
            out.append(_Handler(channel, certain, method, self.site(src, offset)))
        return out

    def handler_wiring(self, src: _Src, channels: List[_Channel]) -> Dict[str, Tuple[Set[str], List[Tuple[int, int]]]]:
        """channel name -> (functions its `setMethodCallHandler(...)` names, spans of that call's arguments)."""
        wiring: Dict[str, Tuple[Set[str], List[Tuple[int, int]]]] = {}
        for ch in channels:
            if not ch.var:
                continue
            pattern = re.compile(r"\b" + re.escape(ch.var) + r"\s*(?:\?\.|!\.|\.|->)\s*(?:setMethodCallHandler|"
                                 r"SetMethodCallHandler)\b")
            delegate = re.compile(r"addMethodCallDelegate\s*\(\s*[\w.]+\s*,\s*channel\s*:\s*" + re.escape(ch.var)
                                  + r"\b")
            if delegate.search(src.text):  # a FlutterPlugin: its `handle(_:result:)` answers this channel
                wiring.setdefault(ch.name, (set(), []))[0].add("handle")
            for m in pattern.finditer(src.text):
                rest = src.text[m.end():m.end() + 2]
                start = m.end() + (len(rest) - len(rest.lstrip()))
                end = _block(src.text, start) if src.text[start:start + 1] == "{" else (
                    _args(src.text, start)[1] if src.text[start:start + 1] == "(" else -1)
                if end < 0:
                    continue
                funcs = set(re.findall(r"[A-Za-z_]\w*", src.text[start:end]))  # `handle`, `::methodHandler`, `x.handle(..)`
                entry = wiring.setdefault(ch.name, (set(), []))
                entry[0].update(funcs)
                entry[1].append((start, end))
        for funcs, _spans in wiring.values():  # one hop: `handle` that only hands over to `handleAsync`
            funcs.update(c.chain[-1] for c in src.f.calls if c.caller is not None and c.caller.name in funcs)
        return wiring

    def wired_channel(self, src: _Src, offset: int, wiring: Dict[str, Tuple[Set[str], List[Tuple[int, int]]]]
                      ) -> Optional[str]:
        inside = [name for name, (_funcs, spans) in wiring.items() if any(a <= offset < b for a, b in spans)]
        if len(inside) == 1:
            return inside[0]
        decl = src.decl_at(src.line(offset))
        owners = set()
        while decl is not None:
            owners.add(decl.name)
            decl = decl.parent
        named = [name for name, (funcs, _spans) in wiring.items() if funcs & owners]
        return named[0] if len(named) == 1 else None

    def match_channels(self, declared: List[_Channel], calls: List[_Call], handlers: List[_Handler]) -> None:
        handled: Dict[Tuple[str, str], List[_Handler]] = {}  # (channel, platform) -> handlers
        for h in handlers:
            if h.channel is not None:
                handled.setdefault((h.channel, platform_of(h.site.src.rel, h.site.src.lang)), []).append(h)
        found_gaps: List[Tuple[str, str, str, ActualEdge]] = []  # (channel, platform, method, edge) of Dart calls
        for call in calls:
            from_dart = call.site.src.lang == "dart"
            other_side = (lambda lang: lang != "dart") if from_dart else (lambda lang: lang == "dart")
            if call.kind == "Method":
                matches = [h for h in handlers if h.method in (call.method, "*") and other_side(h.site.src.lang)
                           and (call.channel is None or h.channel in (call.channel, None))
                           and (h.method != "*" or h.channel == call.channel)]
                by_platform: Dict[str, List[_Handler]] = {}
                for h in matches:
                    by_platform.setdefault(platform_of(h.site.src.rel, h.site.src.lang), []).append(h)
                for platform, found in list(by_platform.items()):  # a branch for the method beats "any method"
                    named = [h for h in found if h.method != "*"]
                    by_platform[platform] = named or found
                for platform, found in sorted(by_platform.items()):
                    exact = [h for h in found if h.channel == call.channel]
                    if len(found) == 1 and call.certain and found[0].certain and exact:
                        self.link(call.site, found[0].site.owner, "resolved", "channel", call.channel or "?",
                                  found[0].site, call.method)
                    else:
                        ids = sorted({h.site.owner for h in found})
                        self.link(call.site, ids[0] if len(ids) == 1 else f"ambiguous:{call.method}", "inferred",
                                  "channel", call.channel or "?", found[0].site, call.method, candidates=ids)
                if call.channel is None:
                    continue
                only = self.platform_guard(call.site)  # a call made only on some platforms
                for (channel, platform), hs in sorted(handled.items(), key=lambda kv: kv[0]):
                    if channel != call.channel or platform in by_platform or not other_side(hs[0].site.src.lang):
                        continue
                    if only is not None and platform not in only:
                        continue
                    gap = self.link(call.site, f"unresolved:channel {channel}#{call.method}@{platform}", "unresolved",
                                    "channel", channel, hs[0].site, call.method,
                                    missing=self.platform_label(platform, hs[0].site.src.lang))
                    if gap is not None and from_dart:
                        found_gaps.append((channel, platform, call.method, gap))
            else:
                via = "event-channel" if call.kind == "Event" else "message-channel"
                for ch in declared:
                    if ch.kind != call.kind or ch.name != call.channel or not other_side(ch.site.src.lang):
                        continue
                    target = self.stream_target(ch) if call.kind == "Event" else self.message_target(ch)
                    pigeon = _PIGEON.match(ch.name) if call.kind == "BasicMessage" else None
                    if pigeon:  # one generated channel per API method: name the API and the method
                        self.link(call.site, target.owner, "resolved", "pigeon", pigeon.group(1), target,
                                  pigeon.group(2))
                    else:
                        self.link(call.site, target.owner, "resolved", via, ch.name, target, call.method)
        self.unsent_branches(found_gaps, calls, handled)
        self.near_names(declared, calls, handlers)

    @staticmethod
    def platform_label(platform: str, lang: str) -> str:
        """`Kotlin (android)`, or `Swift` for a folder that names no platform."""
        name = LANGUAGE_NAMES.get(lang, lang)
        return f"{name} ({platform})" if platform != lang else LANGUAGE_NAMES.get(platform, platform)

    def unsent_branches(self, found: List[Tuple[str, str, str, ActualEdge]], calls: List[_Call],
                        handled: Dict[Tuple[str, str], List[_Handler]]) -> None:
        """A gap on a platform that has a branch for a method no Dart code sends on that channel (`"disconect"` beside
        a missing `"disconnect"`): the gap names that branch. Skipped when Dart sends a method name that is not a
        literal, since then the methods it sends are not all known, and when a Dart file this pass does not read
        (a `part` file, generated code) quotes the branch's name."""
        if not found or None in self.dynamic_sends:
            return
        sent: Dict[Optional[str], Set[str]] = {}
        for c in calls:
            if c.kind == "Method" and c.site.src.lang == "dart":
                sent.setdefault(c.channel, set()).add(c.method)
        groups: Dict[Tuple[str, str], List[Tuple[str, ActualEdge]]] = {}
        for channel, platform, method, edge in found:
            groups.setdefault((channel, platform), []).append((method, edge))
        for (channel, platform), items in sorted(groups.items(), key=lambda kv: kv[0]):
            if channel in self.dynamic_sends:
                continue
            known = sent.get(channel, set()) | sent.get(None, set())
            branches: Dict[str, _Handler] = {}
            for h in handled.get((channel, platform), []):
                if h.certain and h.channel == channel and h.method != "*" and h.method not in known:
                    branches.setdefault(h.method, h)
            if not branches:
                continue
            paired = _pair(sorted({m for m, _ in items}), sorted(branches.items()))
            for method, edge in items:
                if method not in paired or edge.bridge is None or edge.location is None:
                    continue
                name, h, _others = paired[method]
                if self.dart_quotes(edge.location.path, name):
                    continue
                edge.bridge["orphan"] = {"method": name, "path": h.site.src.rel,  # type: ignore[union-attr]
                                         "line": h.site.line, "platform": platform}  # type: ignore[union-attr]

    def near_names(self, declared: List[_Channel], calls: List[_Call], handlers: List[_Handler]) -> None:
        """Dart listens on event channel `ns/x` (or calls method channel `ns/x`); no code of a platform folder of the
        same package registers a channel under that name, and it registers `ns/y`, a name near it in the same
        namespace that no Dart code uses: name both. A platform without such a channel lacks the feature and stays
        silent; so does one where a channel name is unknown, where Dart builds a name of that namespace at run time,
        where code outside the platform folders (a local Swift package, shared code) registers `ns/x`, or where any
        file of the folder (one pmap does not read, too) quotes `ns/x`, or a Dart file it does not read quotes `ns/y`."""
        dart_names = {c.name for c in declared if c.site.src.lang == "dart"}
        platforms: Dict[str, List[_Channel]] = {}
        for c in declared:
            if c.site.src.lang != "dart":
                platforms.setdefault(platform_of(c.site.src.rel, c.site.src.lang), []).append(c)
        if not dart_names or not any(p in PLATFORM_DIRS for p in platforms):
            return
        # A name registered outside any platform folder may be built into every platform of its language.
        shared: Dict[str, Set[str]] = {}
        for platform, chans in platforms.items():
            if platform not in PLATFORM_DIRS:
                for c in chans:
                    shared.setdefault(_family(c.site.src.lang), set()).add(c.name)
        dart_srcs = [s for s in self.srcs.values() if s.lang == "dart"]
        served = {(h.channel, platform_of(h.site.src.rel, h.site.src.lang)) for h in handlers
                  if h.certain and h.channel is not None}
        for kind, via, mkind in (("Event", "event-channel", "event-channel-name"),
                                 ("Method", "channel", "channel-name")):
            if ("dart", kind) in self.unnamed:
                continue  # a Dart channel whose name is unknown may be the one a platform registers
            loose = {c.name for c in declared if c.site.src.lang == "dart" and c.kind == kind and not c.exact}
            wanted: Dict[str, List[_Call]] = {}
            for call in calls:
                if call.kind == kind and call.site.src.lang == "dart" and call.channel and call.certain:
                    wanted.setdefault(call.channel, []).append(call)
            dart_decls: Dict[str, _Channel] = {}
            for c in declared:
                if c.site.src.lang == "dart" and c.kind == kind:
                    dart_decls.setdefault(c.name, c)
            for platform, chans in sorted(platforms.items()):
                if platform not in PLATFORM_DIRS or (platform, kind) in self.unnamed \
                        or any(not c.exact for c in chans if c.kind == kind):
                    continue
                pool: Dict[str, _Channel] = {}  # the platform's channels of this kind that no Dart code uses
                for c in chans:
                    if c.kind != kind or "/" not in c.name or c.name in pool or c.name in dart_names \
                            or any(c.name.startswith(p) for p in loose):  # `EventChannel('player/$kind')`
                        continue
                    if kind == "Method" and (c.name, platform) not in served:
                        continue  # a method channel counts once it has a handler
                    if kind == "Event" and "etStreamHandler" not in c.site.src.text:  # set… / C++ Set…
                        continue
                    pool[c.name] = c
                registered = {c.name for c in chans}
                for family in {_family(c.site.src.lang) for c in chans}:
                    registered |= shared.get(family, set())
                missing = [n for n in sorted(wanted) if n not in registered and n in dart_decls
                           and dart_decls[n].exact]
                pool = {n: c for n, c in pool.items()
                        if any(m.rpartition("/")[0] == n.rpartition("/")[0] and _near_name(m, n) for m in missing)}
                if not pool:
                    continue
                pool = {n: c for n, c in pool.items() if not any(_quoted(n, s.text) for s in dart_srcs)}
                texts = [s.text for s in self.srcs.values()
                         if s.lang != "dart" and platform_of(s.rel, s.lang) == platform]
                missing = [n for n in missing if not any(_quoted(n, t) for t in texts)
                           and self.used_on(wanted[n], platform)]
                groups: Dict[Tuple[str, Optional[str]], Tuple[List[str], List[Tuple[str, _Channel]]]] = {}
                for n in missing:
                    key = (n.rpartition("/")[0], self.package_of(dart_decls[n].site.src.rel))
                    groups.setdefault(key, ([], []))[0].append(n)
                for n, c in sorted(pool.items()):
                    key = (n.rpartition("/")[0], self.package_of(c.site.src.rel))
                    if key in groups:
                        groups[key][1].append((n, c))
                for _key, (here, orphans) in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
                    if not orphans:
                        continue
                    for name, (other, ch, others) in sorted(_pair(here, orphans, _near_name).items()):
                        dart, site = dart_decls[name], ch.site  # type: ignore[attr-defined]
                        if self.quoted_nearby(site.src.rel, name) or self.dart_quotes(dart.site.src.rel, other):
                            continue
                        edge = self.link(dart.site, f"unresolved:{via} {name}@{platform}", "unresolved", via, name,
                                         site, "listen" if kind == "Event" else None,
                                         missing=self.platform_label(platform, site.src.lang))
                        if edge is not None and edge.bridge is not None:
                            edge.bridge["mismatch"] = {"kind": mkind, "detail": {"platform": platform,
                                                                                 "registered": other,
                                                                                 "others": others}}

    def dart_quotes(self, rel: str, name: str) -> bool:
        """A Dart file of rel's package that this pass does not read (a `part` or generated file, a folder pmap
        skips, a constants file) quotes `name`; True also when the package is too big to read, since then the name
        is not known to be unused."""
        texts = self.unread_dart(self.package_of(rel) or "")
        if texts is None:
            return True
        raw = name.encode()
        return any(raw in data and _quoted(name, data.decode("utf-8", "replace")) for data in texts)

    def unread_dart(self, folder: str) -> Optional[List[bytes]]:
        if not hasattr(self, "_unread_dart"):
            self._unread_dart: Dict[str, Optional[List[bytes]]] = {}
        if folder not in self._unread_dart:
            out: Optional[List[bytes]] = []
            seen = 0
            top = self.r.root / folder if folder else self.r.root
            for dirpath, dirnames, filenames in os.walk(top):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                for fname in filenames:
                    if not fname.endswith(".dart") or fname.startswith("._"):
                        continue
                    seen += 1
                    if seen > 4000:
                        out = None
                        break
                    path = Path(dirpath) / fname
                    try:
                        rel = path.relative_to(self.r.root).as_posix()
                    except ValueError:
                        rel = ""
                    if rel in self.srcs:
                        continue
                    try:
                        out.append(path.read_bytes()[:2_000_000])  # type: ignore[union-attr]
                    except OSError:
                        continue
                if out is None:
                    break
            self._unread_dart[folder] = out
        return self._unread_dart[folder]

    def package_of(self, rel: str) -> Optional[str]:
        """The folder of the nearest pubspec.yaml above a file (a Flutter app or plugin); None when there is none."""
        if not hasattr(self, "_packages"):
            self._packages: Dict[str, Optional[str]] = {}
        folder = posixpath.dirname(rel)
        seen = []
        while True:
            if folder in self._packages:
                found = self._packages[folder]
                break
            seen.append(folder)
            if (self.r.root / folder / "pubspec.yaml").is_file():
                found = folder
                break
            if not folder:
                found = None
                break
            folder = posixpath.dirname(folder)
        for f in seen:
            self._packages[f] = found
        return found

    def quoted_nearby(self, rel: str, name: str) -> bool:
        """Some file of the platform folder that holds rel (`ios`, `packages/a/android`; else its package) quotes
        the name, files pmap does not read included (Objective-C, a constants file); True also when that folder is
        too big to read, since then the name is not known to be absent."""
        parts = rel.split("/")[:-1]
        cut = next((i for i, p in enumerate(parts) if p in PLATFORM_DIRS), None)
        folder = "/".join(parts[:cut + 1]) if cut is not None else (self.package_of(rel) or "")
        top = self.r.root / folder if folder else self.r.root
        read, raw = 0, name.encode()
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if posixpath.splitext(fname)[1].lower() not in _NEARBY_EXTS or fname.startswith("._"):
                    continue
                read += 1
                if read > 4000:
                    return True
                try:
                    data = (Path(dirpath) / fname).read_bytes()[:2_000_000]
                except OSError:
                    continue
                if raw in data and _quoted(name, data.decode("utf-8", "replace")):
                    return True
        return False

    def used_on(self, uses: List[_Call], platform: str) -> bool:
        """Some of these Dart calls may run on the platform (no `Platform.isX` check or plugin class rules it out)."""
        for call in uses:
            only = self.platform_guard(call.site)
            if only is None or platform in only:
                return True
        return False

    def platform_guard(self, site: _Site) -> Optional[Set[str]]:
        """The platforms a Dart call may run on (see `platform_checks` and `plugin_platforms`); None: any."""
        checks = self.platform_checks(site)
        plugin = self.plugin_platforms(site)
        if plugin is None:
            return checks
        return plugin if checks is None else checks & plugin

    def plugin_platforms(self, site: _Site) -> Optional[Set[str]]:
        """The platforms whose plugin registration loads the Dart code at site, from the pubspec.yaml of its package:
        a platform's `dartPluginClass` (web: `pluginClass`) and its file (`dartFileName`, web: `fileName`) run on
        that platform only. None when the code belongs to no such class or file."""
        if site.src.lang != "dart":
            return None
        folder = self.package_of(site.src.rel)
        if folder is None:
            return None
        classes, files = self.plugin_spec(folder)
        if not classes and not files:
            return None
        rel = site.src.rel[len(folder) + 1:] if folder else site.src.rel
        found = set(files[rel[4:]]) if rel.startswith("lib/") and rel[4:] in files else None
        decl = site.src.decl_at(site.line)
        while decl is not None:
            if decl.kind == "class" and decl.name in classes:
                found = set(classes[decl.name]) if found is None else found & classes[decl.name]
                break
            decl = decl.parent
        return found

    def plugin_spec(self, folder: str) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
        """(Dart plugin class -> platforms, file below lib/ -> platforms) of the package in folder."""
        if not hasattr(self, "_plugins"):
            self._plugins: Dict[str, Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]] = {}
        if folder not in self._plugins:
            try:
                text = (self.r.root / folder / "pubspec.yaml").read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            classes: Dict[str, Set[str]] = {}
            files: Dict[str, Set[str]] = {}
            for platform, keys in _pubspec_platforms(text).items():
                if platform not in PLATFORM_DIRS:
                    continue
                cls = keys.get("dartPluginClass") or (keys.get("pluginClass") if platform == "web" else None)
                name = keys.get("dartFileName") or (keys.get("fileName") if platform == "web" else None)
                if cls and cls != "none":
                    classes.setdefault(cls, set()).add(platform)
                if name:
                    files.setdefault(name[2:] if name.startswith("./") else name, set()).add(platform)
            self._plugins[folder] = (classes, files)
        return self._plugins[folder]

    def platform_checks(self, site: _Site) -> Optional[Set[str]]:
        """The platforms a Dart call is limited to by `Platform.isAndroid` / `TargetPlatform.iOS` checks in its
        function: those named, or none at all when a check is negated; None when the function checks nothing."""
        decl = site.src.decl_at(site.line)
        if site.src.lang != "dart" or decl is None:
            return None
        body = site.src.text[site.src.starts[decl.line_start - 1]:site.src.starts[min(decl.line_end,
                                                                                      len(site.src.starts) - 1)]]
        named = [a or b for a, b in _PLATFORM_CHECK.findall(body)]
        if not named:
            return None
        if re.search(r"!\s*Platform\.is|!=\s*TargetPlatform\.", body):
            return set()
        return {_PLATFORM_KEYS.get(n.lower(), n.lower()) for n in named}

    def stream_target(self, ch: _Channel) -> _Site:
        """The `onListen` of the handler given to `setStreamHandler`, else one in the file, else the declaration."""
        src = ch.site.src
        m = _STREAM_HANDLER.search(src.text, ch.start)
        if m:
            for decl in self.r.decl_by_id.values():
                if decl.name == "onListen" and decl.parent is not None and decl.parent.name == m.group(1):
                    return self.decl_site(decl)
        listeners = [d for d in src.f.decls if d.name == "onListen"]
        return self.decl_site(listeners[0]) if len(listeners) == 1 else ch.site

    def message_target(self, ch: _Channel) -> _Site:
        m = _MESSAGE_HANDLER.search(ch.site.src.text, ch.start)
        if m is None:
            return ch.site
        site = self.site(ch.site.src, m.start())
        # Pigeon: `setMessageHandler { … api.connect(server: serverArg) … }` — the app's implementation of the
        # generated protocol / interface is what answers
        body_end = _block(ch.site.src.text, ch.site.src.text.find("{", m.end())) if "{" in ch.site.src.text[
            m.end():m.end() + 80] else m.end()
        api = re.search(r"\bapi\s*(?:\.|->)\s*([A-Za-z_]\w*)\s*\(", ch.site.src.text[m.end():body_end])
        if api is not None:
            impl = self.implementation(ch.site.src, api.group(1))
            if impl is not None:
                return impl
        return site

    def implementation(self, src: _Src, method: str) -> Optional[_Site]:
        """The one project method `method` of a class that adopts an interface declared in src (Pigeon's API)."""
        apis = {d.name for d in src.f.decls if d.kind == "class" and d.interface}
        found = []
        for decl in self.r.decl_by_id.values():
            rel = self.r.file_of.get(decl.node_id, "")
            if decl.name != method or decl.parent is None or rel == src.rel \
                    or _family(self.r.files[rel].language) != _family(src.lang):  # Java may implement a Kotlin API
                continue
            if any(base.split(".")[-1].split("<")[0].strip() in apis for base, _ in decl.parent.bases):
                found.append(decl)
        return self.decl_site(found[0]) if len(found) == 1 else None

    # ---------------------------------------------------------------- the C ABI and JNI

    def exports(self) -> Dict[str, List[_Export]]:
        out: Dict[str, List[_Export]] = {}

        def add(symbol: str, decl: Optional[Decl], src: _Src) -> None:
            if decl is not None and decl.node_id:
                out.setdefault(symbol, []).append(_Export(symbol, decl.node_id, _Site(src, decl.line_start,
                                                                                      decl.node_id)))

        for src in self.srcs.values():
            if src.lang == "rust":
                if "no_mangle" not in src.raw and "export_name" not in src.raw:
                    continue
                for m in _RUST_EXPORT.finditer(src.text):
                    fn = _FN_AFTER.search(src.text, m.end(), m.end() + 600)
                    if fn is None or re.search(r"[;{]", src.text[m.end():fn.start()]):
                        continue
                    add(m.group(1) or fn.group(1), self.named_decl(src, fn.group(1), src.line(fn.start())), src)
            elif src.lang == "swift":
                if "_cdecl" not in src.raw:
                    continue
                for m in _CDECL.finditer(src.text):
                    fn = _FUNC_AFTER.search(src.text, m.end(), m.end() + 400)
                    if fn is not None:
                        add(m.group(1), self.named_decl(src, fn.group(1), src.line(fn.start())), src)
            elif src.lang == "go":
                for m in _GO_EXPORT.finditer(src.raw):
                    add(m.group(1), self.named_decl(src, m.group(1), src.line(m.start()) + 1), src)
            elif src.lang in ("c", "cpp"):
                is_c = src.lang == "c" or src.rel.endswith(".c")
                marked = is_c or _C_EXPORT_MARKS.search(src.raw) is not None
                blocks = []
                for m in _EXTERN_C_BLOCK.finditer(src.text) if marked else ():
                    blocks.append((src.line(m.start()), src.line(_block(src.text, m.end() - 1))))
                for decl in src.f.decls:
                    if decl.kind != "function" or decl.parent is not None or decl.declaration_only or decl.receiver:
                        continue
                    if not marked:
                        if decl.name.startswith("Java_"):
                            add(decl.name, decl, src)
                        continue
                    head = "\n".join(src.line_text(n) for n in range(max(1, decl.line_start - 1), decl.line_start + 1))
                    if is_c:
                        if not re.search(r"\bstatic\b", src.line_text(decl.line_start)):
                            add(decl.name, decl, src)
                    elif (decl.name.startswith("Java_") or _C_EXPORT_MARKS.search(head)
                          or any(a <= decl.line_start <= b for a, b in blocks)):
                        add(decl.name, decl, src)
        return out

    def named_decl(self, src: _Src, name: str, line: int) -> Optional[Decl]:
        found = [d for d in src.f.decls if d.name == name and d.line_start >= line - 1]
        return min(found, key=lambda d: d.line_start) if found else None

    def c_abi(self, exports: Dict[str, List[_Export]]) -> None:
        if not exports:
            return
        swift_names = {d.name for f in self.r.files.values() if f.language == "swift" for d in f.decls}
        for facts in self.r.files.values():
            lang = facts.language
            if lang not in ("swift", "c", "cpp", "go"):
                continue
            src = self.srcs.get(facts.rel_path) or _Src(facts, "")
            for call in facts.calls:
                if lang == "go":
                    if len(call.chain) != 2 or call.chain[0] != "C":
                        continue
                    symbol = call.chain[1]
                elif len(call.chain) == 1:
                    symbol = call.chain[0]
                else:
                    continue
                found = [x for x in exports.get(symbol, []) if _family(x.site.src.lang) != _family(lang)]
                if not found or (lang == "swift" and symbol in swift_names):
                    continue
                owner = call.caller.node_id if call.caller is not None and call.caller.node_id else src.mod_id
                site = _Site(src, call.line, owner)
                self.note_abi(symbol, "passes", lang, site, self.ffi_link(site, symbol, found), None)
        for src in self.srcs.values():
            if src.lang != "dart" or ("lookup" not in src.raw and "@Native" not in src.raw):
                continue
            for m in _LOOKUP.finditer(src.text):
                paren = _open_paren(src.text, m.end())
                args, _ = _args(src.text, paren) if paren >= 0 else ([], -1)
                symbol = _literal(args[0]) if args else None
                if not symbol or symbol not in exports:
                    continue
                site = self.site(src, m.start())
                decl = src.decl_at(site.line)
                if decl is not None and decl.kind == "class":  # ffigen: `late final _xPtr = _lookup(...)` fields
                    same = [d for d in src.f.decls if d.parent is decl and d.name == symbol]
                    if same:
                        site = _Site(src, site.line, same[0].node_id)
                var = re.search(r"(\w+)\s*=\s*[\w.]*$", src.prefix(m.start()))
                if var:
                    self.drop.add((site.owner, var.group(1)))
                edge = self.ffi_link(site, symbol, exports[symbol])
                count = self.dart_type_params(src, m.end()) if edge is not None else None
                self.note_abi(symbol, "declares", "dart", site, edge, count)
            for m in _NATIVE.finditer(src.text):
                symbol = None
                paren = _open_paren(src.text, m.end())
                if paren >= 0:
                    for arg in _args(src.text, paren)[0]:
                        if re.match(r"\s*symbol\s*:", arg):
                            symbol = _literal(arg.split(":", 1)[1])
                decl = src.decl_after(src.line(m.start()))
                if decl is None:
                    continue
                symbol = symbol or decl.name
                if symbol in exports:
                    site = _Site(src, src.line(m.start()), decl.node_id)
                    edge = self.ffi_link(site, symbol, exports[symbol])
                    if edge is None:
                        continue
                    if src.text[m.end():m.end() + 1] == "<":
                        count = self.dart_type_params(src, m.end())
                    else:  # `@Native()`: the external function's own parameters, one for one
                        parts = self.declared_params(decl, src.rel, "dart")
                        count = None if parts is None or any(p[:1] in "{[" for p in parts) else len(parts)
                    self.note_abi(symbol, "declares", "dart", site, edge, count)

    def note_abi(self, symbol: str, role: str, lang: str, site: _Site, edge: Optional[ActualEdge],
                 count: Optional[int]) -> None:
        """A use of a C ABI function, compared with its definition once every link is made (`abi_contracts`)."""
        if edge is not None:
            self.abi_sites.setdefault(symbol, []).append((role, lang, site, edge, count))

    def dart_type_params(self, src: _Src, pos: int) -> Optional[int]:
        """Parameters of the native function type in `lookupFunction<N, D>`, `lookup<NativeFunction<N>>` or
        `@Native<N>` whose `<` is at pos (typedefs followed)."""
        got = _group(src.text, pos) if src.text[pos:pos + 1] == "<" else None
        return self.dart_native_count(src, got[0][0]) if got and got[0] else None

    def dart_native_count(self, src: _Src, t: str, depth: int = 0) -> Optional[int]:
        t = t.strip()
        wrapped = re.fullmatch(r"(?:\w+\.)?NativeFunction\s*<(.*)>", t, re.DOTALL)
        if wrapped:
            t = wrapped.group(1).strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", t):
            rhs = self.dart_typedef(src, t) if depth < 3 else None
            return self.dart_native_count(src, rhs, depth + 1) if rhs else None
        fn = re.search(r"\bFunction\s*\(", t)
        if fn is None or "VarArgs" in t:
            return None
        got = _group(t, fn.end() - 1)
        if got is None or t[got[1]:].strip() or any(p[:1] in "{[" for p in got[0]):
            return None  # not one plain function type (`Function` inside the result type, named parameters)
        return len(got[0])

    def dart_typedef(self, src: _Src, name: str) -> Optional[str]:
        pattern = re.compile(r"\btypedef\s+" + re.escape(name) + r"\s*=\s*([^;]+);")
        found = pattern.findall(src.text)
        if found or name.startswith("_"):  # `_X` is private to its library
            return found[0] if len(found) == 1 else None
        for other in self.srcs.values():
            if other.lang == "dart" and other is not src and name in other.raw:
                found.extend(pattern.findall(other.text))
        return found[0] if len(found) == 1 else None

    def ffi_link(self, site: _Site, symbol: str, found: List[_Export]) -> Optional[ActualEdge]:
        """Link a use of a C symbol to its definition; the edge when the definition is the only one."""
        ids = sorted({x.node for x in found})
        edge = None
        if len(ids) == 1:
            edge = self.link(site, ids[0], "resolved", "ffi", symbol, found[0].site)
            for e in self.r.edges if edge is not None else ():
                # a C/C++ call bound to the header declaration now goes to the definition in the other language
                if e.kind != "calls" or e.from_id != site.owner or e.location is None or e.location.line != site.line:
                    continue
                decl = self.r.decl_by_id.get(e.to_id)
                if decl is not None and decl.declaration_only and decl.name == symbol:
                    self.edges.remove(edge)
                    self.retarget[(e.from_id, site.line, e.to_id)] = edge
                    break
        else:
            self.link(site, f"ambiguous:{symbol}", "inferred", "ffi", symbol, found[0].site, candidates=ids)
        self.drop.add((site.owner, symbol))
        return edge

    def jni(self, exports: Dict[str, List[_Export]]) -> None:
        jni_exports = {s: xs for s, xs in exports.items() if s.startswith("Java_")}
        if not jni_exports:
            return
        for src in self.srcs.values():
            if src.lang not in ("kotlin", "java"):
                continue
            jvm_name = re.search(r'@file\s*:\s*JvmName\s*\(\s*"(\w+)"\s*\)', src.text)
            facade = jvm_name.group(1) if jvm_name else None
            natives = []
            for decl in src.f.decls:
                if decl.kind not in ("function", "method"):
                    continue
                head = src.line_text(decl.line_start)
                native = re.search(r"\bexternal\b[^\n]*\bfun\b", head) if src.lang == "kotlin" \
                    else re.search(r"\bnative\b[^(]*\(", head)
                if native:
                    natives.append(decl)
            names = [(d.parent.node_id if d.parent is not None else "", d.name) for d in natives]
            for decl in natives:
                classes = [p.name for p in _parents(decl)]
                if not classes and src.lang == "kotlin":
                    stem = posixpath.basename(src.rel).rsplit(".", 1)[0]
                    classes = [facade or stem[:1].upper() + stem[1:] + "Kt"]
                variants = [classes] + ([[c for c in classes if c != "Companion"]] if "Companion" in classes else [])
                package = (decl.namespace or src.f.namespace or "").replace(".", "/")
                for parts in variants:
                    binary = (package + "/" if package else "") + "$".join(parts)
                    symbol = f"Java_{_jni_escape(binary)}_{_jni_escape(decl.name)}"
                    if symbol in jni_exports:
                        found = jni_exports[symbol]
                        ids = sorted({x.node for x in found})
                        site = _Site(src, decl.line_start, decl.node_id)
                        if len(ids) == 1:
                            label = f"{'.'.join(parts)}.{decl.name}"
                            edge = self.link(site, ids[0], "resolved", "jni", symbol, found[0].site, label=label)
                            overloaded = names.count((decl.parent.node_id if decl.parent is not None else "",
                                                      decl.name)) > 1
                            if edge is not None and not overloaded:
                                self.jni_contract(src, decl, found[0], edge, label)
                        else:
                            self.link(site, f"ambiguous:{symbol}", "inferred", "jni", symbol, found[0].site,
                                      candidates=ids)
                        break

    def jni_contract(self, src: _Src, decl: Decl, export: _Export, edge: ActualEdge, label: str) -> None:
        """A Kotlin `external fun` / Java `native` method against its JNI function: the parameters after env and
        class (or this) must be as many, and none of a type that cannot be the declared one."""
        head = src.line_text(decl.line_start)
        above = "\n".join(src.line_text(n) for n in range(max(1, decl.line_start - 2), decl.line_start))
        if src.lang == "kotlin":
            m = re.search(r"\bfun\b(.*?)\b" + re.escape(decl.name) + r"\s*\(", head)
            if m is None or "." in m.group(1):
                return  # an extension function: its receiver is one more parameter
            if "JvmOverloads" in above + head:
                return  # one JVM method per default argument
            if re.search(r"\bsuspend\b", head) or re.search(r"(?:^|\s)context\s*\(", above + "\n" + head):
                return  # a hidden Continuation or context receivers are more parameters
            static = decl.parent is None or "JvmStatic" in head
        else:
            static = re.search(r"\bstatic\b", head) is not None
        target = self.r.decl_by_id.get(export.node)
        mine = self.declared_params(decl, src.rel, src.lang)
        theirs = self.declared_params(target, export.site.src.rel, export.site.src.lang) if target else None
        if mine is None or theirs is None or edge.bridge is None:
            return
        detail: Dict[str, object] = {"label": label, "lang": src.lang, "def_lang": export.site.src.lang,
                                     "receiver": "class" if static else "this"}
        if len(theirs) - 2 != len(mine):
            detail.update(declares=len(mine), takes=len(theirs) - 2)
            edge.bridge["mismatch"] = {"kind": "jni-arity", "detail": detail}
            return
        differ = []
        for index, (ours, other) in enumerate(zip(mine, theirs[2:]), 1):
            _name, ours_type = _jvm_param(ours, src.lang)
            _other_name, other_type = _native_param(other, export.site.src.lang)
            if _contradicts(_jvm_category(ours_type, src.lang), _jni_category(other_type, export.site.src.lang)):
                differ.append({"index": index, "declares": re.sub(r"\s+", " ", ours),
                               "takes": re.sub(r"\s+", " ", other)})
        if differ:
            detail["params"] = differ
            edge.bridge["mismatch"] = {"kind": "jni-type", "detail": detail}

    def declared_params(self, decl: Decl, rel: str, lang: str) -> Optional[List[str]]:
        """A definition's or declaration's parameters, from its signature or, when that was cut short or holds a
        comment (squashed onto one line, `// a, b` would hide where it ends), from its text without comments."""
        sig = decl.signature or ""
        if "//" not in sig and "/*" not in sig:
            parts = _params(sig, lang, rel)
            if parts is not None or not sig.endswith("…"):
                return parts
        src = self.source(rel)
        if src is None or not src.text:
            return None
        start = src.starts[decl.line_start - 1] if 0 < decl.line_start <= len(src.starts) else 0
        m = re.compile(r"\b" + re.escape(decl.name) + r"\b").search(src.text, start, start + 4000)
        paren = _open_paren(src.text, m.end()) if m else -1
        got = _group(src.text, paren, '"' if lang in ("rust", "swift") else "\"'") if paren >= 0 else None
        return _params("(" + ", ".join(got[0]) + ")", lang, rel) if got else None

    def source(self, rel: str) -> Optional[_Src]:
        """A file's text: from this pass's sources, else read once (a Swift file calling C needs no hint)."""
        src = self.srcs.get(rel)
        if src is not None:
            return src
        if rel not in self.disk:
            facts = self.r.files.get(rel)
            try:
                raw = (self.r.root / rel).read_bytes().decode("utf-8", "replace") if facts is not None else None
            except OSError:
                raw = None
            self.disk[rel] = _Src(facts, raw) if facts is not None and raw is not None else None
        return self.disk[rel]

    def abi_contracts(self, exports: Dict[str, List[_Export]]) -> None:
        """A C ABI function with one definition and links from other languages: its Dart bindings, its calls and
        the C/C++ header declarations bound to it must have as many parameters. The difference is kept on the links
        (a declaration is no call, so it gets no edge of its own: it is named in the detail of the links). Skipped
        when unsure: several definitions (also one the parser missed, or two copies of the API's header), header
        declarations that disagree among themselves, a macro of that name or among a call's arguments, default
        arguments, a C++ function of that name in a namespace or class, a Swift function of that name in the file.
        The costly checks run only for the functions where a difference was found."""
        names = {s for s, xs in exports.items() if len(xs) == 1 and not s.startswith("Java_") and s in self.abi_sites}
        if not names:
            return
        headers: Dict[str, List[Decl]] = {}
        defined: Dict[str, int] = {}
        shadowed: Set[str] = set()  # also a C++ method or namespaced function: a C++ call may mean that one
        for facts in self.r.files.values():
            if _family(facts.language) != "c":
                continue
            for d in facts.decls:
                if d.name not in names:
                    continue
                if d.kind == "function" and d.parent is None and not d.receiver and not d.namespace:
                    if d.declaration_only:
                        headers.setdefault(d.name, []).append(d)
                    else:
                        defined[d.name] = defined.get(d.name, 0) + 1
                elif d.kind in ("function", "method"):
                    shadowed.add(d.name)
        found: List[tuple] = []  # (symbol, export, takes, header declarations that differ, uses that differ)
        for symbol in sorted(names):
            x = exports[symbol][0]
            target = self.r.decl_by_id.get(x.node)
            in_c = _family(x.site.src.lang) == "c"
            if target is None or defined.get(symbol, 0) > (1 if in_c else 0):
                continue
            decls = headers.get(symbol, [])
            if in_c and len({posixpath.dirname(self.r.file_of.get(d.node_id, "")) for d in decls}) > 1:
                continue  # two copies of the API's header: maybe two libraries
            takes = self.declared_params(target, x.site.src.rel, x.site.src.lang)
            if takes is None:
                continue
            declared = [(d, self.declared_params(d, self.r.file_of.get(d.node_id, ""), "cpp")) for d in decls]
            if len({len(p) for _d, p in declared if p is not None}) > 1:
                continue  # the declarations disagree among themselves
            defaults = any("=" in part for _d, p in declared for part in p or [])
            uses = []
            for role, lang, site, edge, count in self.abi_sites[symbol]:
                args: List[str] = []
                if role == "passes":
                    count, args = self.passed_args(site, symbol, lang, defaults or (lang == "cpp" and symbol in shadowed))
                if count is not None and count != len(takes):
                    uses.append((role, lang, site, edge, count, args))
            differ = [(d, len(p)) for d, p in declared if p is not None and len(p) != len(takes)]
            if uses or differ:
                found.append((symbol, x, len(takes), differ, uses))
        if not found:
            return
        index = self.c_index({symbol for symbol, *_rest in found})
        for symbol, x, takes, differ, uses in found:
            if symbol in index["defines"] or symbol in index["elsewhere"]:
                continue  # a macro of that name, or a definition the parser did not take
            if _family(x.site.src.lang) == "c" and symbol in index["unsure"]:
                continue
            uses = [u for u in uses if not (_family(u[1]) == "c" and any(w in index["defines"] for a in u[5]
                                                                          for w in re.findall(r"[A-Za-z_]\w*", a)))]
            differ = [(d, n) for d, n in differ if self.bound_header(self.r.file_of.get(d.node_id, ""), symbol)]
            detail: Dict[str, object] = {"def_lang": x.site.src.lang, "takes": takes}
            if differ:
                at = {id(d): self.r.file_of.get(d.node_id, "") for d, _n in differ}
                detail["declarations"] = [
                    {"at": f"{at[id(d)]}:{d.line_start}", "count": n,
                     "lang": self.r.files[at[id(d)]].language if at[id(d)] in self.r.files else "cpp"}
                    for d, n in sorted(differ, key=lambda dn: (at[id(dn[0])], dn[0].line_start))]
            own = {id(u[3]): u for u in uses}
            for _role, _lang, _site, edge, _count in self.abi_sites[symbol]:
                if edge.bridge is None or (id(edge) not in own and not differ):
                    continue
                mine = dict(detail)
                if id(edge) in own:
                    role, lang, _s, _e, count, _args = own[id(edge)]
                    mine.update(role=role, lang=lang, count=count)
                edge.bridge["mismatch"] = {"kind": "c-arity", "detail": mine}

    def passed_args(self, site: _Site, symbol: str, lang: str, unsure: bool) -> Tuple[Optional[int], List[str]]:
        """(arguments the call passes, their text) or (None, []) when that is not certain (see `passed`); a Swift
        file that declares a function or closure of that name (a nested `func`, a `let`) may call that one."""
        src = self.source(site.src.rel)
        if unsure or src is None:
            return None, []
        if lang == "swift" and re.search(r"\b(?:func|let|var)\s+" + re.escape(symbol) + r"\b", src.text):
            return None, []
        got = self.passed(src, site.line, symbol, lang)
        return (None, []) if got is None else (len(got), got)

    def passed(self, src: _Src, line: int, symbol: str, lang: str) -> Optional[List[str]]:
        """The arguments the call of `symbol` on `line` passes; None unless the call text leaves no doubt (one call
        of it on the line, brackets that balance, no `<`, no macro-like argument, no preprocessor line inside, no
        Swift trailing closure). Arguments #defined elsewhere in the project are checked by the caller."""
        if not 0 < line <= len(src.starts):
            return None
        start = src.starts[line - 1]
        end = src.starts[line] if line < len(src.starts) else len(src.text)
        text = src.text[start:end]
        c_family = _family(lang) == "c"
        if c_family and text.lstrip().startswith("#"):
            return None
        prefix = r"\bC\s*\.\s*" if lang == "go" else r"(?<![\w$.>:])"
        hits = list(re.finditer(prefix + re.escape(symbol) + r"\s*\(", text))
        if len(hits) != 1 or len(re.findall(r"\b" + re.escape(symbol) + r"\b", text)) != 1:
            return None
        paren = start + hits[0].end() - 1
        got = _group(src.text, paren, '"' if lang == "swift" else "\"'`" if lang == "go" else "\"'", angles=False)
        if got is None:
            return None
        parts, after = got
        if c_family and re.search(r"(?m)^[ \t]*#", src.text[paren:after]):
            return None  # `#if` / `#ifdef` inside the argument list
        if any("<" in p or "..." in p for p in parts):
            return None
        if lang == "swift" and (src.text[after:after + 200].lstrip().startswith("{")
                                or any(re.match(r"\s*\w+\s*:(?!:)", p) for p in parts)):
            return None  # a trailing closure is one more argument; labels belong to a Swift function
        if (c_family or lang == "go") and any((_MACRO_LIKE.fullmatch(p) and p != "NULL")
                                               or re.match(r"[A-Z_][A-Z0-9_]*\s*\(", p) for p in parts):
            return None  # a macro may stand for several arguments
        return parts

    def c_index(self, symbols: Set[str]) -> Dict[str, Set[str]]:
        """What the C and C++ files of the project (all of them, read for this when the pass holds no text) say about
        these functions: {"defines": every name a `#define` gives, "elsewhere": symbols defined (a body follows) where
        the parser found no declaration of that name, "unsure": symbols declared or defined in a file the parser
        could not read fully, where it found no declaration (it may have missed a definition there)}."""
        defines: Set[str] = set()
        elsewhere: Set[str] = set()
        unsure: Set[str] = set()
        call = re.compile(r"(?<![\w$.>:~])(" + "|".join(re.escape(s) for s in sorted(symbols, key=len, reverse=True))
                          + r")\s*\(")
        for rel, facts in self.r.files.items():
            if _family(facts.language) != "c":
                continue
            src = self.srcs.get(rel)
            if src is not None:
                raw = src.raw
            else:
                try:
                    raw = (self.r.root / rel).read_bytes().decode("utf-8", "replace")
                except OSError:
                    continue
            if "define" in raw:
                defines.update(_DEFINE.findall(raw))
            if not any(s in raw for s in symbols):
                continue
            text = src.text if src is not None else _blank_comments(raw)
            starts: Optional[List[int]] = None
            for m in call.finditer(text):
                symbol = m.group(1)
                head = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
                if head and (not _TYPE_HEAD.fullmatch(head) or set(re.findall(r"\w+", head)) & _NOT_TYPES):
                    continue  # a call, not a declaration or definition
                if starts is None:
                    starts = [0] + [n.end() for n in re.finditer("\n", text)]
                line = bisect_right(starts, m.start())
                if any(d.name == symbol and d.line_start <= line <= d.line_start + 3 for d in facts.decls):
                    continue  # one the parser took (the definition, a declaration, a method or namespaced one)
                got = _group(text, m.end() - 1, angles=False)
                if got is not None and re.match(r"\s*(?:const\s*)?\{", text[got[1]:got[1] + 200]):
                    elsewhere.add(symbol)
                if facts.syntax_errors:
                    unsure.add(symbol)
        return {"defines": defines, "elsewhere": elsewhere, "unsure": unsure}

    def bound_header(self, rel: str, symbol: str) -> bool:
        """The header is known to declare the functions of the other language: the resolver bound a C/C++ call to
        one of its declarations that is defined there, a C/C++ caller of `symbol` includes it, or a Swift bridging
        header or module map names it."""
        if not hasattr(self, "_bound"):
            self._bound: Set[str] = {self.r.file_of.get(key[2], "") for key in self.retarget}
        if rel in self._bound:
            return True
        base = posixpath.basename(rel)
        include = re.compile(r'(?m)^[ \t]*#[ \t]*(?:include|import)[ \t]*["<](?:[^">\n]*/)?' + re.escape(base) + '[">]')
        callers = {u[2].src.rel for u in self.abi_sites.get(symbol, []) if _family(u[1]) == "c"}
        for other in sorted(callers):
            src = self.source(other)
            if src is not None and base in src.raw and include.search(src.raw):
                return True
        for _path, text in self.umbrella_files():
            if base in text and (include.search(text) or re.search(r'\bheader\s+"(?:[^"\n]*/)?' + re.escape(base)
                                                                    + '"', text)):
                return True
        return False

    def umbrella_files(self) -> List[Tuple[str, str]]:
        """Swift bridging headers and module maps of the project: (path, text)."""
        if not hasattr(self, "_umbrellas"):
            found: List[Tuple[str, str]] = []
            seen = 0
            for dirpath, dirnames, filenames in os.walk(self.r.root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                seen += len(filenames)
                if seen > 200_000:
                    break
                for fname in filenames:
                    if fname.endswith(("Bridging-Header.h", ".modulemap")) and not fname.startswith("._"):
                        try:
                            found.append((fname, (Path(dirpath) / fname).read_text(encoding="utf-8",
                                                                                   errors="replace")))
                        except OSError:
                            continue
            self._umbrellas = found
        return self._umbrellas

    # ---------------------------------------------------------------- generated bindings

    def rust_crates(self) -> Dict[str, Tuple[str, str]]:
        """crate dir -> (library name, Cargo.toml text)."""
        if not hasattr(self, "_crates"):
            self._crates: Dict[str, Tuple[str, str]] = {}
            for crate_dir, package in self.r.rust.crates:
                path = self.r.root / crate_dir / "Cargo.toml"
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    raw = ""
                lib = re.search(r"\[lib\][^\[]*?^\s*name\s*=\s*\"([^\"]+)\"", raw, re.MULTILINE | re.DOTALL)
                self._crates[crate_dir] = ((lib.group(1) if lib else package).replace("-", "_"), raw)
        return self._crates

    def crate_of(self, rel: str) -> Optional[str]:
        for crate_dir in sorted(self.rust_crates(), key=len, reverse=True):
            if not crate_dir or rel.startswith(crate_dir + "/"):
                return crate_dir
        return None

    def rust_decls(self, crate_dir: str) -> List[Tuple[Decl, str]]:
        """(declaration, module path below the crate root as `a::b`) of the crate's functions and methods."""
        out = []
        for rel, facts in self.r.files.items():
            if facts.language != "rust" or self.crate_of(rel) != crate_dir:
                continue
            key = self.r.rust.module_key(rel)
            module = key.split("::", 1)[1] if "::" in key else ""
            out.extend((d, module) for d in facts.decls if d.kind in ("function", "method"))
        return out

    def frb(self) -> None:
        dart = [s for s in self.srcs.values() if s.lang == "dart" and _FRB.search(s.raw)]
        if not dart:
            return
        keys: Dict[str, List[Decl]] = {}
        for crate_dir, (_lib, toml) in self.rust_crates().items():
            if "flutter_rust_bridge" not in toml:
                continue
            for decl, module in self.rust_decls(crate_dir):
                owner = self.r.receiver_of.get(decl.parent.node_id) if decl.parent is not None else None
                type_name = (self.r.decl_by_id[owner].name if owner in self.r.decl_by_id else decl.parent.name) \
                    if decl.parent is not None else None
                parts = ["crate"] + (module.split("::") if module else []) + ([type_name] if type_name else []) \
                    + [decl.name]
                keys.setdefault(_lower_camel(parts), []).append(decl)
        for src in dart:
            for m in _FRB.finditer(src.text):
                found = keys.get(m.group(1), [])
                if len(found) == 1:
                    self.link(self.site(src, m.start(1)), found[0].node_id, "resolved", "frb", m.group(1),
                              self.decl_site(found[0]), label=found[0].name)

    def uniffi(self) -> None:
        users = [s for s in self.srcs.values() if s.lang != "rust" and "uniffi_" in s.raw]
        if not users:
            return
        libs = {lib: crate_dir for crate_dir, (lib, _toml) in self.rust_crates().items()}
        for src in users:
            for m in _UNIFFI.finditer(src.text):
                crate_dir = libs.get(m.group(1))
                if crate_dir is None:
                    continue
                kind, rest = m.group(2), m.group(3)
                decls = self.rust_decls(crate_dir)
                if kind == "func":
                    found = [d for d, _ in decls if d.name == rest and d.parent is None]
                else:
                    obj, _, name = rest.partition("_")
                    found = [d for d, _ in decls if d.name == name and d.parent is not None
                             and self.type_name(d).lower() == obj]
                if len(found) == 1:
                    self.link(self.site(src, m.start()), found[0].node_id, "resolved", "uniffi", m.group(0),
                              self.decl_site(found[0]), label=found[0].name)

    def type_name(self, decl: Decl) -> str:
        parent = decl.parent
        owner = self.r.receiver_of.get(parent.node_id) if parent is not None else None
        return self.r.decl_by_id[owner].name if owner in self.r.decl_by_id else (parent.name if parent else "")

    def tauri(self) -> None:
        commands: Dict[str, List[Decl]] = {}
        for src in self.srcs.values():
            if src.lang == "rust" and "tauri::command" in src.raw:
                for m in _TAURI_COMMAND.finditer(src.text):
                    fn = _FN_AFTER.search(src.text, m.end(), m.end() + 400)
                    decl = self.named_decl(src, fn.group(1), src.line(fn.start())) if fn else None
                    if decl is not None:
                        commands.setdefault(decl.name, []).append(decl)
        if not commands:
            return
        for src in self.srcs.values():
            if src.lang not in ("typescript", "javascript") or ("@tauri-apps/api" not in src.raw
                                                                 and "__TAURI__" not in src.raw):
                continue
            for m in _TAURI_INVOKE.finditer(src.text):
                found = commands.get(m.group(2), [])
                if len(found) == 1:
                    self.link(self.site(src, m.start()), found[0].node_id, "resolved", "tauri", m.group(2),
                              self.decl_site(found[0]))

    # ---------------------------------------------------------------- system hand-offs

    def network_extension(self) -> None:
        providers: Dict[str, List[Decl]] = {}
        for facts in self.r.files.values():
            if facts.language != "swift":
                continue
            for decl in facts.decls:
                if decl.kind == "method" and decl.parent is not None and self.is_provider(decl.parent):
                    providers.setdefault(decl.name, []).append(decl)
        if not providers:
            return
        for src in self.srcs.values():
            if src.lang != "swift" or not any(w in src.raw for w in ("startVPNTunnel", "stopVPNTunnel", "startTunnel",
                                                                     "stopTunnel", "sendProviderMessage")):
                continue
            for pattern, target in _NE_CALLS:
                for m in pattern.finditer(src.text):
                    found = providers.get(target, [])
                    site = self.site(src, m.start())
                    if not found or any(site.owner == d.node_id for d in found):
                        continue
                    found = self.nearest(src.rel, found)
                    ids = sorted(d.node_id for d in found)
                    self.link(site, ids[0] if len(ids) == 1 else f"ambiguous:{target}", "inferred",
                              "network-extension", m.group(0).lstrip(". \t").split("(")[0], self.decl_site(found[0]),
                              candidates=ids)
                    self.drop_unresolved_at(site, m.group(0).lstrip(". \t").split("(")[0])

    def nearest(self, rel: str, decls: List[Decl]) -> List[Decl]:
        """The providers sharing the most folders with the caller: an app starts its own extension, so the macOS
        app (`macos/Runner`, `MacOS/App`) reaches the macOS provider, not the iOS one."""
        here = [p.lower() for p in rel.split("/")[:-1]]

        def shared(decl: Decl) -> int:
            n = 0
            for a, b in zip(here, self.r.file_of.get(decl.node_id, "").lower().split("/")[:-1]):
                if a != b:
                    break
                n += 1
            return n

        best = max(shared(d) for d in decls)
        return [d for d in decls if shared(d) == best]

    def is_provider(self, cls: Decl) -> bool:
        seen = set()
        stack = [cls]
        while stack:
            d = stack.pop()
            if d.node_id in seen:
                continue
            seen.add(d.node_id)
            if any(base.split(".")[-1].strip() in NE_PROVIDERS for base, _ in d.bases):
                return True
            stack.extend(self.r.decl_by_id[b] for b in self.r.bases.get(d.node_id, []) if b in self.r.decl_by_id)
        return False

    def drop_unresolved_at(self, site: _Site, name: str) -> None:
        for e in self.r.edges:
            if e.from_id == site.owner and e.confidence == "unresolved" and e.location is not None \
                    and e.location.line == site.line and (e.target_symbol or "").endswith(name):
                self.drop.add((e.from_id, e.target_symbol or ""))

    def android_intents(self) -> None:
        classes: Dict[str, List[Decl]] = {}
        for facts in self.r.files.values():
            if facts.language in ("kotlin", "java"):
                for decl in facts.decls:
                    if decl.kind == "class":
                        classes.setdefault(decl.name, []).append(decl)
        for src in self.srcs.values():
            if src.lang not in ("kotlin", "java") or "Intent" not in src.raw:
                continue
            for m in _INTENT.finditer(src.text):
                target_cls = classes.get(m.group(1) or m.group(2), [])
                if len(target_cls) != 1:
                    continue
                verb = self.intent_verb(src, m)
                if verb is None:
                    continue
                cls = target_cls[0]
                methods = self.r.methods.get(cls.node_id, {}).get(_INTENT_VERBS[verb], [])
                target = methods[0] if len(methods) == 1 else cls.node_id
                site = self.site(src, m.start())
                to = self.decl_site(self.r.decl_by_id[target])
                self.link(site, target, "inferred", "android-intent", f"{verb} {cls.name}", to, candidates=[target])
                self.drop_unresolved_at(site, verb)

    def intent_verb(self, src: _Src, m: re.Match) -> Optional[str]:
        before = re.search(r"(\w+)\s*\(\s*$", src.prefix(m.start()))
        if before and before.group(1) in _INTENT_VERBS:
            return before.group(1)
        bind = re.search(r"(\w+)\s*=\s*$", src.prefix(m.start()))
        decl = src.decl_at(src.line(m.start()))
        if bind is None or decl is None:
            return None
        body = src.text[src.starts[decl.line_start - 1]:src.starts[min(decl.line_end, len(src.starts) - 1)]]
        for verb in _INTENT_VERBS:
            if re.search(rf"\b{verb}\s*\(\s*{re.escape(bind.group(1))}\b", body):
                return verb
        return None


def _pubspec_platforms(text: str) -> Dict[str, Dict[str, str]]:
    """`flutter: plugin: platforms:` of a pubspec.yaml: platform -> its plain keys (`dartPluginClass: X`, …)."""
    out: Dict[str, Dict[str, str]] = {}
    if "platforms" not in text:
        return out
    path: List[Tuple[int, str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith(("#", "- ")) or not line.strip():
            continue
        line = re.sub(r"\s+#.*$", "", line).rstrip()
        m = re.match(r"(\s*)([\w.-]+)\s*:(?:\s+(.*))?$", line)
        if m is None:
            continue
        indent = len(m.group(1).expandtabs())
        while path and path[-1][0] >= indent:
            path.pop()
        keys = [k for _i, k in path]
        value = (m.group(3) or "").strip().strip("'\"")
        if keys[:3] == ["flutter", "plugin", "platforms"] and len(keys) == 4 and value:
            out.setdefault(keys[3], {})[m.group(2)] = value
        path.append((indent, m.group(2)))
    return out


def _parents(decl: Decl) -> List[Decl]:
    out, d = [], decl.parent
    while d is not None:
        out.append(d)
        d = d.parent
    return out[::-1]


def link(resolver, sources: Dict[str, bytes]) -> None:
    """Add the links between languages to the resolver's edges (in place)."""
    if not sources:
        return
    linker = _Linker(resolver, sources)
    resolver.edges.extend(linker.run())


# ====================================================================== reading the links back


def language_of(path: str) -> str:
    from pmap.langs import EXT_TO_LANGUAGE

    if path.endswith(".py"):
        return "python"
    return EXT_TO_LANGUAGE.get(posixpath.splitext(path)[1], "")


def bridge_edges(actual: ActualMap) -> List[ActualEdge]:
    return [e for e in actual.edges if e.bridge]


def gaps(actual: ActualMap) -> List[ActualEdge]:
    """Channel methods a platform does not handle."""
    return [e for e in actual.edges if e.bridge and e.bridge.get("missing") and not e.bridge.get("mismatch")]


def mismatches(actual: ActualMap) -> List[ActualEdge]:
    """Links whose two sides are both known and do not agree (parameter counts, JNI types, channel names)."""
    return [e for e in actual.edges if e.bridge and e.bridge.get("mismatch")]


def problems(actual: ActualMap) -> List[ActualEdge]:
    """Gaps and mismatches: what `pmap check --strict` fails on."""
    return gaps(actual) + mismatches(actual)


def _is_link(edge: ActualEdge) -> bool:
    """An edge that reaches the other side: not a gap or a channel registered under another name."""
    b = edge.bridge or {}
    return bool(b) and not b.get("missing")


_CHANNEL_MISMATCHES = ("channel-name", "event-channel-name")


def _at(edge: ActualEdge) -> str:
    return f"{edge.location.path}:{edge.location.line}" if edge.location else "?"


def _there(bridge: dict) -> str:
    to = bridge.get("to") or {}
    return f"{to.get('path')}:{to.get('line')}" if to else "?"


def _lang_name(lang: object) -> str:
    return LANGUAGE_NAMES.get(str(lang), str(lang))


def _c_sites(edge: ActualEdge) -> List[Dict[str, object]]:
    """The sites a c-arity difference names: the header declarations bound to the function, then the edge's own
    site when it differs: [{"at", "role", "lang", "count"}]."""
    d = ((edge.bridge or {}).get("mismatch") or {}).get("detail") or {}
    sites = [{"at": h.get("at"), "role": "declares", "lang": h.get("lang"), "count": h.get("count")}
             for h in d.get("declarations") or []]
    if d.get("role"):
        sites.append({"at": _at(edge), "role": d.get("role"), "lang": d.get("lang"), "count": d.get("count")})
    return sites


def _c_text(name: object, def_lang: object, takes: object, there: str, sites: List[Dict[str, object]]) -> str:
    return (f"C ABI {name}: {_lang_name(def_lang)} takes {_count(takes, 'parameter')} ({there}); "
            + "; ".join(f"{s['at']} {s['role']} {s['count']}" for s in sites))


def mismatch_text(edge: ActualEdge) -> str:
    """One line of facts for an edge whose sides differ (empty for any other edge), e.g.
    `JNI NativeCore.setVolume: Kotlin declares 2 parameters (…NativeCore.kt:14), Rust takes 1 after env and this
    (rust/src/jni.rs:23)`. Never which side is right."""
    b = edge.bridge or {}
    m = b.get("mismatch") or {}
    d = m.get("detail") or {}
    kind, at, there = m.get("kind"), _at(edge), _there(b)
    if kind == "jni-arity":
        takes, receiver = int(d.get("takes", 0)), "this" if d.get("receiver") == "this" else "class"
        tail = f"takes {takes} after env and {receiver}" if takes >= 0 else \
            f"takes {takes + 2} in all, fewer than env and {receiver}"
        return (f"JNI {d.get('label')}: {_lang_name(d.get('lang'))} declares {_count(d.get('declares'), 'parameter')} "
                f"({at}), {_lang_name(d.get('def_lang'))} {tail} ({there})")
    if kind == "jni-type":
        params = list(d.get("params") or [])
        if not params:
            return ""
        first, rest = params[0], params[1:]
        more = "".join(f"; parameter {p.get('index')}: {p.get('declares')} against {p.get('takes')}" for p in rest)
        return (f"JNI {d.get('label')}: {_lang_name(d.get('lang'))} declares parameter {first.get('index')} as "
                f"{first.get('declares')} ({at}), {_lang_name(d.get('def_lang'))} takes it as {first.get('takes')} "
                f"({there}){more}")
    if kind == "c-arity":
        sites = _c_sites(edge)
        return _c_text(b.get("name"), d.get("def_lang"), d.get("takes"), there, sites) if sites else ""
    if kind in _CHANNEL_MISMATCHES:
        event = kind == "event-channel-name"
        others = int(d.get("others") or 0)
        also = (f" (and {others} more channel{'s' if others != 1 else ''} of that namespace that no Dart code uses)"
                if others else "")
        where = platform_name(str(d.get("platform")))
        return (f"{'event channel' if event else 'channel'} {b.get('name')} ({at}): no {where} code in this project "
                f"{'registers this name' if event else 'registers a handler under this name'}; {where} code "
                f"{'registers' if event else 'handles'} {d.get('registered')}, which no Dart code "
                f"{'listens to' if event else 'calls'} ({there}){also}")
    return ""


def mismatch_list(actual: ActualMap) -> List[Dict[str, object]]:
    """The mismatches, one per JNI function or channel and one per C symbol (its sites together, each once),
    sorted: [{"kind", "name", "text", "at", "to", "detail" | "sites", "short"?}]."""
    out: List[Dict[str, object]] = []
    symbols: Dict[str, Dict[str, object]] = {}
    for e in mismatches(actual):
        b = e.bridge or {}
        m = b.get("mismatch") or {}
        d = dict(m.get("detail") or {})
        if m.get("kind") == "c-arity":
            entry = symbols.get(str(b.get("name")))
            if entry is None:
                entry = symbols[str(b.get("name"))] = {"kind": "c-arity", "name": b.get("name"), "to": _there(b),
                                                       "def_lang": d.get("def_lang"), "takes": d.get("takes"),
                                                       "sites": {}}
            for site in _c_sites(e):
                entry["sites"].setdefault((site["at"], site["role"]), site)  # type: ignore[union-attr]
            continue
        text = mismatch_text(e)
        if text:
            out.append({"kind": m.get("kind"), "name": b.get("name"), "text": text, "at": _at(e), "to": _there(b),
                        "detail": d})
    for entry in symbols.values():
        sites = sorted(entry["sites"].values(),  # type: ignore[union-attr]
                       key=lambda s: (s["role"] != "declares", s["lang"] == "dart", _natural(str(s["at"]))))
        if not sites:
            continue
        entry["sites"] = sites
        entry["at"] = sites[0]["at"]
        entry["text"] = _c_text(entry["name"], entry["def_lang"], entry["takes"], str(entry["to"]), sites)
        if len(sites) > _SHORT_SITES:  # the overview keeps its budget: `pmap check` lists every site
            entry["short"] = _c_text(entry["name"], entry["def_lang"], entry["takes"], str(entry["to"]),
                                     sites[:_SHORT_SITES]) + f"; +{len(sites) - _SHORT_SITES} more"
        out.append(entry)
    return sorted(out, key=lambda m: _natural(str(m["text"])))


_SHORT_SITES = 3
_OVERVIEW_MISMATCHES = 3


def describe(edge: ActualEdge) -> str:
    """` via channel audio/player "pause" -> ios/Classes/AudioPlayerPlugin.swift:88` for an edge between languages
    (empty for any other edge)."""
    b = edge.bridge or {}
    if not b:
        return ""
    method = f' "{b["method"]}"' if b.get("method") else ""
    to = b.get("to") or {}
    where = f" -> {to.get('path')}:{to.get('line')}" if to and not b.get("missing") else ""
    missing = f" (not handled in {b['missing']})" if b.get("missing") else ""
    unsent = _unsent(b.get("orphan"))
    differs = f" — {mismatch_text(edge)}" if b.get("mismatch") else ""
    return f" via {VIA_NAMES.get(str(b.get('via')), b.get('via'))} {b.get('name')}{method}{where}{missing}{unsent}" \
           f"{differs}"


def format_check(actual: ActualMap) -> str:
    """The part of `pmap check` about links between languages."""
    edges = bridge_edges(actual)
    if not edges:
        return ""
    kinds: Dict[str, int] = {}
    for e in edges:
        if _is_link(e):
            via = VIA_NAMES.get(str(e.bridge.get("via")), str(e.bridge.get("via")))  # type: ignore[union-attr]
            kinds[via] = kinds.get(via, 0) + 1
    lines = [f"Links between languages: {sum(kinds.values())}" + (" (" + ", ".join(
        f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ")" if kinds else "")]
    data = summary(actual)
    found = data["mismatches"]
    if data["gaps"]:
        lines.append("[!] GAPS (a platform that handles the channel does not handle this method):")
        lines.extend("  ✗ " + _gap_line(gap)[len("[GAP] "):] for gap in data["gaps"])
    elif not any(m["kind"] in _CHANNEL_MISMATCHES for m in found):
        lines.append("[OK] Every channel method called from one side is handled on each platform of the other side.")
    if found:
        lines.append("[!] SIGNATURES AND NAMES THAT DIFFER (both sides are known and do not agree):")
        lines.extend(f"  ✗ {m['text']}" for m in found)
    return "\n".join(lines) + "\n"


def check_dict(actual: ActualMap) -> Dict[str, object]:
    edges = bridge_edges(actual)
    return {"links": sum(1 for e in edges if _is_link(e)),
            "gaps": [{"from": e.from_id, "at": f"{e.location.path}:{e.location.line}" if e.location else None,
                      **(e.bridge or {})} for e in gaps(actual)],
            "mismatches": mismatch_list(actual)}


def summary(actual: ActualMap) -> Dict[str, list]:
    """The links grouped by kind (channels one by one), the gaps and the mismatches, for the overview, `pmap check`
    and the web map: {"groups": [{"via", "name", "dirs": [[from language, [to languages]]], "items": [...]}],
     "gaps": [{"name", "method", "from", "at", "missing", "to", "orphan"?}], "mismatches": mismatch_list(...)}."""
    nodes = {n.id: n for n in actual.nodes}

    def lang(node_id: str, fallback: str = "") -> str:
        node = nodes.get(node_id)
        path = node.location.path if node is not None and node.location else fallback
        return LANGUAGE_NAMES.get(language_of(path), language_of(path) or "?")

    def short(node_id: str) -> str:
        node = nodes.get(node_id)
        if node is None:
            return node_id.split(":")[-1]
        parent = nodes.get(node.parent_id or "")
        return f"{parent.name}.{node.name}" if parent is not None and parent.kind == "class" else node.name

    out: Dict[str, list] = {"groups": [], "gaps": [], "mismatches": mismatch_list(actual)}
    for e in gaps(actual):
        b = e.bridge or {}
        to = b.get("to") or {}
        gap = {"name": b.get("name"), "method": b.get("method"), "missing": b.get("missing"),
               "from": lang(e.from_id, e.location.path if e.location else ""),
               "at": f"{e.location.path}:{e.location.line}" if e.location else "",
               "to": f"{to.get('path')}:{to.get('line')}" if to else ""}
        if b.get("orphan"):
            gap["orphan"] = dict(b["orphan"])
        out["gaps"].append(gap)
    groups: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for e in bridge_edges(actual):
        b = e.bridge or {}
        if not _is_link(e):
            continue
        via = str(b.get("via"))
        src_lang = lang(e.from_id, e.location.path if e.location else "")
        dst_lang = lang(e.to_id, (b.get("to") or {}).get("path", ""))
        key = (via, str(b.get("name")) if via in ("channel", "event-channel", "message-channel", "pigeon") else "")
        g = groups.setdefault(key, {"dirs": {}, "items": {}})
        g["dirs"].setdefault(src_lang, set()).add(dst_lang)
        if via in ("channel", "event-channel", "message-channel", "pigeon"):
            item = str(b.get("method") or "")
        elif via in ("network-extension", "android-intent"):
            item = f"{short(e.from_id)} → {short(e.to_id)}"
        elif via in ("ffi", "tauri"):
            item = str(b.get("name"))
        elif via == "jni":
            item = short(e.from_id)
        else:
            item = short(e.to_id)
        g["items"][item] = True
    order = list(VIA_NAMES)
    for (via, name), g in sorted(groups.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order
                                                                 else len(order), kv[0][1])):
        out["groups"].append({"via": via, "name": name,
                              "dirs": [[src, sorted(dst)] for src, dst in sorted(g["dirs"].items())],
                              "items": sorted(g["items"])})
    return out


def _unsent(orphan: Optional[dict]) -> str:
    """` (Android handles "relase" at …/ControlHandler.kt:24, which no Dart code sends)` after a gap: a fact
    beside the gap, never a claim that it is the method meant."""
    if not orphan:
        return ""
    who = platform_name(str(orphan["platform"])) if orphan.get("platform") else "it"
    return f' ({who} handles "{orphan.get("method")}" at {orphan.get("path")}:{orphan.get("line")}, which no Dart ' \
           "code sends)"


def _gap_line(gap: dict) -> str:
    where = f" ({gap['to']})" if gap["to"] else ""
    unsent = _unsent(gap.get("orphan"))
    return (f"[GAP] channel {gap['name']}: \"{gap['method']}\" is called from {gap['from']} ({gap['at']}) "
            f"but {gap['missing']} does not handle it{where}{unsent}")


def summary_lines(actual: ActualMap, limit: int = 14) -> List[str]:
    """One line per channel or kind of link: gaps first, then mismatches; for the agent overview and `pmap check`."""
    data = summary(actual)
    lines: List[str] = [_gap_line(gap) for gap in data["gaps"]]
    found = data["mismatches"]
    lines.extend(f"[MISMATCH] {m.get('short') or m['text']}" for m in found[:_OVERVIEW_MISMATCHES])
    if len(found) > _OVERVIEW_MISMATCHES:  # the link groups keep their place in the overview's budget
        lines.append(f"[MISMATCH] +{len(found) - _OVERVIEW_MISMATCHES} more: `pmap check` lists them")
    for g in data["groups"]:
        dirs = "; ".join(f"{src} → {', '.join(dst)}" for src, dst in g["dirs"])
        items = g["items"]
        shown = ", ".join(items[:8]) + (f" +{len(items) - 8}" if len(items) > 8 else "")
        name = g["name"]
        title = VIA_NAMES.get(g["via"], g["via"]) + (" (name not found)" if name == "?" else f" {name}" if name else "")
        lines.append(f"{title}: {dirs} ({shown})")
    if len(lines) > limit:
        lines = lines[:limit - 1] + [f"... {len(lines) - limit + 1} more"]
    return lines
