"""Inputs for `pmap twins --exec`: the string literals a repository's own tests already feed its code.

The copies of one piece of logic are compared on the inputs their authors thought worth testing, in every language:
a stream link written in a C# test is just as good an input for the Swift and Rust copies. This module lexes source
files just enough to find string literals (unescaped per language, interpolated ones skipped because their value is
not in the source) and comments (where an intended difference would be explained). It reads either the working tree
or one git revision, so a dirty checkout can be compared at HEAD without touching it.

A literal is an input only when its whole value is in the source. A piece of a concatenation is joined with the
pieces next to it when the language joins them at compile time (`"a" + "b"` in C#, Java, Swift; `"a" "b"` in C,
Python); a piece glued to anything else, an interpolated string and a format template (`format!("stream://{host}")`)
have no fixed value, and the caller lists them as not executed instead of running a prefix no test feeds.
"""

from __future__ import annotations

import bisect
import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

LANG_BY_EXT = {
    ".swift": "swift", ".rs": "rust", ".cs": "csharp", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".go": "go", ".py": "python", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".m": "c", ".mm": "c", ".dart": "dart",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".pmap", "node_modules", "target", "build", ".build", "DerivedData", "Pods", "Carthage",
    "dist", "__pycache__", ".venv", "venv", ".gradle", ".idea", ".dart_tool", ".tox", ".mypy_cache", "bin", "obj",
}
# Somebody else's code, for the static search (pmap/twins.py) and the input harvest alike: one definition.
VENDORED_PATH = re.compile(r"(?:^|/)(?:third[_-]?party|3rd[_-]?party|vendor|vendored|external|extern|deps)/")
_LINGUIST_VENDORED = re.compile(r"(?<![-\w])linguist-vendored(?:=true)?(?![\w=-])")
MAX_FILE_BYTES = 2_000_000
NESTED_BLOCK_COMMENTS = {"swift", "rust", "kotlin", "dart"}
_TEST_DIR = re.compile(r"(?i:^(?:tests?|specs?|__tests__|androidtest|unittests?|testing)$|[._-](?:tests?|specs?)$)"
                       r"|Tests?$")
_TEST_STEM = re.compile(r"(?i:^test_|_tests?$|[._-](?:test|spec)$)|Tests?$")
_CFG_TEST = re.compile(r"#\[cfg\(test\)\]\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*\{")
_CHAR_LIT = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F]{1,6}\}|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)|[^\\'\n])'")
_OPENERS = {
    "rust": re.compile(r"//|/\*|(?<![\w])[bc]?r#*\"|(?<![\w])[bc]?\"|'"),
    "swift": re.compile(r"//|/\*|#+\"|\""),
    "csharp": re.compile(r"//|/\*|(?:\$+@?|@\$*)?\"|'"),
    "python": re.compile(r"#|(?<![\w])[rRbBuUfF]{0,2}(?:'|\")"),
    "shell": re.compile(r"(?:(?<=\s)|^)#|'|\"", re.M),
    "dart": re.compile(r"//|/\*|(?<![\w])r?(?:'|\")"),
    "go": re.compile(r"//|/\*|\"|`|'"),
    "js": re.compile(r"//|/\*|\"|`|'"),
}
for _lang in ("java", "kotlin", "c"):
    _OPENERS[_lang] = re.compile(r"//|/\*|\"|'")
_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "b": "\b", "f": "\f", "v": "\v",
               "a": "\a", "e": "\x1b", "$": "$", "`": "`", "?": "?", "/": "/", "0": "\0"}
_OCTAL_LANGS = {"python", "java", "c", "go"}
# How a language joins two literals at compile time: with `+` between them, or written side by side.
_PLUS_JOIN = {"csharp", "java", "kotlin", "swift", "js", "dart", "python", "go"}
_SIDE_JOIN = {"c", "python", "dart"}
_RUST_FORMAT = re.compile(r"\b(?:format|format_args|print|println|eprint|eprintln|write|writeln|panic|assert\w*|"
                          r"debug_assert\w*|unreachable|todo|unimplemented|bail|anyhow|ensure|log|info|warn|error|"
                          r"debug|trace)!\s*$")
