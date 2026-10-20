"""Links between languages that no compiler checks: Flutter platform channels, the C ABI, JNI, flutter_rust_bridge,
UniFFI and Tauri commands, and the system hand-offs of VPN apps (NetworkExtension, Android services).

A Flutter app reaches Swift, Kotlin and C++ through a channel name and a method name, both plain strings; Dart,
Swift, C++ and Kotlin reach Rust or C through symbol names. Neither compiler sees the other side, so neither does
an IDE, and a rename on one side breaks the app only at run time. This pass runs after the resolver and turns such
links into `calls` edges with a `bridge` record, e.g.
    {"via": "channel", "name": "app.vpn/control", "method": "connect", "to": {"path": "...", "line": 24}}
- `resolved`: the names match exactly one target on that side;
- `inferred`: several targets match, or the system picks the target (NetworkExtension, Android intents);
- `unresolved` with `"missing": "<platform>"`: a channel method that a platform handling the channel does not handle.
"""

from __future__ import annotations

import posixpath
import re
from bisect import bisect_right
from dataclasses import dataclass
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
    """`android`, `ios`, `macos`, `windows`, `linux` from a Flutter-style folder, else the language."""
    for part in path.split("/")[:-1]:
        if part in PLATFORM_DIRS:
            return part
    return language


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

    def channel_name(self, src: _Src, args: List[str], offset: Optional[int] = None) -> Optional[str]:
        for arg in args:
            arg = re.sub(r"^\s*name\s*:\s*", "", arg)
            value = _literal(arg)
            if value is None:  # "simple_view/" + id: the fixed part names the channel
                m = _PREFIXED_STR.match(arg.strip())
                value = m.group("s") if m and arg.strip()[m.end():].lstrip().startswith("+") else None
            if value:
                return _clean_channel(value)
        for arg in args:
            ident = re.sub(r"^\s*name\s*:\s*", "", arg).strip().split(".")[-1]
            if re.fullmatch(r"[A-Za-z_]\w*", ident):
                value = self.local_value(src, ident, offset) if offset is not None else None
                value = value or self.constant(src, ident)
                if value:
                    return _clean_channel(value)
        return None

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
                name = self.channel_name(src, args, m.start())
                if not name:
                    continue
                bind = _BIND.search(src.prefix(m.start()))
                ch = _Channel(m.group(1), name, self.site(src, m.start()), bind.group(1) if bind else None, end,
                              m.start())
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
                        method = _literal(args[0]) if args else None
                        if method is None and args and src.lang == "dart":
                            self.note_forwarder(src, recv_start, args[0], name, certain)
                    elif name is None:
                        continue  # `.send(` and `.receiveBroadcastStream(` only count on a known channel
                    if method:
                        calls.append(_Call(kind, name, certain, method, self.site(src, recv_start)))
            if src.lang == "dart" and "nvokeMethod" in src.raw:  # `invokeMethod(method, …)` inside an extension
                for m in _BARE_INVOKE.finditer(src.text):
                    paren = _open_paren(src.text, m.end())
                    args, _ = _args(src.text, paren) if paren >= 0 else ([], -1)
                    if args and _literal(args[0]) is None:
                        self.note_forwarder(src, m.start(), args[0], None, False)
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

    def note_forwarder(self, src: _Src, offset: int, arg: str, channel: Optional[str], certain: bool) -> None:
        """`Future<void> _call(String method, …) => _channel.invokeMethod(method, …)`: the method name is a parameter,
        so each call of `_call('pause', …)` is a call of the channel method `pause`."""
        decl = src.decl_at(src.line(offset))
        param = arg.strip()
        if decl is None or not re.fullmatch(r"[A-Za-z_]\w*", param):
            return
        positional, named = _parameters(decl.signature or "")
        if param not in positional and param not in named:
            return
        index = positional.index(param) if param in positional else None
        key = (src.rel if decl.name.startswith("_") else "", decl.name)
        if key not in self.forwarders:
            self.forwarders[key] = _Forwarder(decl.name, index, param, channel, certain, src.rel)

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
                for src in self.srcs.values():
                    if src.lang != "dart" or fw.name not in src.raw or (key[0] and src.rel != key[0]):
                        continue
                    for m in pattern.finditer(src.text):
                        paren = m.end() - 1
                        args, _ = _args(src.text, paren)
                        arg = _argument(args, fw.index, fw.param)
                        if arg is None:
                            continue
                        channel, certain = fw.channel, fw.certain
                        if channel is None:  # an extension on MethodChannel: the receiver is the channel
                            if not m.group(1):
                                continue
                            channel, certain = channel_of(src, m.group(1), m.start(1) + len(m.group(1)), "Method")
                            if channel is None:
                                continue
                        method = _literal(arg)
                        start = m.start(1) if m.group(1) else m.start()
                        if method is not None:
                            out.append(_Call("Method", channel, certain, method, self.site(src, start)))
                        else:
                            self.note_forwarder(src, start, arg, channel, certain)
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
                    self.link(call.site, f"unresolved:channel {channel}#{call.method}@{platform}", "unresolved",
                              "channel", channel, hs[0].site, call.method,
                              missing=f"{LANGUAGE_NAMES.get(hs[0].site.src.lang, hs[0].site.src.lang)} ({platform})"
                              if platform != hs[0].site.src.lang else LANGUAGE_NAMES.get(platform, platform))
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

    def platform_guard(self, site: _Site) -> Optional[Set[str]]:
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
                self.ffi_link(_Site(src, call.line, owner), symbol, found)
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
                self.ffi_link(site, symbol, exports[symbol])
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
                    self.ffi_link(_Site(src, src.line(m.start()), decl.node_id), symbol, exports[symbol])

    def ffi_link(self, site: _Site, symbol: str, found: List[_Export]) -> None:
        ids = sorted({x.node for x in found})
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

    def jni(self, exports: Dict[str, List[_Export]]) -> None:
        jni_exports = {s: xs for s, xs in exports.items() if s.startswith("Java_")}
        if not jni_exports:
            return
        for src in self.srcs.values():
            if src.lang not in ("kotlin", "java"):
                continue
            jvm_name = re.search(r'@file\s*:\s*JvmName\s*\(\s*"(\w+)"\s*\)', src.text)
            facade = jvm_name.group(1) if jvm_name else None
            for decl in src.f.decls:
                if decl.kind not in ("function", "method"):
                    continue
                head = src.line_text(decl.line_start)
                native = re.search(r"\bexternal\b[^\n]*\bfun\b", head) if src.lang == "kotlin" \
                    else re.search(r"\bnative\b[^(]*\(", head)
                if not native:
                    continue
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
                            self.link(site, ids[0], "resolved", "jni", symbol, found[0].site,
                                      label=f"{'.'.join(parts)}.{decl.name}")
                        else:
                            self.link(site, f"ambiguous:{symbol}", "inferred", "jni", symbol, found[0].site,
                                      candidates=ids)
                        break

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
            if src.lang != "swift" or not any(w in src.raw for w in ("VPNTunnel", "startTunnel", "stopTunnel",
                                                                     "sendProviderMessage")):
                continue
            for pattern, target in _NE_CALLS:
                for m in pattern.finditer(src.text):
                    found = providers.get(target, [])
                    site = self.site(src, m.start())
                    if not found or any(site.owner == d.node_id for d in found):
                        continue
                    ids = sorted(d.node_id for d in found)
                    self.link(site, ids[0] if len(ids) == 1 else f"ambiguous:{target}", "inferred",
                              "network-extension", m.group(0).lstrip(". \t").split("(")[0], self.decl_site(found[0]),
                              candidates=ids)
                    self.drop_unresolved_at(site, m.group(0).lstrip(". \t").split("(")[0])

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
    return [e for e in actual.edges if e.bridge and e.bridge.get("missing")]


