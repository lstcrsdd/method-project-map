"""`pmap twins --exec`: run every copy of one piece of logic on the same inputs and compare what they print.

The static `pmap twins` finds functions in different languages that use the same rare literals: candidate copies.
This module compares their outputs on the same inputs: a config names the copies (how to build each, how to run it
on one input per stdin line), the inputs come from the repository's own tests (string literals matching a regex,
in every language) plus synthetic ones, and each output is canonicalised (sorted keys, arrays of objects aligned by
content) and compared per key path. Optional judge: an external checker (`player-check --config {file}`) run on
every output a copy produced. Agreement on these inputs shows the copies agree on these inputs, nothing more.

What it reports are differences, never bugs: it does not know which copy is right, and the reference copy is only
the base the others are compared against. A difference is "documented" only when a comment in one of the group's
copy files, or a *.md line, says the difference is intended (an intent word such as "intended", "on purpose",
"by design" next to the key) or carries an explicit `twins: <copy> omits <path>` marker; it is "known" when its
fingerprint is listed in twins-accepted.json next to the config. A comment that merely names the key documents
nothing. Writer lines are found by the key literal in the copy's files and printed with how they were chosen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from pmap.twins_harvest import DocIndex, GitError, RepoFiles, test_literal_records

Command = Union[str, List[str]]
DEFAULT_RUN_TIMEOUT = 120.0
DEFAULT_BUILD_TIMEOUT = 900.0
MAX_VALUE_CHARS = 160
NEAR_WRITER_LINES = 8
ABSENT = object()
ORDER = "{order}"
IN_ORDER = "reference order"
PRODUCED = "produced output"
REJECTED = "returned the reject marker"
NOT_JUDGED = "not judged"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_NORM = re.compile(r'"[^"]*"|\'[^\']*\'|\d+')
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_PLACEHOLDER = re.compile(r"\{(repo|work|config_dir|file)\}")
# A comment or *.md line explains a difference only when it says the difference is meant...
INTENT = re.compile(r"(?i)\b(?:intended|intentional(?:ly)?|on purpose|deliberate(?:ly)?|by design|only this copy|"
                    r"unlike|differs?)\b|намеренно|специально|умышленно|в отличие")
# ...and not when it calls it a problem.
DOUBT = re.compile(r"(?i)\b(?:todo|fixme|xxx|hack|bugs?|should|must|wrong|broken)\b|#\d+")
MARKER = re.compile(r"(?i)\btwins:\s*`?([\w.+-]+)`?\s+(?:omits|differs(?:\s+at)?|writes)\s+`?([\w.\[\]{}+-]+)`?")


class ConfigError(Exception):
    """The config cannot be used (exit code 2)."""


@dataclass
class CopySpec:
    name: str
    language: str = ""
    files: List[str] = field(default_factory=list)
    build: Optional[Command] = None
    run: Optional[Command] = None
    reject_marker: str = "ERROR"
    timeout: float = DEFAULT_RUN_TIMEOUT
    build_timeout: float = DEFAULT_BUILD_TIMEOUT


@dataclass
class GroupSpec:
    name: str
    copies: List[CopySpec]
    from_tests: Optional[str] = None
    test_globs: Optional[List[str]] = None
    extra: List[str] = field(default_factory=list)
    judge: Optional[Command] = None
    reference: Optional[str] = None
    timeout: float = DEFAULT_RUN_TIMEOUT


@dataclass
class Config:
    path: Path
    groups: List[GroupSpec]
    rev: Optional[str] = None
    accepted: Dict[str, str] = field(default_factory=dict)
    accepted_path: Optional[Path] = None


@dataclass
class Input:
    text: str
    sources: List[str] = field(default_factory=list)
    synthetic: bool = False

    def origin(self) -> str:
        return self.sources[0] if self.sources else "synthetic input from the config"


@dataclass
class Outcome:
    kind: str  # "ok", "reject" or "error"
    value: Any = None
    canon: str = ""
    message: str = ""

    def signature(self) -> Tuple[str, str]:
        return (self.kind, self.canon if self.kind == "ok" else self.message if self.kind == "error" else "")


# ---------------------------------------------------------------------------------------------------------- config

def find_config(repo: Path, explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"config not found: {p}")
        return p.resolve()
    for name in ("twins.json", "twins.yaml", "twins.yml"):
        if (repo / name).is_file():
            return (repo / name).resolve()
    raise ConfigError(f"no twins.json or twins.yaml in {repo} (or pass --config FILE)")


def load_config(path: Path) -> Config:
    """twins.json, or twins.yaml in the block-style subset pmap.yaml_loader reads (quoted strings verbatim)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            from pmap.yaml_loader import parse_simple_yaml

            data = parse_simple_yaml(text)
        else:
            data = json.loads(text)
    except (ValueError, IndexError) as exc:
        raise ConfigError(f"{path}: cannot parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list) or not data["groups"]:
        raise ConfigError(f"{path}: expected an object with a non-empty 'groups' list")
    groups = [_group(g, k, path) for k, g in enumerate(data["groups"])]
    names = [g.name for g in groups]
    if len(set(names)) != len(names):
        raise ConfigError(f"{path}: group names must be unique")
    rev = data.get("rev")
    if rev is not None and (not isinstance(rev, str) or not rev.strip()):
        raise ConfigError(f"{path}: 'rev' must be a non-empty string")
    cfg = Config(path=path, groups=groups, rev=rev)
    cfg.accepted_path = path.parent / "twins-accepted.json"
    cfg.accepted = _load_accepted(cfg.accepted_path)
    return cfg


def _command(value: Any, where: str) -> Optional[Command]:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(v, (str, int, float)) for v in value):
        return [str(v) for v in value]
    raise ConfigError(f"{where}: a command is a string (run by the shell) or a list of arguments")


def _number(value: Any, default: float, where: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{where}: timeout must be a positive number of seconds")
    return float(value)


def _strings(value: Any, where: str) -> List[str]:
    if value is None or value == "":
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: expected a list of strings")
    return list(value)


def _group(g: Any, k: int, path: Path) -> GroupSpec:
    where = f"{path}: groups[{k}]"
    if not isinstance(g, dict) or not isinstance(g.get("name"), str) or not g["name"].strip():
        raise ConfigError(f"{where}: needs a 'name'")
    where = f"{path}: group {g['name']!r}"
    inputs = g.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ConfigError(f"{where}: 'inputs' must be an object")
    pattern = inputs.get("from_tests")
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"{where}: 'inputs.from_tests' must be a regular expression")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{where}: 'inputs.from_tests' is not a valid regular expression: {exc}") from exc
    extra = _strings(inputs.get("extra"), f"{where}: inputs.extra")
    if pattern is None and not extra:
        raise ConfigError(f"{where}: no inputs (give inputs.from_tests and/or inputs.extra)")
    raw_copies = g.get("copies")
    if not isinstance(raw_copies, list) or not raw_copies:
        raise ConfigError(f"{where}: needs a non-empty 'copies' list")
    timeout = _number(g.get("timeout"), DEFAULT_RUN_TIMEOUT, where)
    copies = []
    for j, c in enumerate(raw_copies):
        cw = f"{where}: copies[{j}]"
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            raise ConfigError(f"{cw}: needs a 'name'")
        marker = c.get("reject_marker", "ERROR")
        if not isinstance(marker, str) or not marker.strip():
            raise ConfigError(f"{cw}: 'reject_marker' must be a non-empty string")
        copies.append(CopySpec(
            name=c["name"], language=str(c.get("language") or ""), files=_strings(c.get("files"), f"{cw}: files"),
            build=_command(c.get("build"), f"{cw}: build"), run=_command(c.get("run"), f"{cw}: run"),
            reject_marker=marker.strip(), timeout=_number(c.get("timeout"), timeout, cw),
            build_timeout=_number(c.get("build_timeout"), DEFAULT_BUILD_TIMEOUT, cw)))
    names = [c.name for c in copies]
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}: copy names must be unique")
    reference = g.get("reference")
    if reference is not None and reference not in names:
        raise ConfigError(f"{where}: reference {reference!r} is not one of the copies {names}")
    return GroupSpec(name=g["name"], copies=copies, from_tests=pattern,
                     test_globs=_strings(inputs.get("test_globs"), f"{where}: inputs.test_globs") or None,
                     extra=extra, judge=_command(g.get("judge"), f"{where}: judge"), reference=reference,
                     timeout=timeout)