_RUST_HOLE = re.compile(r"\{(?:[A-Za-z_]\w*|\d+)?(?::[^{}]*)?\}")
_FORMAT_CALLS = {  # (call before the literal, placeholder it fills)
    "csharp": (re.compile(r"\b(?:[Ss]tring\.Format|AppendFormat|Console\.Write(?:Line)?)\s*$"), re.compile(r"\{\d+")),
    "java": (re.compile(r"\b(?:String\.format|formatted|printf|format)\s*$"), re.compile(r"%[-#+ 0,(]*\d*(?:\.\d+)?[a-zA-Z%]")),
    "kotlin": (re.compile(r"\b(?:String\.format|format)\s*$"), re.compile(r"%[-#+ 0,(]*\d*(?:\.\d+)?[a-zA-Z%]")),
    "swift": (re.compile(r"\bString\s*\(\s*format\s*:\s*$"), re.compile(r"%[-#+ 0]*\d*(?:\.\d+)?(?:@|l*[a-zA-Z])")),
    "c": (re.compile(r"\b(?:s?n?printf|fprintf|NSString\s+stringWithFormat)\s*$"), re.compile(r"%[-#+ 0]*\d*(?:\.\d+)?[a-zA-Z@]")),
}
_PY_HOLE = re.compile(r"\{[A-Za-z_0-9]*(?:[!:][^{}]*)?\}")
_PERCENT_HOLE = re.compile(r"%(?:\([^)]*\))?[-#+ 0]*\d*(?:\.\d+)?[sdifrx]")


@dataclass
class Literal:
    line: int
    value: Optional[str]  # None: its value is not in the source (interpolated, a template, part of a concatenation)
    triple: bool = False
    start: int = 0
    end: int = 0
    raw: str = ""  # the text between the quotes as written
    why: str = ""  # why value is None


@dataclass
class TestLiteral:
    path: str
    line: int
    value: Optional[str]
    raw: str = ""
    why: str = ""


@dataclass
class Lexed:
    literals: List[Literal] = field(default_factory=list)
    comments: List[Tuple[int, str]] = field(default_factory=list)
    code: str = ""  # the text with strings and comments blanked out (newlines kept), for brace matching


@dataclass
class _Spec:
    close: str
    multiline: bool = False
    skip_backslash: bool = True  # a backslash protects the next char from closing the string
    escapes: bool = True         # unescape the body
    esc: str = "\\"              # Swift raw strings escape with backslash + their hashes
    verbatim: bool = False       # C# @"": a doubled quote is one quote
    interp: str = ""             # "swift" \( ), "brace" { }, "dollar" $x ${ }, "template" ${ }, "shell" $x $( )
    triple: bool = False


def language_of(path: str) -> Optional[str]:
    return LANG_BY_EXT.get(PurePosixPath(path).suffix.lower())


def is_test_path(path: str, globs: Optional[Sequence[str]] = None) -> bool:
    """Test files by convention, or by the config's own globs when it gives them (they then replace the defaults)."""
    if globs:
        return any(fnmatch.fnmatchcase(path, g) for g in globs)
    parts = PurePosixPath(path).parts
    return any(_TEST_DIR.search(p) for p in parts[:-1]) or bool(_TEST_STEM.search(PurePosixPath(path).stem))