def describe(edge: ActualEdge) -> str:
    """` via channel app.vpn/control "connect" -> macos/Runner/VpnChannel.swift:24` for an edge between languages
    (empty for any other edge)."""
    b = edge.bridge or {}
    if not b:
        return ""
    method = f' "{b["method"]}"' if b.get("method") else ""
    to = b.get("to") or {}
    where = f" -> {to.get('path')}:{to.get('line')}" if to and not b.get("missing") else ""
    missing = f" (not handled in {b['missing']})" if b.get("missing") else ""
    return f" via {VIA_NAMES.get(str(b.get('via')), b.get('via'))} {b.get('name')}{method}{where}{missing}"


def format_check(actual: ActualMap) -> str:
    """The part of `pmap check` about links between languages."""
    edges = bridge_edges(actual)
    if not edges:
        return ""
    kinds: Dict[str, int] = {}
    for e in edges:
        if not (e.bridge or {}).get("missing"):
            via = VIA_NAMES.get(str(e.bridge.get("via")), str(e.bridge.get("via")))  # type: ignore[union-attr]
            kinds[via] = kinds.get(via, 0) + 1
    lines = [f"Links between languages: {sum(kinds.values())} (" +
             ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ")"]
    missing = gaps(actual)
    if missing:
        lines.append("[!] GAPS (a platform that handles the channel does not handle this method):")
        for line in summary_lines(actual, limit=len(missing) + 1)[:len(missing)]:
            lines.append("  ✗ " + line[len("[GAP] "):])
    else:
        lines.append("[OK] Every channel method called from one side is handled on each platform of the other side.")
    return "\n".join(lines) + "\n"


def check_dict(actual: ActualMap) -> Dict[str, object]:
    edges = bridge_edges(actual)
    return {"links": sum(1 for e in edges if not (e.bridge or {}).get("missing")),
            "gaps": [{"from": e.from_id, "at": f"{e.location.path}:{e.location.line}" if e.location else None,
                      **(e.bridge or {})} for e in gaps(actual)]}


def summary(actual: ActualMap) -> Dict[str, list]:
    """The links grouped by kind (channels one by one) and the gaps, for the overview, `pmap check` and the web map:
    {"groups": [{"via", "name", "dirs": [[from language, [to languages]]], "items": [...]}],
     "gaps": [{"name", "method", "from", "at", "missing", "to"}]}."""
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

    out: Dict[str, list] = {"groups": [], "gaps": []}
    for e in gaps(actual):
        b = e.bridge or {}
        to = b.get("to") or {}
        out["gaps"].append({"name": b.get("name"), "method": b.get("method"), "missing": b.get("missing"),
                            "from": lang(e.from_id, e.location.path if e.location else ""),
                            "at": f"{e.location.path}:{e.location.line}" if e.location else "",
                            "to": f"{to.get('path')}:{to.get('line')}" if to else ""})
    groups: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for e in bridge_edges(actual):
        b = e.bridge or {}
        if b.get("missing"):
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


def summary_lines(actual: ActualMap, limit: int = 14) -> List[str]:
    """One line per channel or kind of link, gaps first; for the agent overview and `pmap check`."""
    data = summary(actual)
    lines: List[str] = []
    for gap in data["gaps"]:
        where = f" ({gap['to']})" if gap["to"] else ""
        lines.append(f"[GAP] channel {gap['name']}: \"{gap['method']}\" is called from {gap['from']} ({gap['at']}) "
                     f"but {gap['missing']} does not handle it{where}")
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