def _load_accepted(path: Path) -> Dict[str, str]:
    """twins-accepted.json: a list of fingerprints, or {"accepted": [{"fingerprint", "note"}]}."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path}: cannot parse: {exc}") from exc
    items = data.get("accepted") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ConfigError(f"{path}: expected a list of fingerprints or {{\"accepted\": [...]}}")
    out: Dict[str, str] = {}
    for item in items:
        if isinstance(item, str):
            out[item] = ""
        elif isinstance(item, dict) and isinstance(item.get("fingerprint"), str):
            out[item["fingerprint"]] = str(item.get("note") or item.get("reason") or "")
        else:
            raise ConfigError(f"{path}: each entry is a fingerprint or an object with 'fingerprint'")
    return out


# ---------------------------------------------------------------------------------------------------------- inputs

def _lines_of(value: str) -> List[str]:
    """One test literal as the inputs it holds: a trailing line terminator dropped, inner line breaks split."""
    text = value[:-2] if value.endswith("\r\n") else value[:-1] if value.endswith(("\n", "\r")) else value
    return [text] if not _LINE_BREAK.search(text) else [ln for ln in _LINE_BREAK.split(text) if ln.strip()]


def harvest(files: RepoFiles, group: GroupSpec) -> Tuple[List[Input], List[Tuple[Input, str]]]:
    """(inputs, not executed with why): test literals that fully match from_tests, one per line of the literal,
    plus the synthetic extras; deduplicated, sorted by content. Literals whose value is not in the source (an
    interpolation, a format template, a piece of a concatenation) and text that is not valid UTF-8 are not run."""
    by_text: Dict[str, Input] = {}
    skipped: Dict[Tuple[str, str], Input] = {}
    if group.from_tests:
        rx = re.compile(group.from_tests)
        for t in test_literal_records(files, group.test_globs):
            where = f"{t.path}:{t.line}"
            if t.value is None:
                if t.raw and rx.fullmatch(t.raw):
                    why = f"value not fixed in the source ({t.why or 'interpolated'})"
                    skipped.setdefault((t.raw, why), Input(t.raw)).sources.append(where)
                continue
            for text in _lines_of(t.value):
                if text.strip() and rx.fullmatch(text):
                    inp = by_text.setdefault(text, Input(text))
                    if where not in inp.sources:
                        inp.sources.append(where)
    for value in group.extra:
        if value not in by_text:
            by_text[value] = Input(value, synthetic=True)
    inputs = []
    for text in sorted(by_text):
        inp = by_text[text]
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            skipped[(text, "not valid UTF-8 (a lone surrogate)")] = inp
            continue
        if _LINE_BREAK.search(text):  # only a synthetic input can still hold one
            skipped[(text, "contains a line break (copies read one input per line)")] = inp
            continue
        inputs.append(inp)
    return inputs, [(inp, why) for (_t, why), inp in sorted(skipped.items(), key=lambda kv: (kv[0][1], kv[0][0]))]


def harvest_inputs(files: RepoFiles, group: GroupSpec) -> List[Input]:
    return harvest(files, group)[0]


# ---------------------------------------------------------------------------------------------------------- running

def _quote(value: str) -> str:
    """Quoting for the shell a string command runs in: cmd.exe on Windows, sh elsewhere."""
    return subprocess.list2cmdline([value]) if os.name == "nt" else shlex.quote(value)


def _expand(cmd: Command, subst: Dict[str, str]) -> Tuple[Union[str, List[str]], bool]:
    """Placeholders {repo} {work} {config_dir} {file}, in one pass (a value is never expanded again): raw in a list,
    quoted in a string command."""
    if isinstance(cmd, str):
        return _PLACEHOLDER.sub(lambda m: _quote(subst[m.group(1)]) if m.group(1) in subst else m.group(0), cmd), True
    return [_PLACEHOLDER.sub(lambda m: subst.get(m.group(1), m.group(0)), arg) for arg in cmd], False


def _kill(proc: subprocess.Popen) -> None:
    """The whole process group: a shell's children and a build tool's compilers die with it."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _exec(cmd: Command, subst: Dict[str, str], cwd: Path, env: Dict[str, str], timeout: float,
          stdin: Optional[str] = None) -> Tuple[Optional[int], str, str]:
    """(exit code, or None when it could not start or did not finish, stdout, stderr)."""
    args, shell = _expand(cmd, subst)
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(args, shell=shell, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE, **kwargs)
    except OSError as exc:
        return None, "", f"cannot start: {exc}"
    try:
        out, err = proc.communicate(None if stdin is None else stdin.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            proc.communicate(timeout=10)
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        return None, "", f"timed out after {timeout:g}s"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _out_lines(text: str) -> List[str]:
    """Output lines split on \\n only: U+2028 or \\x85 inside a JSON line is not a line break."""
    return [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]


def _first_line(text: str) -> str:
    lines = [_ANSI.sub("", ln).strip() for ln in _out_lines(text)]
    return lines[0] if lines else ""


def _why(code: Optional[int], err: str) -> str:
    if code is None:
        return err
    lines = [_ANSI.sub("", ln).strip() for ln in _out_lines(err)]
    tail = next((ln for ln in lines if re.search(r"(?i)\berror\b", ln)), lines[-1] if lines else "")
    return f"exit {code}: {tail}" if tail else f"exit {code}"


def scrubber(paths: Dict[str, Path]) -> Callable[[str], str]:
    """Replace the run's own directories in a message ({work}, {repo}, ...): a temp path changes every run, and a
    fingerprint built from it could never be accepted."""
    pairs: List[Tuple[str, str]] = []
    for name, p in paths.items():
        for variant in {str(p), os.path.realpath(str(p))}:
            if len(variant) > 1:
                pairs.append((variant, name))
    pairs.sort(key=lambda x: -len(x[0]))

    def scrub(text: str) -> str:
        for path, name in pairs:
            text = text.replace(path, name)
        return text

    return scrub


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _parse(line: str, marker: str) -> Outcome:
    line = line.strip()
    if line == marker:
        return Outcome("reject")
    try:
        value = json.loads(line)
    except ValueError:
        return Outcome("error", message=f"not JSON: {line[:120]}")
    return Outcome("ok", value=value, canon=canonical(value))


@dataclass
class CopyRun:
    copy: CopySpec
    executed: bool = False
    note: str = ""  # why it was not executed, or how
    outcomes: List[Outcome] = field(default_factory=list)


def run_copy(copy: CopySpec, inputs: Sequence[Input], work: Path, env: Dict[str, str], subst: Dict[str, str],
             scrub: Callable[[str], str] = lambda s: s) -> CopyRun:
    """Build once, then feed every input, one per stdin line. When the line count is off, one process per input;
    a run that cannot start or times out is not executed (never retried input by input at the same cost)."""
    res = CopyRun(copy)
    if copy.run is None:
        res.note = "no run command (attribution only)"
        return res
    work.mkdir(parents=True, exist_ok=True)
    env = dict(env, PMAP_TWINS_WORK=str(work))
    sub = dict(subst, work=str(work))
    if copy.build is not None:
        code, _, err = _exec(copy.build, sub, work, env, copy.build_timeout)
        if code != 0:
            res.note = scrub(f"build failed ({_why(code, err)})")
            return res
    if not inputs:
        res.executed = True
        return res
    feed = "".join(i.text + "\n" for i in inputs)
    code, out, err = _exec(copy.run, sub, work, env, copy.timeout, stdin=feed)
    lines = _out_lines(out)
    if code == 0 and len(lines) == len(inputs):
        res.executed, res.outcomes = True, [_parse(ln, copy.reject_marker) for ln in lines]
        return res
    if code is None or (code in (126, 127) and not lines):
        res.note = scrub(f"not run: {_why(code, err)}")
        return res
    res.note = scrub(f"one process per input (a single run {_why(code, err) if code != 0 else 'exited 0'}, "
                     f"printed {len(lines)} lines for {len(inputs)} inputs)")
    for inp in inputs:
        code, out, err = _exec(copy.run, sub, work, env, copy.timeout, stdin=inp.text + "\n")
        if code is None:
            res.outcomes = []
            res.note = scrub(f"not run: {err} on input {_short(inp.text, 60)!r} ({inp.origin()}), run one input "
                             "per process after the single run printed the wrong number of lines")
            return res
        lines = _out_lines(out)
        if len(lines) == 1:
            res.outcomes.append(_parse(lines[0], copy.reject_marker))
        elif not lines:
            res.outcomes.append(Outcome("error", message=scrub(f"no output ({_why(code, err)})")))
        else:
            res.outcomes.append(Outcome("error", message=f"printed {len(lines)} lines for one input"))
    res.executed = True
    return res


class Judge:
    """The external checker, run once per distinct output: accepted, refused with its first error line verbatim,
    or not judged (it timed out, or could not run: exit 126/127)."""

    def __init__(self, cmd: Command, work: Path, env: Dict[str, str], subst: Dict[str, str], timeout: float,
                 scrub: Callable[[str], str] = lambda s: s) -> None:
        self.cmd, self.work, self.env, self.subst, self.timeout, self.scrub = cmd, work, env, subst, timeout, scrub
        self.cache: Dict[str, Tuple[str, str]] = {}
        self.broken = ""
        self.judged = 0  # verdicts that were accepted or refused
        work.mkdir(parents=True, exist_ok=True)

    def verdict(self, out: Outcome) -> Optional[Tuple[str, str]]:
        """("accepted", ""), ("refused", first error line) or (NOT_JUDGED, why); None once the judge turned out
        not to run at all (nothing is judged)."""
        if self.broken:
            return None
        key = hashlib.sha256(out.canon.encode("utf-8")).hexdigest()[:16]
        if key not in self.cache:
            f = self.work / f"output-{key}.json"
            f.write_text(json.dumps(out.value, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf-8")
            code, so, se = _exec(self.cmd, dict(self.subst, file=str(f), work=str(self.work)), self.work,
                                 dict(self.env, PMAP_TWINS_WORK=str(self.work)), self.timeout)
            line = self.scrub((_first_line(se) or _first_line(so) or _why(code, se)).replace(str(f), "{file}"))
            if (code is None and se.startswith("cannot start")) or (code in (126, 127) and not self.judged):
                self.broken = self.scrub(se if code is None else f"exit {code}: {line}")
                return None
            if code is None:
                self.cache[key] = (NOT_JUDGED, se)
            elif code in (126, 127):
                self.cache[key] = (NOT_JUDGED, f"could not run (exit {code}: {line})")
            else:
                self.judged += 1
                self.cache[key] = ("accepted", "") if code == 0 else ("refused", line)
        return self.cache[key]


# ---------------------------------------------------------------------------------------------------------- compare

def _flat_items(value: Any, prefix: str = "") -> set:
    if isinstance(value, dict):
        out: set = set()
        for k, v in value.items():
            out |= _flat_items(v, f"{prefix}.{k}")
        return out
    return {(prefix, canonical(value))}


def align(a: List[Any], b: List[Any]) -> List[Tuple[Optional[int], Optional[int]]]:
    """Pair the objects of two arrays by the largest share of equal key/value leaves (never by a tag field).

    Greedy on the best pairs first, ties to the nearest positions; objects with nothing in common stay unpaired.
    """
    fa, fb = [_flat_items(x) for x in a], [_flat_items(x) for x in b]
    scored = []
    for i, x in enumerate(fa):
        for j, y in enumerate(fb):
            common = len(x & y)
            if common:
                scored.append((-common / len(x | y), abs(i - j), i, j))
    scored.sort()
    used_a, used_b, pairs = set(), set(), []
    for _, _, i, j in scored:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            pairs.append((i, j))
    pairs += [(i, None) for i in range(len(a)) if i not in used_a]
    pairs += [(None, j) for j in range(len(b)) if j not in used_b]
    return sorted(pairs, key=lambda p: (p[0] if p[0] is not None else len(a), p[1] if p[1] is not None else -1))


def _objects(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def walk(values: Dict[str, Any], base: str, path: str = "", npath: str = "",
         parents: Optional[Dict[str, Any]] = None, out: Optional[Dict[str, tuple]] = None) -> Dict[str, tuple]:
    """Leaves of several copies' outputs walked together: {(path, kind): (normalised path, values, parents)}.

    values maps a copy to its value at this path (ABSENT when it has none). Where some copies have a subtree and
    others do not, that is one "absent" leaf, and the walk goes on among the copies that have it. Objects recurse by
    key; arrays of objects are aligned by content to the base copy's array (`[+k]`: an element of another copy with no
    partner there), and their order is a leaf of its own (`path{order}`), since content alignment would otherwise
    hide that two copies list the same rules in a different order. Anything else is one "value" leaf.
    """
    out = {} if out is None else out
    parents = parents or {}
    here = path or "$"
    present = {n: v for n, v in values.items() if v is not ABSENT}
    if len(present) < len(values):
        out[(here, "absent")] = (npath or "$", dict(values), dict(parents))
    if len(present) < 2:
        return out
    vs = list(present.values())
    if all(isinstance(v, dict) for v in vs):
        for k in sorted({k for v in vs for k in v}, key=str):
            walk({n: v.get(k, ABSENT) for n, v in present.items()}, base, f"{path}.{k}" if path else str(k),
                 f"{npath}.{k}" if npath else str(k), present, out)
    elif all(_objects(v) for v in vs):
        b = base if base in present else next(iter(present))
        slots: Dict[str, Dict[str, Any]] = {}
        order: Dict[str, Any] = {b: IN_ORDER}
        for n, v in present.items():
            if n == b:
                for i, x in enumerate(v):
                    slots.setdefault(f"{path}[{i}]", {})[n] = x
                continue
            pairs, extra = align(present[b], v), 0
            matched = [(i, j) for i, j in pairs if i is not None and j is not None]
            in_n = [i for i, _ in sorted(matched, key=lambda q: q[1])]
            order[n] = IN_ORDER if in_n == sorted(in_n) else in_n
            for i, j in pairs:
                if i is None:
                    slots.setdefault(f"{path}[+{extra}]", {})[n] = v[j]
                    extra += 1
                elif j is not None:
                    slots.setdefault(f"{path}[{i}]", {})[n] = v[j]
        if len(present[b]) >= 2:
            out[(f"{path}{ORDER}", "order")] = (npath + ORDER, order, dict(present))
        for sub in sorted(slots, key=lambda s: (s.count("+"), int(re.findall(r"\d+", s[len(path):])[0]))):
            walk({n: slots[sub].get(n, ABSENT) for n in present}, base, sub, npath + "[]", present, out)
    else:
        out[(here, "value")] = (npath or "$", dict(present), dict(parents))
    return out


def _show(v: Any) -> str:
    if v is ABSENT:
        return "(absent)"
    s = canonical(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS - 1] + "…"


@dataclass
class Row:
    group: str
    kind: str        # "value", "absent", "reject", "error", "judge", "order"
    path: str        # concrete path of the first example ("" for whole-output rows)
    partition: List[List[str]]
    npath: str = ""
    inputs: List[Input] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)     # copy -> value in the first example
    parents: Dict[str, Any] = field(default_factory=dict)    # copy -> object holding the key in the first example
    against: tuple = ()  # the classes that disagree with the reference: the grouping key
    extra: tuple = ()    # the rest of the grouping key: normalised messages, judge verdicts
    fingerprint: str = ""
    writers: Dict[str, dict] = field(default_factory=dict)
    documented: List[str] = field(default_factory=list)
    accepted: Optional[str] = None
    same_writer: str = ""  # the differing copies write the key with the same line

    @property
    def count(self) -> int:
        return len(self.inputs)

    @property
    def leaf_key(self) -> Optional[str]:
        """The object key a value row is about; None for whole outputs and for unpaired array elements."""
        if self.kind not in ("value", "absent") or self.path.endswith("]") or self.path == "$":
            return None
        return re.split(r"[.\]]", self.path)[-1] or None


def _against(part: List[List[str]], base: Optional[str]) -> tuple:
    """Who disagrees with the base copy: the grouping key of a row, so a copy that failed on some inputs (and so
    is missing from their partition) does not split one difference into several rows."""
    return tuple(tuple(c) for c in part if base not in c)


def _partition(values: Dict[str, str], order: List[str]) -> List[List[str]]:
    classes: Dict[str, List[str]] = {}
    for name in order:
        if name in values:
            classes.setdefault(values[name], []).append(name)
    return sorted(classes.values(), key=lambda names: order.index(names[0]))


def compare(group: GroupSpec, inputs: List[Input], runs: List[CopyRun],
            judge: Optional[Judge]) -> Tuple[List[Row], dict]:
    """Rows of differences (identical ones across inputs grouped) and the agreement counts."""
    ran = [r for r in runs if r.executed]
    order = [r.copy.name for r in ran]
    ref_name = group.reference if group.reference in order else (order[0] if order else None)
    rows: Dict[tuple, Row] = {}
    stats: Dict[str, Any] = {"inputs": len(inputs), "agree": 0, "pairs": {}, "judge": {}, "judge_refusals": {},
                             "not_judged": {}, "compared": len(ran) >= 2, "reference": ref_name,
                             "agree_split": {"judge_accepts": 0, "judge_refuses": 0, "not_judged": 0,
                                             "all_rejected": 0}}
    if len(ran) < 1:
        return [], stats

    def add(inp: Input, against: tuple, extra: tuple = (), **kw: Any) -> None:
        """One more input for the row of this difference: same kind, normalised path, who disagrees (and words)."""
        key = (kw["kind"], kw.get("npath", ""), against, extra)
        if key not in rows:
            rows[key] = Row(group=group.name, against=against, extra=extra, **kw)
        if not rows[key].inputs or rows[key].inputs[-1] is not inp:  # two array elements, one input
            rows[key].inputs.append(inp)

    def judged(n: str, o: Outcome) -> Optional[Tuple[str, str]]:
        return judge.verdict(o) if judge is not None and o.kind == "ok" else None

    for idx in sorted(range(len(inputs)), key=lambda k: (inputs[k].synthetic, inputs[k].text)):
        inp = inputs[idx]  # inputs from tests first: a row's first example is a real one when there is one
        outs = {r.copy.name: r.outcomes[idx] for r in ran}
        sigs = {n: o.signature() for n, o in outs.items()}
        verdicts = {n: v for n, v in ((n, judged(n, o)) for n, o in outs.items()) if v is not None}
        if len(set(sigs.values())) == 1 and outs[ref_name].kind != "error":
            stats["agree"] += 1
            v = verdicts.get(ref_name)
            split = ("all_rejected" if outs[ref_name].kind == "reject" else
                     "judge_accepts" if v and v[0] == "accepted" else
                     "judge_refuses" if v and v[0] == "refused" else "not_judged")
            stats["agree_split"][split] += 1
        for r in ran:
            if r.copy.name != ref_name:
                p = stats["pairs"].setdefault(r.copy.name, [0, 0])
                p[1] += 1
                p[0] += sigs[r.copy.name] == sigs[ref_name] and sigs[ref_name][0] != "error"
        errors = {n: o.message for n, o in outs.items() if o.kind == "error"}
        if errors:
            norm = {n: _NORM.sub("#", m) for n, m in errors.items()}
            labels = {n: f"error: {norm[n]}" if n in errors else outs[n].kind for n in order}
            part = _partition(labels, order)
            failed = tuple(tuple(c) for c in part if c[0] in errors)
            shown = {n: (f"error: {errors[n]}" if n in errors else
                         PRODUCED if outs[n].kind == "ok" else REJECTED) for n in order}
            add(inp, failed, tuple(sorted(norm.items())), kind="error", path="", partition=part, values=shown)
        okrej = {n: o.kind for n, o in outs.items() if o.kind in ("ok", "reject")}
        if len(set(okrej.values())) > 1:
            part = _partition({n: k for n, k in okrej.items()}, order)
            vals, jclass = {}, []
            for n, k in okrej.items():
                vals[n] = REJECTED if k == "reject" else PRODUCED
                v = verdicts.get(n)
                if v is not None:
                    state, line = v
                    vals[n] += {"accepted": "; the judge accepted it", "refused": f"; the judge refused it: {line}"
                                }.get(state, f"; not judged: {line}")
                    jclass.append((n, state, _NORM.sub("#", line) if state == "refused" else ""))
            add(inp, _against(part, ref_name), tuple(jclass), kind="reject", path="", partition=part, values=vals)
        oks = [n for n in order if outs[n].kind == "ok"]
        decided = {n: v for n, v in verdicts.items() if v[0] in ("accepted", "refused")}
        for n, (state, line) in verdicts.items():
            if state == NOT_JUDGED:
                slot = stats["not_judged"].setdefault(n, {"count": 0, "why": line, "inputs": []})
                slot["count"] += 1
                slot["inputs"].append(inp)
                continue
            j = stats["judge"].setdefault(n, [0, 0])
            j[1] += 1
            j[0] += state == "accepted"
            if state == "refused":
                rk = (n, _NORM.sub("#", line))
                stats["judge_refusals"].setdefault(rk, {"copy": n, "line": line, "inputs": []})["inputs"].append(inp)
        if len({s for s, _ in decided.values()}) > 1:
            labels = {n: s for n, (s, _) in decided.items()}
            part = _partition(labels, order)
            refused = tuple(sorted((n, _NORM.sub("#", ln)) for n, (s, ln) in decided.items() if s == "refused"))
            add(inp, _against(part, ref_name), refused, kind="judge", path="", partition=part,
                values={n: ("accepted" if s == "accepted" else f"refused: {ln}") for n, (s, ln) in decided.items()})
        if len(oks) < 2:
            continue
        base = ref_name if ref_name in oks else oks[0]
        for (path, kind), (npath, vals, parents) in sorted(walk({n: outs[n].value for n in oks}, base).items()):
            if kind == "absent":
                shown = {n: ("absent" if v is ABSENT else "present") for n, v in vals.items()}
            else:
                shown = {n: canonical(v) for n, v in vals.items()}
            if len(set(shown.values())) == 1:
                continue
            part = _partition(shown, oks)
            add(inp, _against(part, base), kind=kind, path=path, npath=npath, partition=part,
                values=vals, parents=parents)
    kinds = ["reject", "error", "judge", "value", "absent", "order"]
    out = sorted(rows.values(), key=lambda r: (kinds.index(r.kind), r.npath, canonical(r.partition),
                                               canonical([list(x) for x in r.extra])))
    for row in out:  # the first example comes from a test when one exists
        row.inputs.sort(key=lambda i: (i.synthetic, i.text))
        row.fingerprint = fingerprint(row)
    return out, stats


def fingerprint(row: Row) -> str:
    """Stable across runs and input sets: group, kind, normalised path, who agrees with whom (and the words of the
    judge or of the failing copy, with the run's own directories already replaced)."""
    parts: List[Any] = [row.group, row.kind, row.npath, [list(c) for c in row.against]]
    if row.kind in ("judge", "error", "reject"):
        parts.append([list(x) if isinstance(x, tuple) else x for x in row.extra])
    return hashlib.sha1(canonical(parts).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------- attribution

_CONST = re.compile(r"\b(?:const|let|var|val|final\s+String|string)\s+([A-Za-z_]\w*)\s*"
                    r"(?::\s*&?(?:'static\s+)?(?:str|String))?\s*=\s*\"([^\"\\\n]*)\"")
_PY_CONST = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*(?::\s*str)?\s*=\s*[\"']([^\"'\\\n]*)[\"']", re.M)
_CHUNK = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
_INTERP = re.compile(r"\\\([^)]*\)|\$\{[^}]*\}|\{[^}]*\}|\$\w+")
_GLUE = {"to", "self", "this", "let", "var", "val", "const", "into", "to_string", "tostring", "clone", "text",
         "json", "put", "insert", "set", "string", "str", "some", "new", "return", "obj", "vec", "map"}


def _writer_patterns(key: str, names: List[str]) -> List[re.Pattern]:
    lit = "(?:\"" + re.escape(key) + "\"|'" + re.escape(key) + "')"
    pats = [
        lit + r"\s*:(?!:)",                                   # dict / json! / object literal
        r"\[\s*" + lit + r"\s*\]\s*=(?!=)",                    # out["key"] = value
        r"\b(?:put|insert|addProperty|Add|set|setdefault|WriteString|WriteNumber|WriteBoolean|WritePropertyName|"
        r"WriteStartObject|WriteStartArray|entry|Pair)\s*\(\s*" + lit,
        lit + r"\s+to\b",                                      # Kotlin mapOf("key" to value)
        r"(?<![\w.])\(\s*" + lit + r"\s*,",                      # a ("key", value) pair; not a call like get("key", d)
        r"^\s*" + lit + r"\s*\.\s*(?:into|to_string|to_owned)\s*\(\s*\)\s*,",  # Rust map.insert(\n "key".into(),
        r"(?:rename|SerialName|JsonPropertyName|JsonProperty|SerializedName|Json)\s*(?:\(|=)\s*(?:name\s*=\s*)?" + lit,
        r"\bcase\s+\w+\s*=\s*" + lit,                          # Swift CodingKeys
        r"json:\\?\"" + re.escape(key) + r"[,\\\"]",           # Go struct tag
    ]
    for n in names:  # the key written through a constant: [Keys.server: x], out[KEY] = x, put(KEY, x)
        name = r"(?:[\w.]+(?:\.|::))?" + re.escape(n)
        pats += [r"[\[{(,]\s*" + name + r"\s*:(?!:)", r"\[\s*" + name + r"\s*\]\s*=(?!=)",
                 r"\b(?:put|insert|Add|set)\s*\(\s*" + name + r"\b"]
    return [re.compile(p) for p in pats]


def _constants(files: RepoFiles, paths: List[str]) -> Dict[str, List[str]]:
    """value -> constant names defined in a copy's files (Rust const, Swift static let, C# const, Kotlin const val)."""
    table: Dict[str, List[str]] = {}
    for p in paths:
        text = files.read(p) or ""
        for m in list(_CONST.finditer(text)) + list(_PY_CONST.finditer(text)):
            table.setdefault(m.group(2), [])
            if m.group(1) not in table[m.group(2)]:
                table[m.group(2)].append(m.group(1))
    return table


def _region(code_lines: List[str], line: int, min_lines: int = 3, cap: int = 80) -> Tuple[int, int]:
    """Lines of the innermost bracket block around a line, widened until it spans a few lines."""
    text = "\n".join(code_lines)
    starts = [0]
    for ln in code_lines:
        starts.append(starts[-1] + len(ln) + 1)
    pos = starts[line - 1]
    lo, hi = pos, pos + len(code_lines[line - 1])
    while True:
        depth, a = 0, lo - 1
        while a >= 0:
            ch = text[a]
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    break
                depth -= 1
            a -= 1
        depth, b = 0, hi
        while b < len(text):
            ch = text[b]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            b += 1
        if a < 0 or b >= len(text):
            first, last = 1, len(code_lines)
            break
        first, last = text.count("\n", 0, a) + 1, text.count("\n", 0, b) + 1
        if last - first + 1 >= min_lines:
            break
        lo, hi = a, b + 1
    return max(first, line - cap), min(last, line + cap)


def _value_strings(v: Any) -> List[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def find_writer(files: RepoFiles, paths: List[str], key: str, value: Any, parent: Any) -> dict:
    """The line in a copy's files that writes `key`: the best candidate with how it was chosen, or a note saying
    what was searched when no line fits (never an absence: the key may come from a field or a computed name).

    Candidates are lines writing the key literal (or a constant holding it). Several candidates are told apart by
    literal fragments of the value on the line (`"\\(host)/128"` for "...::1/128") and by the sibling keys and values
    of the object around the key; a tie is reported as a tie, never resolved by guessing.
    """
    consts = _constants(files, paths)
    pats = _writer_patterns(key, consts.get(key, []))
    parent = parent if isinstance(parent, dict) else {}
    sib_keys = [k for k in parent if k != key]
    sib_vals = sorted({v for k, v in parent.items() if k != key and isinstance(v, str) and len(v) >= 2})
    cands = []
    fields = []
    for p in paths:
        text = files.read(p)
        lx = files.lexed(p)
        if text is None:
            continue
        lines = text.split("\n")
        code_lines = (lx.code if lx else text).split("\n")
        comment_lines = {ln for ln, _ in (lx.comments if lx else [])}
        field_rx = re.compile(r"^\s*(?:(?:pub(?:\([^)]*\))?|public|private|internal|open|final|static|readonly)\s+)*"
                              r"(?:(?:let|var|val)\s+)?" + re.escape(key) + r"\s*:\s*[\w\[<(]")
        for k, line in enumerate(lines, 1):
            if k in comment_lines and not code_lines[k - 1].strip():
                continue
            if field_rx.search(code_lines[k - 1] if k <= len(code_lines) else line):
                fields.append(f"{p}:{k}")
            if not any(pt.search(line) for pt in pats):
                continue
            first, last = _region(code_lines, k)
            block = "\n".join(lines[first - 1:last])
            score = 0
            lit = re.search("[\"']" + re.escape(key) + "[\"']", line)
            tail = line[lit.end():] if lit else line
            chunks = [m.group(1) or m.group(2) or "" for m in _CHUNK.finditer(tail)]
            frags = [f for c in chunks for f in _INTERP.split(c) if len(f) >= 2]
            vs = _value_strings(value)
            score += 3 * sum(1 for f in frags if f != key and f not in sib_keys and any(f in s for s in vs))
            score += sum(1 for s in sib_keys if f'"{s}"' in block or f"'{s}'" in block
                         or any(re.search(r"\b" + re.escape(n) + r"\b", block) for n in consts.get(s, [])))
            score += sum(1 for s in sib_vals if f'"{s}"' in block or f"'{s}'" in block
                         or any(re.search(r"\b" + re.escape(n) + r"\b", block) for n in consts.get(s, [])))
            cands.append((score, p, k, line.strip()))
    if not cands and value is ABSENT:
        return {"found": False, "note": f"the key is absent from this copy's output, and no line in its files writes "
                                        f"\"{key}\" as a literal or a known constant"}
    if not cands:
        note = (f"writer line not found (no line in this copy's files writes \"{key}\" as a literal or a known "
                "constant; it may come from a struct field, a constant elsewhere, or a computed key)")
        if fields:
            note += f"; possibly written by a field named {key} at {fields[0]} (not traced)"
        return {"found": False, "note": note}
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    if len(cands) == 1 or cands[0][0] > cands[1][0]:
        score, p, k, text = cands[0]
        how = ("the only line writing the key" if len(cands) == 1 else
               f"best of {len(cands)} lines writing the key, sibling-key score {score} vs {cands[1][0]}")
        return {"found": True, "path": p, "line": k, "how": how, "text": text}
    tied = [c for c in cands if c[0] == cands[0][0]]
    where = ", ".join(f"{p}:{k}" for _, p, k, _t in tied[:4]) + (" ..." if len(tied) > 4 else "")
    return {"found": False, "note": f"writer line not found ({len(tied)} lines write \"{key}\" equally well: {where})"}


def _code_tokens(line: str) -> List[str]:
    """A writer line as identifiers, with syntax and per-language glue dropped: `"host": host,` in Swift and
    `("host", text(host)),` in Rust are the same tokens."""
    toks = [t.lower().replace("_", "") for t in re.findall(r"[A-Za-z_]\w*|\d+", line)]
    return [t for t in toks if t not in _GLUE]


def blame(root: Path, path: str, line: int, rev: Optional[str]) -> str:
    """`git blame -w -M -C` of one line: the day (in the author's own time zone), short commit, author and subject;
    read-only."""
    cmd = ["git", "-C", str(root), "blame", "-w", "-M", "-C", "--porcelain", "-L", f"{line},{line}"]
    cmd += ([rev] if rev else []) + ["--", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "blame unavailable"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")
        if "not a git repository" in err:
            return "no git history (not a git repository)"
        return "not in git history (the file is not committed)" if "no such path" in err else "blame unavailable"
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    if not lines:
        return "blame unavailable"
    sha = lines[0].split()[0]
    info = dict(ln.split(" ", 1) for ln in lines[1:] if " " in ln and not ln.startswith("\t"))
    if set(sha) == {"0"}:
        return "not committed yet"
    try:
        tz = info.get("author-tz", "+0000")
        sign = -1 if tz.startswith("-") else 1
        offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        day = datetime.fromtimestamp(int(info.get("author-time", "0")), timezone(offset)).strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        day = "?"
    return f"{day} {sha[:8]} {info.get('author', '?')}: {info.get('summary', '')}".strip()


def _word(term: str) -> re.Pattern:
    return re.compile(r"(?i)(?<![\w])" + re.escape(term) + r"(?![\w])")


def documented(index: DocIndex, row: Row, group: Optional[GroupSpec] = None, limit: int = 2) -> List[str]:
    """Citations of comment / *.md lines that say this difference is intended; nothing weaker counts, since a false
    "documented" would let --strict pass over a real difference.

    Only key differences (value, absent) can be documented this way; a reject, error, judge or order row only by
    twins-accepted.json. A line counts when it is a comment in one of the group's copy files or a *.md line, and
    - carries `twins: <copy> omits|differs at <path>` for a copy of the group and this path or key, or
    - says the difference is meant (INTENT on the line or in its comment block) and does not call it a problem
      (TODO, FIXME, bug, should, must ...: DOUBT), names the key (the dotted path, or the leaf key in backticks or
      quotes), in a *.md file also names a copy of the group, and for a value row shows a differing value (a
      number or boolean right after the key: `buffer_ms: 800`).
    """
    key = row.leaf_key
    if row.kind not in ("value", "absent") or not key or group is None:
        return []
    copy_files = {p for c in group.copies for p in c.files}
    names = sorted({n for c in group.copies for n in (c.name, c.language) if n}, key=len, reverse=True)
    parts = re.sub(r"\[[^\]]*\]", "", row.path).split(".")
    dotted = [".".join(parts[k:]) for k in range(len(parts) - 1)]  # rules.peer, playback.rules.peer
    quoted = re.compile(r"[`\"']" + re.escape(key) + r"[`\"']")
    values: List[str] = []
    scalars: List[str] = []
    if row.kind == "value":
        for v in row.values.values():
            if isinstance(v, (str, list)):
                values += [s for s in _value_strings(v) if len(s) >= 2]
            elif v is not ABSENT and not isinstance(v, dict):
                scalars.append(canonical(v))
    hits: List[int] = []
    for term in sorted(set(dotted + [key])):
        for k in index.find(term):
            p, ln, text = index.lines[k]
            md = p.lower().endswith(".md")
            if not md and p not in copy_files:
                continue  # a comment in some other file is about that file
            m = MARKER.search(text)
            if m and m.group(1) in {c.name for c in group.copies} and m.group(2) in (row.npath, row.path, key):
                hits.append(k)
                continue
            if DOUBT.search(text) or not (INTENT.search(text) or (not md and INTENT.search(index.block(k)))):
                continue
            if not (any(_word(d).search(text) for d in dotted) or quoted.search(text)):
                continue
            if md and not any(_word(n).search(text) for n in names):
                continue
            if row.kind == "value" and not (any(v in text for v in values) or any(
                    re.search(re.escape(key) + r"[`\"']?\s*(?:[:=]|to|is|of)\s*[`\"']?" + re.escape(s) + r"(?![\w.])",
                              text) for s in scalars)):
                continue
            hits.append(k)
    out = []
    for k in sorted(set(hits))[:limit]:
        p, ln, text = index.lines[k]
        out.append(f"{p}:{ln}: {text.strip()[:140]}")
    if len(set(hits)) > limit:
        out.append(f"(and {len(set(hits)) - limit} more)")
    return out


def comments_near(files: RepoFiles, path: str, line: int, span: int = NEAR_WRITER_LINES, limit: int = 2) -> List[str]:
    """Comment lines just above a writer line, shown as data next to a row (they explain nothing by themselves)."""
    lx = files.lexed(path)
    found = [(ln, c.strip()) for ln, c in (lx.comments if lx else []) if line - span <= ln <= line and
             re.search(r"\w{3}", c)]
    return [f"{path}:{ln}: {c[:140]}" for ln, c in found[-limit:]]


# ---------------------------------------------------------------------------------------------------------- driver

@dataclass
class GroupResult:
    group: GroupSpec
    inputs: List[Input]
    skipped_inputs: List[Tuple[Input, str]]
    runs: List[CopyRun]
    rows: List[Row]
    stats: dict
    judge_note: str = ""


def run_group(group: GroupSpec, files: RepoFiles, cfg: Config, root: Path, work: Path,
              index_box: List[Optional[DocIndex]], tmp: Optional[Path] = None) -> GroupResult:
    inputs, skipped = harvest(files, group)
    env = dict(os.environ, PMAP_TWINS_REPO=str(root), PMAP_TWINS_CONFIG_DIR=str(cfg.path.parent))
    subst = {"repo": str(root), "config_dir": str(cfg.path.parent)}
    runs = []
    for j, c in enumerate(group.copies):
        cwork = work / f"{j:02d}-{_slug(c.name)}"
        scrub = scrubber({"{work}": cwork, "{repo}": root, "{config_dir}": cfg.path.parent, "{tmp}": tmp or work})
        runs.append(run_copy(c, inputs, cwork, env, subst, scrub))
    judge = None
    if group.judge:
        jwork = work / "_judge"
        judge = Judge(group.judge, jwork, env, subst, group.timeout,
                      scrubber({"{work}": jwork, "{repo}": root, "{config_dir}": cfg.path.parent,
                                "{tmp}": tmp or work}))
    rows, stats = compare(group, inputs, runs, judge)
    note = f"judge did not run: {judge.broken}" if judge is not None and judge.broken else ""
    if rows and index_box[0] is None:
        index_box[0] = DocIndex(files)
    ran = {r.copy.name for r in runs if r.executed}
    for row in rows:
        row.accepted = cfg.accepted.get(row.fingerprint)
        row.documented = documented(index_box[0], row, group) if index_box[0] else []
        key = row.leaf_key
        if row.kind not in ("value", "absent") or not key:
            continue
        for c in group.copies:
            if not c.files:
                continue
            files.read_many(c.files)
            w = find_writer(files, c.files, key, row.values.get(c.name, ABSENT), row.parents.get(c.name))
            if w["found"]:
                w["blame"] = blame(root, w["path"], w["line"], cfg.rev)
                w["comments_near"] = comments_near(files, w["path"], w["line"])
            w["ran"] = c.name in ran
            row.writers[c.name] = w
        row.same_writer = _same_writer(row)
    return GroupResult(group, inputs, skipped, runs, rows, stats, note)


def _same_writer(row: Row) -> str:
    """When copies on different sides of a value row write the key with the same line, the difference is made
    before that line: say so instead of pointing at the line as its cause."""
    if row.kind != "value" or len(row.partition) < 2:
        return ""
    sides = [[row.writers.get(n) for n in side if (row.writers.get(n) or {}).get("found")] for side in row.partition]
    toks = [{" ".join(_code_tokens(w["text"])) for w in side} for side in sides if side]
    if len(toks) >= 2 and all(len(t) == 1 for t in toks) and len(set.union(*toks)) == 1 and next(iter(toks[0])):
        text = next(w["text"] for side in sides for w in side)
        return (f"the copies on both sides write this key the same way (`{text[:80]}`); the values differ before "
                "this line, and pmap does not trace where")
    return ""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "x"


def execute(root: Path, cfg: Config) -> List[GroupResult]:
    files = RepoFiles(root, cfg.rev)
    work = Path(tempfile.mkdtemp(prefix="pmap-twins-"))
    index_box: List[Optional[DocIndex]] = [None]
    try:
        return [run_group(g, files, cfg, root, work / f"{k:02d}-{_slug(g.name)}", index_box, work)
                for k, g in enumerate(cfg.groups)]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _status(row: Row) -> str:
    """accepted (in twins-accepted.json), documented (an intent comment), or undocumented (no explanation found)."""
    if row.accepted is not None:
        return "accepted"
    return "documented" if row.documented else "undocumented"


def _label(row: Row) -> str:
    if row.accepted is not None:
        return "known" + (f": {row.accepted}" if row.accepted else ": listed in twins-accepted.json")
    return "documented" if row.documented else "no explanation found"


def _headline(row: Row) -> str:
    who = " vs ".join("+".join(c) for c in row.partition)
    if row.kind == "reject":
        acc = [n for n, v in row.values.items() if v.startswith(PRODUCED)]
        rej = [n for n, v in row.values.items() if v.startswith(REJECTED)]
        return f"input: {', '.join(acc)} produced output, {', '.join(rej)} returned the reject marker"
    if row.kind == "judge":
        acc = [n for n, v in row.values.items() if v == "accepted"]
        ref = [n for n, v in row.values.items() if v != "accepted"]
        return f"the judge accepted the output of {', '.join(acc)} and refused the output of {', '.join(ref)}"
    if row.kind == "order":
        return (f"{row.path[:-len(ORDER)]}: the same elements in a different order "
                f"({who}; a list gives the base copy's indexes in that copy's order)")
    if row.kind == "error":
        return f"failed in {', '.join(n for n, v in row.values.items() if str(v).startswith('error'))}"
    return f"{row.path}  ({'different values' if row.kind == 'value' else 'present in one, absent in another'}: {who})"


def _agree_split(stats: dict, judged: bool) -> str:
    s = stats["agree_split"]
    parts = []
    if judged:
        parts += [f"{s['judge_accepts']} identical outputs the judge accepts",
                  f"{s['judge_refuses']} identical outputs the judge refuses"]
        if s["not_judged"]:
            parts.append(f"{s['not_judged']} identical outputs not judged")
    else:
        parts.append(f"{s['not_judged']} identical outputs (no judge)")
    parts.append(f"{s['all_rejected']} rejected by every copy")
    return ", ".join(parts)


def _input_sets(rows: List[Row]) -> int:
    return len({tuple(sorted(i.text for i in r.inputs)) for r in rows})


def to_json(results: List[GroupResult], cfg: Config, root: Path) -> dict:
    seen = {r.fingerprint for g in results for r in g.rows}
    groups = []
    for g in results:
        ran = [r.copy.name for r in g.runs if r.executed]
        groups.append({
            "name": g.group.name,
            "inputs": {"executed": len(g.inputs), "from_tests": sum(1 for i in g.inputs if not i.synthetic),
                       "synthetic": sum(1 for i in g.inputs if i.synthetic),
                       "not_executed": [{"input": i.text, "source": i.origin(), "sources": i.sources, "reason": why}
                                        for i, why in g.skipped_inputs]},
            "copies": [{"name": r.copy.name, "language": r.copy.language, "executed": r.executed, "note": r.note}
                       for r in g.runs],
            "compared": len(ran) >= 2,
            "reference": g.stats.get("reference"),
            "agree": g.stats["agree"], "total": g.stats["inputs"], "agree_split": dict(g.stats["agree_split"]),
            "agree_with_reference": {n: {"agree": a, "of": t} for n, (a, t) in sorted(g.stats["pairs"].items())},
            "judge": {n: {"accepted": a, "of": t} for n, (a, t) in sorted(g.stats["judge"].items())},
            "judge_refusals": [{"copy": v["copy"], "line": v["line"], "count": len(v["inputs"]),
                                "first_input": v["inputs"][0].text, "source": v["inputs"][0].origin()}
                               for _, v in sorted(g.stats["judge_refusals"].items())],
            "not_judged": {n: {"count": v["count"], "why": v["why"]} for n, v in sorted(g.stats["not_judged"].items())},
            "judge_note": g.judge_note,
            "input_sets": _input_sets(g.rows),
            "rows": [{
                "fingerprint": r.fingerprint, "kind": r.kind, "path": r.path, "normalised_path": r.npath,
                "partition": r.partition, "count": r.count, "status": _status(r),
                "accepted_note": r.accepted, "documented": r.documented,
                "first_input": r.inputs[0].text, "first_source": r.inputs[0].origin(),
                "synthetic": r.inputs[0].synthetic,
                "values": {n: (None if v is ABSENT else v) for n, v in r.values.items()},
                "absent_in": [n for n, v in r.values.items() if v is ABSENT],
                "inputs": [i.text for i in r.inputs], "writers": r.writers, "same_writer": r.same_writer,
            } for r in g.rows],
        })
    return {"config": str(cfg.path), "repo": str(root), "rev": cfg.rev, "groups": groups,
            "accepted_not_seen": sorted(set(cfg.accepted) - seen)}


def render_text(results: List[GroupResult], cfg: Config, root: Path, md: bool = False) -> str:
    out: List[str] = []
    h1, h2, li, sub = ("# ", "## ", "- ", "  - ") if md else ("", "", "  ", "    ")
    q = (lambda s: f"`{s}`") if md else (lambda s: s)  # noqa: E731
    out.append(f"{h1}twins --exec: {cfg.path}" + (f" (files at {cfg.rev})" if cfg.rev else ""))
    for g in results:
        out.append("")
        out.append(f"{h2}{g.group.name}")
        n_t = sum(1 for i in g.inputs if not i.synthetic)
        n_s = len(g.inputs) - n_t
        out.append(f"{li}inputs: {n_t} from tests, {n_s} synthetic")
        for r in g.runs:
            if r.executed:
                out.append(f"{li}ran {r.copy.name}" + (f" ({r.copy.language})" if r.copy.language else "")
                           + (f": {r.note}" if r.note else ""))
        not_run = [r for r in g.runs if not r.executed]
        if not_run or g.skipped_inputs or g.judge_note or g.stats["not_judged"]:
            out.append(f"{li}NOT executed:")
            for r in not_run:
                out.append(f"{sub}{r.copy.name}" + (f" ({r.copy.language})" if r.copy.language else "") + f": {r.note}")
            for i, why in g.skipped_inputs:
                more = f" and {len(i.sources) - 1} more" if len(i.sources) > 1 else ""
                what = "test literal" if why.startswith("value not fixed") else "input"  # not this text: its value
                out.append(f"{sub}{what} {q(_short(i.text))} ({i.origin()}{more}): {why}")
            if g.judge_note:
                out.append(f"{sub}{g.judge_note}")
            for n, v in sorted(g.stats["not_judged"].items()):
                out.append(f"{sub}the judge did not judge {v['count']} output(s) of {n}: {v['why']}")
        ran = [r for r in g.runs if r.executed]
        if len(ran) >= 2:
            ref = g.stats["reference"]
            out.append(f"{li}agree on {g.stats['agree']} of {g.stats['inputs']} inputs: "
                       f"{_agree_split(g.stats, g.group.judge is not None and not g.judge_note)}")
            out.append(f"{li}compared against {ref} (the base chosen in the config; pmap does not assume it is right)"
                       if g.group.reference else f"{li}compared against {ref} (the first copy that ran; pmap does not "
                                                 "assume it is right)")
            for n, (a, t) in sorted(g.stats["pairs"].items()):
                out.append(f"{sub}{n} vs {ref}: same on {a} of {t}")
        for n, (a, t) in sorted(g.stats["judge"].items()):
            out.append(f"{li}the judge accepted {a} of {t} outputs of {n}")
        for _, v in sorted(g.stats["judge_refusals"].items()):
            i0 = v["inputs"][0]
            out.append(f"{sub}the judge refused {v['copy']} on {len(v['inputs'])} input(s), e.g. {q(_short(i0.text))} "
                       f"({i0.origin()}): {q(v['line'])}")
        out.append("")
        if len(ran) < 2:
            names = ", ".join(r.copy.name for r in ran) or "no copy"
            out.append(f"{li}differences: not compared (only {names} ran)")
            continue
        und = sum(1 for r in g.rows if _status(r) == "undocumented")
        out.append(f"{li}differences: {len(g.rows)} rows over {_input_sets(g.rows)} distinct input set(s) "
                   f"({und} with no explanation found)" if g.rows else f"{li}differences: none")
        for k, r in enumerate(g.rows, 1):
            out.extend(_render_row(k, r, md, q))
    stale = sorted(set(cfg.accepted) - {r.fingerprint for g in results for r in g.rows})
    if stale:
        out.append("")
        out.append(f"accepted but not seen this run: {', '.join(stale)}")
    return "\n".join(out) + "\n"


def _render_row(k: int, r: Row, md: bool, q: Callable[[str], str]) -> List[str]:
    pad = "- " if md else "     "
    out = [f"{'### ' if md else ''}{k}. {_headline(r)}  [{_label(r)}]  {r.count} input(s)"]
    i0 = r.inputs[0]
    out.append(f"{pad}first input: {q(_short(i0.text))} ({i0.origin()})")
    if r.kind in ("value", "absent", "error", "judge", "reject"):
        for n, v in r.values.items():
            shown = _show(v) if r.kind in ("value", "absent") else str(v)
            out.append(f"{pad}{n} (first input): {q(shown)}")
    for n, w in sorted(r.writers.items(), key=lambda kv: not kv[1].get("ran")):
        label = n if w.get("ran") else f"{n} (not run; its value is unknown)"
        if w.get("found"):
            out.append(f"{pad}writer in {label}: {w['path']}:{w['line']} ({w['how']}; line last changed: "
                       f"{w['blame']})")
            for c in w.get("comments_near") or []:
                out.append(f"{pad}  comment near it: {c}")
        else:
            out.append(f"{pad}writer in {label}: {w['note']}")
    if r.same_writer:
        out.append(f"{pad}{r.same_writer}")
    for d in r.documented:
        out.append(f"{pad}documented at {d}")
    out.append(f"{pad}fingerprint {r.fingerprint}")
    return out


def _short(s: str, n: int = 100) -> str:
    s = s.replace("\n", "\\n").replace("\r", "\\r")
    return s if len(s) <= n else s[:n - 1] + "…"


def strict_failures(results: List[GroupResult]) -> List[str]:
    """Why --strict fails: rows with no explanation that are not accepted, and every way a group compared less
    than it says (no input ran, fewer than two copies ran, a copy or the judge did not run)."""
    why = []
    for g in results:
        n = sum(1 for r in g.rows if _status(r) == "undocumented")
        if n:
            why.append(f"{g.group.name}: {n} undocumented difference(s)")
        for r in g.runs:
            if r.copy.run is not None and not r.executed:
                why.append(f"{g.group.name}: {r.copy.name} did not run ({r.note})")
        ran = [r for r in g.runs if r.executed]
        if not g.inputs:
            why.append(f"{g.group.name}: no input executed (inputs.from_tests matched nothing that can run)")
        if len(ran) < 2:
            why.append(f"{g.group.name}: fewer than two copies ran, nothing was compared")
        if g.judge_note:
            why.append(f"{g.group.name}: {g.judge_note}")
        for n, v in sorted(g.stats["not_judged"].items()):
            why.append(f"{g.group.name}: the judge did not judge {v['count']} output(s) of {n}")
    return why


def run_cli(args: Any) -> int:
    """Entry point for `pmap twins --exec` (args: repo, config, json, md, strict)."""
    root = Path(getattr(args, "repo", None) or ".").expanduser().resolve()
    try:
        cfg = load_config(find_config(root, getattr(args, "config", None)))
        results = execute(root, cfg)
    except (ConfigError, GitError) as exc:
        print(f"pmap twins --exec: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(to_json(results, cfg, root), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_text(results, cfg, root, md=bool(getattr(args, "md", False))))
    if getattr(args, "strict", False):
        why = strict_failures(results)
        for w in why:
            print(f"strict: {w}", file=sys.stderr)
        return 1 if why else 0
    return 0