def unescape(body: str, lang: str) -> str:
    """Backslash escapes of one language; an escape this function does not know is kept as written."""
    if "\\" not in body:
        return body
    out: List[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nx = body[i + 1]
        if nx == "\n":
            i += 2
            while lang == "rust" and i < n and body[i] in " \t\r\n":
                i += 1
            continue
        m = None
        if nx == "u" and body[i + 2:i + 3] == "{":
            m = re.match(r"\\u\{([0-9a-fA-F]{1,6})\}", body[i:])
        elif nx == "u":
            m = re.match(r"\\u([0-9a-fA-F]{4})", body[i:])
        elif nx == "U":
            m = re.match(r"\\U([0-9a-fA-F]{8})", body[i:])
        elif nx == "x":
            m = re.match(r"\\x([0-9a-fA-F]{1,4})" if lang == "csharp" else r"\\x([0-9a-fA-F]{2})", body[i:])
        elif nx in "01234567" and lang in _OCTAL_LANGS:
            m = re.match(r"\\([0-7]{1,3})", body[i:])
            if m:
                out.append(chr(int(m.group(1), 8)))
                i += len(m.group(0))
                continue
        if m:
            code = int(m.group(1), 16)
            out.append(chr(code) if code <= 0x10FFFF else m.group(0))
            i += len(m.group(0))
            continue
        out.append(_SIMPLE_ESC.get(nx, "\\" + nx))
        i += 2
    text = "".join(out)
    try:  # \uD83D\uDE00 in Java, C# and JS: two UTF-16 halves of one character
        return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeDecodeError:
        return text


def _opener(lang: str, text: str, i: int, tok: str) -> Tuple[Optional[_Spec], int]:
    """The string that starts at i (tok is what the opener regex matched), and where its body starts."""
    if lang == "rust":
        if tok == "'":
            m = _CHAR_LIT.match(text, i)
            return (None, i + 1) if not m else (_Spec("'"), i + 1)
        prefix = tok[:-1]
        if "r" in prefix:
            hashes = prefix.count("#")
            return _Spec('"' + "#" * hashes, multiline=True, skip_backslash=False, escapes=False), i + len(tok)
        return _Spec('"', multiline=True), i + len(tok)
    if lang == "swift":
        hashes = tok.count("#")
        start = i + len(tok)
        triple = text.startswith('""', start)
        close = ('"""' if triple else '"') + "#" * hashes
        spec = _Spec(close, multiline=triple, esc="\\" + "#" * hashes, interp="swift", triple=triple)
        return spec, start + (2 if triple else 0)
    if lang == "csharp":
        if tok == "'":
            return (_Spec("'"), i + 1) if _CHAR_LIT.match(text, i) else (None, i + 1)
        prefix = tok[:-1]
        start = i + len(tok)
        quotes = 1
        while text.startswith('"', start + quotes - 1):
            quotes += 1
        interp = "brace" if "$" in prefix else ""
        if quotes >= 3:  # C# 11 raw string literal
            spec = _Spec('"' * quotes, multiline=True, skip_backslash=False, escapes=False, interp=interp, triple=True)
            return spec, start + quotes - 1
        if "@" in prefix:
            return _Spec('"', multiline=True, skip_backslash=False, escapes=False, verbatim=True, interp=interp), start
        return _Spec('"', interp=interp), start
    if lang == "python":
        q = tok[-1]
        prefix = tok[:-1].lower()
        start = i + len(tok)
        triple = text.startswith(q * 2, start)
        interp = "brace" if "f" in prefix else ""
        spec = _Spec(q * 3 if triple else q, multiline=triple, escapes="r" not in prefix, interp=interp, triple=triple)
        return spec, start + (2 if triple else 0)
    if lang == "shell":
        if tok == "'":
            return _Spec("'", multiline=True, skip_backslash=False, escapes=False), i + 1
        return _Spec('"', multiline=True, interp="shell"), i + 1
    if lang == "dart":
        q = tok[-1]
        start = i + len(tok)
        triple = text.startswith(q * 2, start)
        raw = tok.startswith("r")
        spec = _Spec(q * 3 if triple else q, multiline=triple, skip_backslash=not raw, escapes=not raw,
                     interp="" if raw else "dollar", triple=triple)
        return spec, start + (2 if triple else 0)
    if lang in ("go", "js") and tok == "`":
        if lang == "go":
            return _Spec("`", multiline=True, skip_backslash=False, escapes=False), i + 1
        return _Spec("`", multiline=True, interp="template"), i + 1
    if tok == "'":
        if lang == "js":
            return _Spec("'"), i + 1
        return (_Spec("'"), i + 1) if _CHAR_LIT.match(text, i) else (None, i + 1)
    # a double quote in Java, Kotlin, C, Go, JS
    start = i + 1
    if lang in ("java", "kotlin") and text.startswith('""', start):
        kotlin = lang == "kotlin"
        return _Spec('"""', multiline=True, skip_backslash=not kotlin, escapes=not kotlin,
                     interp="dollar" if kotlin else "", triple=True), start + 2
    return _Spec('"', interp="dollar" if lang == "kotlin" else ""), start


def _skip_balanced(text: str, i: int, lang: str, open_ch: str, close_ch: str) -> int:
    """Index after the bracket that closes the one at i, skipping strings inside (an interpolation may hold quotes)."""
    depth, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        elif ch in "\"'" and not (lang == "swift" and ch == "'"):
            m = _OPENERS[lang].match(text, i)
            if m:
                spec, body = _opener(lang, text, i, m.group(0))
                if spec:
                    i, _, _ = _scan_body(text, body, lang, spec)
                    continue
        i += 1
    return n


def _scan_body(text: str, j: int, lang: str, spec: _Spec) -> Tuple[int, Optional[str], bool]:
    """(end index, unescaped value or None, terminated) of a string body starting at j."""
    n, start, interpolated = len(text), j, False
    esc = spec.esc
    while j < n:
        if spec.verbatim and text.startswith('""', j):
            j += 2
            continue
        if text.startswith(spec.close, j):
            body = text[start:j]
            if interpolated:
                return j + len(spec.close), None, True
            if spec.verbatim:
                return j + len(spec.close), body.replace('""', '"'), True
            if spec.escapes:
                if len(esc) > 1:
                    body = body.replace("\\", "\\\\").replace(esc.replace("\\", "\\\\"), "\\")
                return j + len(spec.close), unescape(body, lang), True
            return j + len(spec.close), body, True
        ch = text[j]
        if ch == "\n" and not spec.multiline:
            return j, None, False
        if spec.interp == "swift" and text.startswith(esc + "(", j):
            j = _skip_balanced(text, j + len(esc), lang, "(", ")")
            interpolated = True
            continue
        if spec.skip_backslash and text.startswith(esc, j):
            j += len(esc) + 1
            continue
        if spec.interp == "brace" and ch == "{":
            if text.startswith("{{", j):
                j += 2
                continue
            j = _skip_balanced(text, j, lang, "{", "}")
            interpolated = True
            continue
        if spec.interp in ("dollar", "template", "shell") and ch == "$":
            nx = text[j + 1:j + 2]
            if nx == "{":
                j = _skip_balanced(text, j + 1, lang, "{", "}")
                interpolated = True
                continue
            if spec.interp == "shell" and nx == "(":
                j = _skip_balanced(text, j + 1, lang, "(", ")")
                interpolated = True
                continue
            shell_special = spec.interp == "shell" and nx != "" and nx in "0123456789@*#?$!"
            if spec.interp != "template" and (nx.isalpha() or nx == "_" or shell_special):
                interpolated = True
        j += 1
    return n, None, False


def lex(text: str, lang: str) -> Lexed:
    """String literals and comments of one source file, plus its code with both blanked out."""
    rx = _OPENERS.get(lang)
    out = Lexed()
    if rx is None:
        out.code = text
        return out
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    line_of = lambda pos: bisect.bisect_right(starts, pos)  # noqa: E731
    blank: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        m = rx.search(text, i)
        if not m:
            break
        tok, pos = m.group(0), m.start()
        if tok in ("//", "#") and not (tok == "#" and lang not in ("python", "shell")):
            end = text.find("\n", pos)
            end = n if end < 0 else end
            out.comments.append((line_of(pos), text[pos:end]))
            blank.append((pos, end))
            i = end
            continue
        if tok == "/*":
            end = _block_comment_end(text, pos, lang in NESTED_BLOCK_COMMENTS)
            for k, part in enumerate(text[pos:end].split("\n")):
                out.comments.append((line_of(pos) + k, part))
            blank.append((pos, end))
            i = end
            continue
        spec, body = _opener(lang, text, pos, tok)
        if spec is None:
            i = body
            continue
        end, value, done = _scan_body(text, body, lang, spec)
        line = line_of(pos)
        raw = text[body:end - len(spec.close)] if done else text[body:end]
        out.literals.append(Literal(line, value, spec.triple, pos, end, raw, "interpolated" if value is None else ""))
        if lang == "python" and spec.triple and not text[starts[line - 1]:pos].strip():
            out.comments.extend((line + k, part) for k, part in enumerate(text[pos:end].split("\n")))
        blank.append((pos, end))
        i = max(end, pos + 1)
    chars = list(text)
    for a, b in blank:
        for k in range(a, b):
            if chars[k] != "\n":
                chars[k] = " "
    out.code = "".join(chars)
    out.literals = _fixed_values(out.literals, out.code, lang)
    return out


def _depths(code: str, positions: List[int]) -> List[int]:
    """Bracket depth at each (sorted) position of blanked code."""
    out, depth, k = [], 0, 0
    marks = [(m.start(), 1 if m.group(0) in "([{" else -1) for m in re.finditer(r"[()\[\]{}]", code)]
    for pos in positions:
        while k < len(marks) and marks[k][0] < pos:
            depth += marks[k][1]
            k += 1
        out.append(depth)
    return out


def _fixed_values(lits: List[Literal], code: str, lang: str) -> List[Literal]:
    """Join the pieces of one concatenated literal; a piece glued to anything else, and a format template, keep no
    value (the test does not feed that text verbatim)."""
    if not lits:
        return lits
    depth = _depths(code, [lit.start for lit in lits])
    groups: List[List[Literal]] = [[lits[0]]]
    for k in range(1, len(lits)):
        prev, nxt = groups[-1][-1], lits[k]
        gap = code[prev.end:nxt.start]
        bare = re.sub(r"\\\n", "", gap).strip()
        # Python ends a statement at a line break outside brackets: two literals on two lines are two statements
        one_statement = lang != "python" or "\n" not in gap.replace("\\\n", "") or depth[k] > 0
        if one_statement and ((bare == "+" and lang in _PLUS_JOIN) or (bare == "" and lang in _SIDE_JOIN)):
            groups[-1].append(nxt)
        else:
            groups.append([nxt])
    out: List[Literal] = []
    for k, g in enumerate(groups):
        first, last = g[0], g[-1]
        values = [lit.value for lit in g]
        joined = Literal(first.line, None if None in values else "".join(v or "" for v in values),
                         any(lit.triple for lit in g), first.start, last.end, "".join(lit.raw for lit in g),
                         next((lit.why for lit in g if lit.why), ""))
        before, after = code[:first.start].rstrip(), code[last.end:].lstrip()
        glued = (before.endswith("+") and not before.endswith("++")) or (after.startswith("+") and
                                                                         not after.startswith(("++", "+=")))
        if not glued and lang != "shell":  # another literal right beside it, which this language does not join
            gaps = [code[groups[k - 1][-1].end:first.start] if k else "\n",
                    code[last.end:groups[k + 1][0].start] if k + 1 < len(groups) else "\n"]
            glued = any("\n" not in gap and not gap.strip() for gap in gaps)
        if glued:
            joined.value, joined.why = None, "part of a concatenated string"
        elif joined.value is not None and _is_template(joined, code, lang):
            joined.value, joined.why = None, "a format template"
        out.append(joined)
    return out


def _is_template(lit: Literal, code: str, lang: str) -> bool:
    """The format string of format!(...), "...".format(...), String.format(...): its holes are filled at run time."""
    value = lit.value or ""
    after = code[lit.end:lit.end + 40].lstrip()
    if lang == "python":
        return (after.startswith(".format") and bool(_PY_HOLE.search(value))) or \
            (after.startswith("%") and not after.startswith("%=") and bool(_PERCENT_HOLE.search(value)))
    if lang == "kotlin" and after.startswith(".format") and _FORMAT_CALLS["kotlin"][1].search(value):
        return True
    if lang == "java" and after.startswith(".formatted") and _FORMAT_CALLS["java"][1].search(value):
        return True
    call = _call_before(code, lit.start)
    if call is None:
        return False
    head, first_arg = call
    if lang == "rust":
        return bool(_RUST_FORMAT.search(head)) and bool(_RUST_HOLE.search(value))
    rule = _FORMAT_CALLS.get(lang)
    return bool(rule and rule[0].search(head) and rule[1].search(value)) and (first_arg or lang == "swift")


def _call_before(code: str, pos: int, reach: int = 2000) -> Optional[Tuple[str, bool]]:
    """(text before the open bracket of the call this position is an argument of, whether it is the first one)."""
    depth, i, lo, first = 0, pos - 1, max(0, pos - reach), True
    while i >= lo:
        ch = code[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                return (code[max(0, i - 80):i], first) if ch == "(" else None
            depth -= 1
        elif ch == "," and depth == 0:
            first = False
        elif ch == ";" and depth == 0:
            return None
        i -= 1
    return None


def _block_comment_end(text: str, pos: int, nested: bool) -> int:
    if not nested:
        end = text.find("*/", pos + 2)
        return len(text) if end < 0 else end + 2
    depth, i = 0, pos
    while i < len(text):
        if text.startswith("/*", i):
            depth += 1
            i += 2
        elif text.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return len(text)


def rust_test_ranges(code: str) -> List[Tuple[int, int]]:
    """Line ranges of `#[cfg(test)] mod x { ... }` in blanked Rust code: the tests of a file that is not a test file."""
    starts = [0] + [m.end() for m in re.finditer("\n", code)]
    ranges = []
    for m in _CFG_TEST.finditer(code):
        depth, i = 0, m.end() - 1
        while i < len(code):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        ranges.append((bisect.bisect_right(starts, m.start()), bisect.bisect_right(starts, i)))
    return ranges


class GitError(Exception):
    """A revision cannot be read: git is missing, or the revision does not exist (a config problem, exit 2)."""


def _git(root: Path, args: List[str], data: Optional[bytes] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root)] + args, input=data, capture_output=True)
    except OSError as exc:
        raise GitError(f"git not found ({exc}); it is needed to read the files at 'rev'") from exc


def _attr_match(glob: str, path: str) -> bool:
    """A .gitattributes pattern: without a slash it matches a name at any depth, with one it is rooted."""
    g = glob.lstrip("/")
    if g.endswith("/**"):
        return path.startswith(g[:-2])
    if "/" not in g.rstrip("/"):
        return any(fnmatch.fnmatchcase(part, g.rstrip("/")) for part in PurePosixPath(path).parts)
    return fnmatch.fnmatchcase(path, g) or fnmatch.fnmatchcase(path, g.rstrip("/") + "/*")


def vendored_rules(read: Callable[[str], Optional[str]]) -> Callable[[str], bool]:
    """Whether a path is somebody else's code: VENDORED_PATH, a `linguist-vendored` pattern in .gitattributes, or
    a submodule listed in .gitmodules. `read(rel)` returns a root file's text or None."""
    globs: List[str] = []
    for line in (read(".gitattributes") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#") and any(_LINGUIST_VENDORED.fullmatch(a) for a in parts[1:]):
            globs.append(parts[0])
    subs = [m.group(1).strip().strip("/") + "/" for m in
            re.finditer(r"(?m)^\s*path\s*=\s*(.+?)\s*$", read(".gitmodules") or "")]

    def vendored(path: str) -> bool:
        return bool(VENDORED_PATH.search(path)) or any(path.startswith(s) for s in subs) or \
            any(_attr_match(g, path) for g in globs)

    return vendored


class RepoFiles:
    """The files of a repository: the working tree, or one git revision read with `git cat-file` (nothing written)."""

    def __init__(self, root: Path, rev: Optional[str] = None) -> None:
        self.root, self.rev = root, rev
        self._paths: Optional[List[str]] = None
        self._cache: Dict[str, Optional[str]] = {}
        self._lexed: Dict[str, Lexed] = {}
        self._vendored: Optional[Callable[[str], bool]] = None

    def vendored(self, path: str) -> bool:
        if self._vendored is None:
            self._vendored = vendored_rules(self.read)
        return self._vendored(path)

    def paths(self) -> List[str]:
        if self._paths is None:
            self._paths = sorted(p for p in (self._git_paths() if self.rev else self._walk()) if not _skipped(p))
        return self._paths

    def _walk(self) -> Iterable[str]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            rel = Path(dirpath).relative_to(self.root)
            for name in filenames:
                yield (rel / name).as_posix()

    def _git_paths(self) -> List[str]:
        proc = _git(self.root, ["ls-tree", "-r", "-z", "--name-only", self.rev])
        if proc.returncode != 0:
            raise GitError(f"git ls-tree {self.rev} failed: {proc.stderr.decode(errors='replace').strip()}")
        return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]

    def read_many(self, paths: Sequence[str]) -> None:
        """Load several files at once (one `git cat-file --batch` for a revision)."""
        todo = [p for p in paths if p not in self._cache]
        if not todo:
            return
        if not self.rev:
            for p in todo:
                self._cache[p] = self._read_disk(p)
            return
        req = "".join(f"{self.rev}:./{p}\n" for p in todo).encode()
        proc = _git(self.root, ["cat-file", "--batch"], req)
        data, pos = proc.stdout, 0
        for p in todo:
            nl = data.find(b"\n", pos)
            header = data[pos:nl].split()
            pos = nl + 1
            if len(header) == 3 and header[1] == b"blob":
                size = int(header[2])
                blob = data[pos:pos + size]
                pos += size + 1
                binary = size > MAX_FILE_BYTES or b"\0" in blob[:4096]
                self._cache[p] = None if binary else blob.decode("utf-8", "replace")
            else:
                self._cache[p] = None

    def _read_disk(self, path: str) -> Optional[str]:
        f = self.root / path
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                return None
            raw = f.read_bytes()
        except OSError:
            return None
        return None if b"\0" in raw[:4096] else raw.decode("utf-8", "replace")

    def read(self, path: str) -> Optional[str]:
        if path not in self._cache:
            self.read_many([path])
        return self._cache.get(path)

    def lexed(self, path: str) -> Optional[Lexed]:
        if path not in self._lexed:
            text, lang = self.read(path), language_of(path)
            if text is None or lang is None:
                return None
            self._lexed[path] = lex(text, lang)
        return self._lexed[path]


def _skipped(path: str) -> bool:
    return any(part in SKIP_DIRS for part in PurePosixPath(path).parts[:-1])


def test_literals(files: RepoFiles, globs: Optional[Sequence[str]] = None) -> List[Tuple[str, int, str]]:
    """(path, line, value) of every fixed string literal in the repository's tests, in path and line order."""
    return [(t.path, t.line, t.value) for t in test_literal_records(files, globs) if t.value is not None]


def test_literal_records(files: RepoFiles, globs: Optional[Sequence[str]] = None) -> List[TestLiteral]:
    """Every string literal in the repository's tests, the ones with no fixed value too (they say why)."""
    paths = [p for p in files.paths() if language_of(p) and not files.vendored(p)]
    tests = [p for p in paths if is_test_path(p, globs)]
    test_set = set(tests)
    rust_extra = [] if globs else [p for p in paths if p.endswith(".rs") and p not in test_set]
    files.read_many(tests + rust_extra)
    found: List[TestLiteral] = []
    for p in tests:
        lx = files.lexed(p)
        found.extend(TestLiteral(p, lit.line, lit.value, lit.raw, lit.why) for lit in (lx.literals if lx else []))
    for p in rust_extra:
        text = files.read(p)
        if text is None or "cfg(test)" not in text:
            continue
        lx = files.lexed(p)
        ranges = rust_test_ranges(lx.code)
        found.extend(TestLiteral(p, lit.line, lit.value, lit.raw, lit.why) for lit in lx.literals
                     if any(a <= lit.line <= b for a, b in ranges))
    return sorted(found, key=lambda t: (t.path, t.line, t.value is None, t.value or "", t.raw))


class DocIndex:
    """Every comment line and every line of a *.md file, searchable at once: where intended differences are written."""

    def __init__(self, files: RepoFiles) -> None:
        paths = [p for p in files.paths() if language_of(p) or p.lower().endswith(".md")]
        files.read_many(paths)
        self.lines: List[Tuple[str, int, str]] = []
        for p in paths:
            if p.lower().endswith(".md"):
                text = files.read(p) or ""
                self.lines.extend((p, k + 1, line) for k, line in enumerate(text.split("\n")) if line.strip())
            else:
                lx = files.lexed(p)
                self.lines.extend((p, ln, c) for ln, c in (lx.comments if lx else []) if c.strip())
        low = [t.lower() for _, _, t in self.lines]  # lower() can lengthen a line (İ): offsets from the lowered text
        self.blob = "\n".join(low)
        self.offsets, pos = [], 0
        for t in low:
            self.offsets.append(pos)
            pos += len(t) + 1

    def block(self, k: int) -> str:
        """The comment block line k belongs to: the lines of the same file around it with no gap."""
        p = self.lines[k][0]
        lo = hi = k
        while lo > 0 and self.lines[lo - 1][0] == p and self.lines[lo - 1][1] == self.lines[lo][1] - 1:
            lo -= 1
        while hi + 1 < len(self.lines) and self.lines[hi + 1][0] == p and self.lines[hi + 1][1] == self.lines[hi][1] + 1:
            hi += 1
        return "\n".join(t for _, _, t in self.lines[lo:hi + 1])

    def find(self, needle: str) -> List[int]:
        """Indexes of the lines that contain needle (case-insensitive)."""
        needle = needle.lower()
        if not needle or "\n" in needle:
            return []
        hits, pos = [], self.blob.find(needle)
        while pos >= 0:
            k = bisect.bisect_right(self.offsets, pos) - 1
            hits.append(k)
            nxt = self.offsets[k + 1] if k + 1 < len(self.offsets) else len(self.blob)
            pos = self.blob.find(needle, nxt)
        return hits
