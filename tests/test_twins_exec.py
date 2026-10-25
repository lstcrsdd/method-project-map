"""`pmap twins --exec` (pmap/twins_exec.py, pmap/twins_harvest.py): copies run on the same inputs and compared.

The portable tests build a small repository in a temp dir: three Python copies of a `play://host:port?mode=x`
link -> audio player config builder and a Python judge. Known differences, each deliberate:

- copy `b` keeps the brackets of an IPv6 host (the judge refuses its output), accepts port 999999, lists the two
  rules in reverse order, never writes `buffer_ms` (its own docstring says that is intended) and defaults `mode`
  to "fast" (docs/NOTES.md says that is intended);
- copy `c` crashes on `crash.example` and prints something that is not JSON for `garbage.example`.

Comments that name a key without saying the difference is intended (a TODO, a note in a file that is not a copy,
a line saying an input "must be rejected") document nothing. The inputs come from test files in Python, Swift, Java
and a Rust `#[cfg(test)]` module, plus one synthetic input. The last test compiles the Swift and Rust copies of
tests/fixtures/twins when swiftc and rustc exist.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from pmap import twins_exec as tx
from pmap.twins_harvest import DocIndex, RepoFiles, is_test_path, lex, test_literal_records, test_literals, unescape

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "twins"
PY = sys.executable
GIT = shutil.which("git")

COPY_A = '''\
"""Reference copy: a play link -> a player config."""
import json
import sys


def build(link):
    if not link.startswith("play://"):
        raise ValueError(link)
    rest, _, query = link[len("play://"):].partition("?")
    host, _, port = rest.rpartition(":")
    host = host.strip("[]")  # `server` is the host name
    port = int(port)
    if not 0 < port <= 65535:
        raise ValueError(port)
    q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
    return {
        "server": host,
        "port": port,
        "buffer_ms": 800,
        "mode": q.get("mode", "auto"),
        "rules": [{"ip": host, "action": "cache"}, {"domain": "example.com", "action": "stream"}],
    }


for line in sys.stdin:
    try:
        print(json.dumps(build(line.strip())))
    except ValueError:
        print("ERROR")
'''

COPY_B = '''\
"""Mobile copy. Intended: this copy never writes `buffer_ms` (the phone's player sets it)."""
import json
import sys


def build(link):
    if not link.startswith("play://"):
        raise ValueError(link)
    rest, _, query = link[len("play://"):].partition("?")
    host, _, port = rest.rpartition(":")
    q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
    rules = [{"domain": "example.com", "action": "stream"}, {"ip": host, "action": "cache"}]
    # TODO: strip the brackets of `server` on purpose like the desktop copy (bug #12)
    return {
        "server": host,
        "port": int(port),
        "mode": q.get("mode", "fast"),
        "rules": rules,
    }


for line in sys.stdin:
    try:
        print(json.dumps(build(line.strip())))
    except ValueError:
        print("ERROR")
'''

COPY_C = COPY_A.replace('"""Reference copy', '"""Desktop copy').replace(
    "    try:\n        print(json.dumps(build(line.strip())))",
    "    if 'crash.example' in line:\n        raise RuntimeError('boom')\n"
    "    if 'garbage.example' in line:\n        print('<html>')\n        continue\n"
    "    try:\n        print(json.dumps(build(line.strip())))")

JUDGE = '''\
import json
import sys

cfg = json.load(open(sys.argv[1]))
if "[" in cfg["server"]:
    sys.exit("FATAL: bad address " + cfg["server"] + " in " + sys.argv[1])
if cfg["port"] > 65535:
    sys.exit("FATAL: port out of range")
'''

TEST_PY = '''\
LINKS = [
    "play://1.2.3.4:443?mode=x",
    "play://[2001:db8::1]:443?mode=x",
    "play://h.example:999999?mode=x",
    "play://h.example:80",
    "play://crash.example:1?mode=x",
    "play://garbage.example:1?mode=x",
    "play://h.example:1?mode=x\\n",
]
DUP = "play://1.2.3.4:443?mode=x"
name = "x"
NOT_FIXED = f"play://{name}:1"
BODY = "play://multi.example:1?mode=m\\nplay://multi.example:2?mode=m\\n"
TEMPLATE = "play://{}:7".format(name)
'''

TEST_SWIFT = '''\
import XCTest
final class LinkTests: XCTestCase {
    func testLinks() {
        let escaped = "play://h.example:8\\u{30}?mode=y"  // play://h.example:80?mode=y
        let host = "x"
        let interpolated = "play://\\(host):1"
        let joined = "play://joined.example:" +
            "5?mode=j"
        let piece = "play://piece.example:6" + host
    }
}
'''

TEST_JAVA = '''\
class LinkTest {
    String broken = "play://h.example:9#\\uD83D";
}
'''

LIB_RS = '''\
pub fn f() -> &'static str { "play://not.a.test:1" }

#[cfg(test)]
mod tests {
    const L: &str = "play://rust.example:443?mode=r";
    fn t(host: &str) { let _ = format!("play://{host}:1"); }
}
'''

NOTES = ('# Notes\n\nIntended: copy `b` defaults `mode` to "fast" when the link has none.\n'
         '- play://h.example:999999?mode=x must be rejected by every copy\n')
OTHER = '# Intended: `server` keeps its brackets on purpose in copy b\nX = 1\n'


def make_repo(root: Path, git: bool = True) -> None:
    files = {"a.py": COPY_A, "b.py": COPY_B, "c.py": COPY_C, "judge.py": JUDGE, "tests/test_links.py": TEST_PY,
             "tests/LinkTests.swift": TEST_SWIFT, "tests/LinkTest.java": TEST_JAVA, "src/lib.rs": LIB_RS,
             "src/app.py": 'URL = "play://app.example:1"\n', "src/other.py": OTHER, "docs/NOTES.md": NOTES}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    if git and GIT:
        # 01:30 in UTC+3 is the evening before in UTC: the blame day must be the author's own
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T01:30:00+03:00", GIT_COMMITTER_DATE="2026-01-02T01:30:00+03:00")
        who = ["-c", "user.name=Tester", "-c", "user.email=t@example.com"]
        for cmd in (["init", "-q"], ["add", "-A"], who + ["commit", "-q", "-m", "initial copies"]):
            subprocess.run(["git", "-C", str(root)] + cmd, check=True, env=env, capture_output=True)


def demo_config(**over) -> dict:
    group = {
        "name": "play link -> config",
        "inputs": {"from_tests": r"(?s)play://.*", "extra": ["play://synthetic.example:2?mode=s"]},
        "reference": "a",
        "judge": [PY, "{repo}/judge.py", "{file}"],
        "copies": [
            {"name": "a", "language": "Python", "files": ["a.py"], "run": [PY, "{repo}/a.py"]},
            {"name": "b", "language": "Python", "files": ["b.py"], "run": [PY, "{repo}/b.py"]},
            {"name": "c", "language": "Python", "files": ["c.py"], "run": [PY, "{repo}/c.py"]},
        ],
    }
    group.update(over)
    return {"groups": [group]}


def run(repo: Path, config: Path, **flags) -> tuple:
    args = argparse.Namespace(repo=str(repo), config=str(config), json=flags.get("json", False),
                              md=flags.get("md", False), strict=flags.get("strict", False))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tx.run_cli(args)
    return code, out.getvalue(), err.getvalue()


class TestLexing(unittest.TestCase):
    def test_literals_per_language(self) -> None:
        cases = {
            "swift": ('let a = "x\\u{41}"; let b = "\\(v)"; let c = #"r\\n"#', ["xA", None, "r\\n"]),
            "rust": ('fn f<\'a>() { let s = "a\\x41"; let r = r#"q"x"#; let c = \'"\'; }', ["aA", 'q"x', '"']),
            "csharp": ('var a = @"C:\\x""q"""; var b = $"{x} y"; var c = "\\u0041";', ['C:\\x"q"', None, "A"]),
            "java": ('String a = "\\uD83D\\uDE00"; String b = "\\101";', ["\U0001F600", "A"]),
            "kotlin": ('val a = "x$y"; val b = "\\$z"; val c = """raw\\n"""', [None, "$z", "raw\\n"]),
            "python": ('a = f"{x}"\nb = r"\\d"\nc = "\\u00e9"', [None, "\\d", "é"]),
            "go": ('a := `raw\\n`; b := "x\\ty"', ["raw\\n", "x\ty"]),
            "js": ("const a = `t ${x}`; const b = 'q\\'s'", [None, "q's"]),
            "shell": ('echo "$HOME" \'lit # x\' # comment', [None, "lit # x"]),
        }
        for lang, (src, want) in cases.items():
            with self.subTest(lang=lang):
                self.assertEqual([lit.value for lit in lex(src, lang).literals], want)

    def test_concatenations_templates_and_pieces(self) -> None:
        cases = {
            "csharp": ('var a = "play://h:" +\n    "1?x=y"; var b = "play://p" + Host;', ["play://h:1?x=y", None]),
            "java": ('String a = "a" + /* c */ "b";', ["ab"]),
            "c": ('const char *a = "play://" "h:1";', ["play://h:1"]),
            "python": ('A = ("a"\n     "b")\nB = "c"\nD = "d"', ["ab", "c", "d"]),
            "rust": ('let a = format!("play://{host}:1"); let b = format!("{}", 1); let c = "x{y}";',
                     [None, None, "x{y}"]),
        }
        for lang, (src, want) in cases.items():
            with self.subTest(lang=lang):
                self.assertEqual([lit.value for lit in lex(src, lang).literals], want)
        whys = [lit.why for lit in lex('let a = "play://p" + host; let t = format!("play://{h}");', "rust").literals]
        self.assertEqual(whys, ["part of a concatenated string", "a format template"])

    def test_comments_and_code_blanking(self) -> None:
        lx = lex('let s = "// not a comment" // real\n/* a /* nested */ b */ let t = 1', "swift")
        self.assertEqual([c for _, c in lx.comments], ["// real", "/* a /* nested */ b */"])
        self.assertNotIn("not a comment", lx.code)
        self.assertIn("let t = 1", lx.code)

    def test_unescape_keeps_unknown_escapes(self) -> None:
        self.assertEqual(unescape("a\\qb", "rust"), "a\\qb")
        self.assertEqual(unescape("\\101", "python"), "A")
        self.assertEqual(unescape("\\0", "swift"), "\0")

    def test_test_paths(self) -> None:
        for p in ("Windows/Player.Core.Tests/StreamLinkParserTests.cs", "MacOS/Scripts/fuzz_test.sh", "tests/x.py",
                  "pkg/foo_test.go", "app/src/test/java/X.java", "web/a.spec.ts", "AppTests/Foo.swift"):
            self.assertTrue(is_test_path(p), p)
        for p in ("MacOS/Player/StreamInspector.swift", "src/latest/a.py", "src/Contest.java"):
            self.assertFalse(is_test_path(p), p)
        self.assertTrue(is_test_path("qa/links.txt.py", ["qa/*"]))
        self.assertFalse(is_test_path("tests/x.py", ["qa/*"]))

    def test_doc_index_lines_survive_lowercasing_that_lengthens(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "NOTES.md").write_text("one\nline two\n" + "İ" * 41 + "\nfour\nunrelated line two\nsix\n",
                                          encoding="utf-8")
            index = DocIndex(RepoFiles(tmp))
            hits = [index.lines[k][:2] for k in index.find("unrelated line two")]
            self.assertEqual(hits, [("NOTES.md", 5)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCompare(unittest.TestCase):
    def test_align_by_content_not_position(self) -> None:
        a = [{"kind": "cache", "tag": "d"}, {"kind": "stream", "tag": "p", "server": "x"}]
        b = [{"kind": "stream", "tag": "p", "server": "y"}, {"kind": "cache", "tag": "d"}, {"kind": "radio"}]
        self.assertEqual(tx.align(a, b), [(0, 1), (1, 0), (None, 2)])

    def test_walk_reports_order_values_absent_and_extras(self) -> None:
        a = {"rules": [{"ip": "1", "action": "cache"}, {"domain": "d", "action": "stream"}], "buffer_ms": 1}
        b = {"rules": [{"domain": "d", "action": "stream"}, {"ip": "2", "action": "cache", "x": 1}, {"other": True}]}
        c = {"rules": [{"ip": "1", "action": "cache"}, {"domain": "d", "action": "stream"}], "buffer_ms": {"v": 2}}
        leaves = tx.walk({"a": a, "b": b, "c": c}, "a")
        _, vals, _ = leaves[("buffer_ms", "absent")]
        self.assertEqual((vals["b"], vals["c"]), (tx.ABSENT, {"v": 2}))
        self.assertEqual(leaves[("buffer_ms", "value")][1], {"a": 1, "c": {"v": 2}})  # and among those that have it
        self.assertEqual(leaves[("rules[0].ip", "value")][:2], ("rules[].ip", {"a": "1", "b": "2", "c": "1"}))
        self.assertEqual(leaves[("rules[0].x", "absent")][1], {"a": tx.ABSENT, "b": 1, "c": tx.ABSENT})
        self.assertEqual(leaves[("rules[+0]", "absent")][1], {"a": tx.ABSENT, "b": {"other": True}, "c": tx.ABSENT})
        self.assertEqual(leaves[("rules{order}", "order")][1], {"a": tx.IN_ORDER, "b": [1, 0], "c": tx.IN_ORDER})
        # where one copy lacks a subtree, the walk goes on among the copies that have it
        deep = tx.walk({"a": {"o": {"k": 1, "p": "x"}}, "b": {"o": {"k": 2, "p": "x"}}, "c": {}}, "a")
        self.assertIn(("o", "absent"), deep)
        self.assertEqual(deep[("o.k", "value")][1], {"a": 1, "b": 2})

    def test_one_input_counts_once_in_a_row(self) -> None:
        group = tx.GroupSpec("g", [tx.CopySpec("a"), tx.CopySpec("b")])
        a = {"rules": [{"ip": "1", "n": 1}, {"ip": "2", "n": 2}]}
        b = {"rules": [{"ip": "[1]", "n": 1}, {"ip": "[2]", "n": 2}]}
        runs = [tx.CopyRun(group.copies[0], True, "", [tx.Outcome("ok", a, tx.canonical(a))]),
                tx.CopyRun(group.copies[1], True, "", [tx.Outcome("ok", b, tx.canonical(b))])]
        rows, _stats = tx.compare(group, [tx.Input("only.one.input")], runs, None)
        self.assertEqual([(r.npath, r.count) for r in rows], [("rules[].ip", 1)])

    def test_placeholders_are_expanded_once(self) -> None:
        cmd, shell = tx._expand("ls {repo}", {"repo": "/tmp/r{config_dir}", "config_dir": "/tmp/c;echo INJECTED;"})
        self.assertTrue(shell)
        self.assertEqual(cmd, "ls " + shlex.quote("/tmp/r{config_dir}"))
        args, _ = tx._expand(["tool", "{repo}", "{file}"], {"repo": "/tmp/r{work}", "work": "/w"})
        self.assertEqual(args, ["tool", "/tmp/r{work}", "{file}"])

    def test_scrubber_replaces_the_runs_own_directories(self) -> None:
        scrub = tx.scrubber({"{work}": Path("/tmp/pmap-twins-x/00-g/01-b"), "{tmp}": Path("/tmp/pmap-twins-x")})
        self.assertEqual(scrub("fatal at /tmp/pmap-twins-x/00-g/01-b/b.py and /tmp/pmap-twins-x/y"),
                         "fatal at {work}/b.py and {tmp}/y")


class DemoCase(unittest.TestCase):
    """One run of the demo repository, shared by the tests that read its result."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="pmap-twins-test-"))
        cls.repo = cls.tmp / "repo"
        cls.repo.mkdir()
        make_repo(cls.repo)
        cls.cfgdir = cls.tmp / "outside"  # the config lives outside the repository
        cls.cfgdir.mkdir()
        cls.config = cls.cfgdir / "twins.json"
        cls.config.write_text(json.dumps(demo_config()), encoding="utf-8")
        code, out, _ = run(cls.repo, cls.config, json=True)
        assert code == 0, out
        cls.data = json.loads(out)
        cls.group = cls.data["groups"][0]
        cls.rows = cls.group["rows"]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def row(self, kind: str, path: str = "", n: int = 1) -> dict:
        found = [r for r in self.rows if r["kind"] == kind and (not path or r["path"] == path)]
        self.assertEqual(len(found), n, f"{kind} {path}: {[(r['kind'], r['path']) for r in self.rows]}")
        return found[0]


class TestHarvest(DemoCase):
    def test_inputs_from_tests_of_every_language(self) -> None:
        values = {v for _, _, v in test_literals(RepoFiles(self.repo))}
        self.assertIn("play://h.example:80?mode=y", values)  # Swift \u{30} unescaped
        self.assertIn("play://rust.example:443?mode=r", values)  # Rust #[cfg(test)] module
        self.assertIn("play://joined.example:5?mode=j", values)  # "a" + "b" joined
        self.assertNotIn("play://not.a.test:1", values)  # Rust code outside the test module
        self.assertNotIn("play://app.example:1", values)  # not a test file
        self.assertNotIn("play://piece.example:6", values)  # a piece of a concatenation with a variable
        self.assertFalse(any(v and "{" in v for v in values))  # interpolated or a template: skipped

    def test_inputs_deduplicated_sorted_with_sources(self) -> None:
        group = tx.load_config(self.config).groups[0]
        inputs = tx.harvest_inputs(RepoFiles(self.repo), group)
        texts = [i.text for i in inputs]
        self.assertEqual(texts, sorted(texts))
        dup = next(i for i in inputs if i.text == "play://1.2.3.4:443?mode=x")
        self.assertEqual(dup.sources, ["tests/test_links.py:2", "tests/test_links.py:10"])
        syn = next(i for i in inputs if i.text == "play://synthetic.example:2?mode=s")
        self.assertTrue(syn.synthetic)
        self.assertEqual(syn.origin(), "synthetic input from the config")

    def test_line_breaks_trailing_and_inner(self) -> None:
        group = tx.load_config(self.config).groups[0]
        inputs = {i.text: i.sources for i in tx.harvest_inputs(RepoFiles(self.repo), group)}
        self.assertEqual(inputs["play://h.example:1?mode=x"], ["tests/test_links.py:8"])  # the trailing \n dropped
        self.assertEqual(inputs["play://multi.example:1?mode=m"], ["tests/test_links.py:13"])  # one input per line
        self.assertEqual(inputs["play://multi.example:2?mode=m"], ["tests/test_links.py:13"])

    def test_inputs_not_executed_say_why(self) -> None:
        skipped = {(s["input"], s["reason"]) for s in self.group["inputs"]["not_executed"]}
        self.assertIn(("play://h.example:9#\ud83d", "not valid UTF-8 (a lone surrogate)"), skipped)
        self.assertIn(("play://{name}:1", "value not fixed in the source (interpolated)"), skipped)
        self.assertIn(("play://{}:7", "value not fixed in the source (a format template)"), skipped)
        self.assertIn(("play://{host}:1", "value not fixed in the source (a format template)"), skipped)
        self.assertIn(("play://piece.example:6", "value not fixed in the source (part of a concatenated string)"),
                      skipped)
        self.assertEqual(self.group["inputs"]["executed"], 13)
        self.assertEqual(self.group["inputs"]["synthetic"], 1)

    @unittest.skipUnless(GIT, "git not installed")
    def test_rev_reads_the_commit_not_the_working_tree(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            make_repo(tmp)
            with open(tmp / "tests" / "test_links.py", "a", encoding="utf-8") as f:
                f.write('NEW = "play://uncommitted.example:1"\n')
            at_head = {v for _, _, v in test_literals(RepoFiles(tmp, "HEAD"))}
            on_disk = {v for _, _, v in test_literals(RepoFiles(tmp))}
            self.assertNotIn("play://uncommitted.example:1", at_head)
            self.assertIn("play://uncommitted.example:1", on_disk)
            self.assertIn("play://rust.example:443?mode=r", at_head)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_records_keep_why(self) -> None:
        whys = {t.why for t in test_literal_records(RepoFiles(self.repo)) if t.value is None}
        self.assertTrue({"interpolated", "a format template", "part of a concatenated string"} <= whys, whys)


class TestRunAndDiff(DemoCase):
    def test_copies_ran_and_agreement(self) -> None:
        copies = {c["name"]: c for c in self.group["copies"]}
        self.assertTrue(all(c["executed"] for c in copies.values()))
        self.assertIn("one process per input", copies["c"]["note"])  # its batch run crashed
        self.assertEqual(copies["a"]["note"], "")
        self.assertEqual((self.group["agree"], self.group["total"]), (0, 13))  # b always differs
        self.assertEqual(self.group["agree_with_reference"]["c"], {"agree": 11, "of": 13})
        self.assertTrue(self.group["compared"])

    def test_reject_rows_split_by_judge_verdict(self) -> None:
        row = self.row("reject")
        self.assertEqual(row["partition"], [["a", "c"], ["b"]])
        self.assertEqual(row["first_input"], "play://h.example:999999?mode=x")
        self.assertEqual(row["first_source"], "tests/test_links.py:4")
        self.assertEqual(row["values"]["a"], "returned the reject marker")
        self.assertEqual(row["values"]["b"], "produced output; the judge refused it: FATAL: port out of range")
        self.assertEqual(row["status"], "undocumented")  # "must be rejected by every copy" explains nothing

    def test_errors_kept_apart_with_stable_words(self) -> None:
        errors = [r for r in self.rows if r["kind"] == "error"]
        self.assertEqual(sorted(r["first_input"] for r in errors),
                         ["play://crash.example:1?mode=x", "play://garbage.example:1?mode=x"])
        msgs = sorted(r["values"]["c"] for r in errors)
        self.assertTrue(msgs[0].startswith("error: no output (exit 1: RuntimeError: boom"), msgs[0])
        self.assertEqual(msgs[1], "error: not JSON: <html>")

    def test_judge_refuses_one_copy_and_accepts_another(self) -> None:
        row = self.row("judge")
        self.assertEqual(row["partition"], [["a", "c"], ["b"]])
        self.assertEqual(row["values"]["a"], "accepted")
        # verbatim first error line, the temp file path replaced by {file}
        self.assertEqual(row["values"]["b"], "refused: FATAL: bad address [2001:db8::1] in {file}")
        refusals = {(r["copy"], r["line"]) for r in self.group["judge_refusals"]}
        self.assertIn(("b", "FATAL: port out of range"), refusals)

    def test_value_rows_grouped_across_inputs(self) -> None:
        server = self.row("value", "server")
        self.assertEqual(server["values"], {"a": "2001:db8::1", "b": "[2001:db8::1]", "c": "2001:db8::1"})
        self.assertEqual(server["partition"], [["a", "c"], ["b"]])
        # a TODO in b, a plain comment in a and an intent comment in a file that is not a copy document nothing
        self.assertEqual(server["status"], "undocumented")
        ip = self.row("value", "rules[0].ip")
        self.assertEqual(ip["normalised_path"], "rules[].ip")
        self.assertEqual(ip["count"], 1)

    def test_absent_key_documented_by_its_copy(self) -> None:
        row = self.row("absent", "buffer_ms")
        self.assertEqual(row["absent_in"], ["b"])
        self.assertEqual(row["count"], 12)  # every input a and b both produced output for
        self.assertEqual(row["status"], "documented")
        self.assertTrue(row["documented"][0].startswith("b.py:1: "), row["documented"])
        self.assertIn("the key is absent from this copy's output", row["writers"]["b"]["note"])

    def test_value_documented_in_markdown(self) -> None:
        mode = self.row("value", "mode")
        self.assertEqual(mode["values"]["b"], "fast")
        self.assertEqual(mode["status"], "documented")
        self.assertTrue(mode["documented"][0].startswith("docs/NOTES.md:3: "), mode["documented"])

    def test_order_row(self) -> None:
        row = self.row("order", "rules{order}")
        self.assertEqual(row["values"]["b"], [1, 0])
        self.assertEqual(row["values"]["a"], tx.IN_ORDER)

    def test_writer_lines_and_blame(self) -> None:
        server = self.row("value", "server")
        a, b = server["writers"]["a"], server["writers"]["b"]
        self.assertEqual((a["path"], a["line"], a["how"]), ("a.py", 17, "the only line writing the key"))
        self.assertEqual((b["path"], b["line"]), ("b.py", 15))
        self.assertTrue(a["ran"])
        self.assertIn("same way", server["same_writer"])  # `"server": host,` in both
        if GIT:
            self.assertEqual(a["blame"][:10], "2026-01-02")  # the author's day, not UTC's
            self.assertTrue(a["blame"].endswith("Tester: initial copies"), a["blame"])
        self.assertIsNone(self.row("reject").get("writers", {}).get("a"))

    def test_agreement_split(self) -> None:
        split = self.group["agree_split"]
        self.assertEqual(sum(split.values()), self.group["agree"])

    def test_rows_and_fingerprints_are_deterministic(self) -> None:
        code, out, _ = run(self.repo, self.config, json=True)
        again = json.loads(out)["groups"][0]["rows"]
        self.assertEqual([r["fingerprint"] for r in again], [r["fingerprint"] for r in self.rows])
        self.assertEqual(len({r["fingerprint"] for r in self.rows}), len(self.rows))


class TestOutputAndStrict(DemoCase):
    def test_text_output(self) -> None:
        code, out, _ = run(self.repo, self.config)
        self.assertEqual(code, 0)
        self.assertIn("agree on 0 of 13 inputs: 0 identical outputs the judge accepts", out)
        self.assertIn("compared against a (the base chosen in the config; pmap does not assume it is right)", out)
        self.assertIn("the judge accepted the output of a, c and refused the output of b", out)
        self.assertIn("input: b produced output, a, c returned the reject marker", out)
        self.assertIn("writer in a: a.py:17 (the only line writing the key; line last changed: ", out)
        self.assertIn("[no explanation found]", out)
        self.assertIn("NOT executed:", out)
        self.assertIn("not valid UTF-8", out)
        self.assertIn("distinct input set(s)", out)

    def test_markdown_output(self) -> None:
        _, out, _ = run(self.repo, self.config, md=True)
        self.assertIn("## play link -> config", out)
        self.assertIn("### 1. ", out)

    def test_strict_and_accepted_list(self) -> None:
        code, _, err = run(self.repo, self.config, strict=True)
        self.assertEqual(code, 1)
        self.assertIn("undocumented difference(s)", err)
        undocumented = [r["fingerprint"] for r in self.rows if r["status"] == "undocumented"]
        accepted = self.cfgdir / "twins-accepted.json"
        try:
            accepted.write_text(json.dumps({"accepted": [{"fingerprint": f, "note": "known"} for f in undocumented]
                                            + [{"fingerprint": "000000000000"}]}), encoding="utf-8")
            code, out, _ = run(self.repo, self.config, strict=True, json=True)
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertEqual(data["accepted_not_seen"], ["000000000000"])
            self.assertTrue(all(r["status"] in ("accepted", "documented") for r in data["groups"][0]["rows"]))
            _, text, _ = run(self.repo, self.config)
            self.assertIn("[known: known]", text)
        finally:
            accepted.unlink()


class TestDocumented(unittest.TestCase):
    """What explains a difference, on hand-built rows: an intent word next to the key, in a copy file or a *.md."""

    def index(self, files: dict) -> DocIndex:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for rel, text in files.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / rel).write_text(text, encoding="utf-8")
        return DocIndex(RepoFiles(tmp))

    GROUP = tx.GroupSpec("g", [tx.CopySpec("a", "Python", ["a.py"]), tx.CopySpec("b", "Python", ["b.py"])])

    def doc(self, files: dict, row: tx.Row) -> list:
        return tx.documented(self.index(files), row, self.GROUP)

    def absent(self, path: str = "buffer_ms") -> tx.Row:
        return tx.Row("g", "absent", path, [["a"], ["b"]], npath=path, values={"a": 800, "b": tx.ABSENT})

    def test_intent_in_the_omitting_copy(self) -> None:
        self.assertTrue(self.doc({"b.py": "# Intended: this copy never writes `buffer_ms`\n"}, self.absent()))

    def test_marker(self) -> None:
        self.assertTrue(self.doc({"b.py": "# twins: b omits buffer_ms\n"}, self.absent()))

    def test_a_comment_that_only_names_the_key(self) -> None:
        self.assertFalse(self.doc({"b.py": "# `buffer_ms` is the buffer size\n"}, self.absent()))

    def test_a_todo_or_a_bug(self) -> None:
        self.assertFalse(self.doc({"b.py": "# TODO: write `buffer_ms` on purpose like a.py (bug #12)\n"},
                                  self.absent()))

    def test_another_file(self) -> None:
        self.assertFalse(self.doc({"other.py": "# Intended: b never writes `buffer_ms`\n"}, self.absent()))

    def test_markdown_needs_a_copy_name(self) -> None:
        self.assertFalse(self.doc({"NOTES.md": "Intended: nothing writes `buffer_ms` here\n"}, self.absent()))
        self.assertTrue(self.doc({"NOTES.md": "Intended: copy b never writes `buffer_ms`\n"}, self.absent()))

    def test_a_dotted_path_alone_is_not_intent(self) -> None:
        row = tx.Row("g", "value", "tls.peer_name", [["a"], ["b"]], npath="tls.peer_name",
                     values={"a": "h", "b": "[h]"})
        self.assertFalse(self.doc({"a.py": "# peer name for TLS (tls.peer_name); if none, the host\n"}, row))

    def test_numbers_and_booleans_need_the_value_next_to_the_key(self) -> None:
        row = tx.Row("g", "value", "tls.enabled", [["a"], ["b"]], npath="tls.enabled", values={"a": True, "b": False})
        self.assertFalse(self.doc({"b.py": "# Intended: `enabled` stays true whenever false certificates exist\n"},
                                  row))
        self.assertTrue(self.doc({"b.py": "# Intended: this copy writes `enabled`: false\n"}, row))

    def test_whole_output_rows_are_never_documented_by_comments(self) -> None:
        row = tx.Row("g", "reject", "", [["a"], ["b"]], inputs=[tx.Input("play://x:1")])
        self.assertEqual(self.doc({"NOTES.md": "Intended: b accepts play://x:1 on purpose\n"}, row), [])


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        make_repo(self.tmp, git=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name: str, data) -> Path:
        p = self.tmp / name
        p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return p

    def test_config_errors_exit_2(self) -> None:
        bad = [
            {},
            {"groups": [{"name": "g", "inputs": {"from_tests": "("}, "copies": [{"name": "a", "run": "x"}]}]},
            {"groups": [{"name": "g", "inputs": {"extra": ["x"]}, "copies": [{"name": "a"}], "reference": "z"}]},
            {"groups": [{"name": "g", "copies": [{"name": "a"}]}]},
            {"groups": [{"name": "g", "inputs": {"extra": ["x"]}, "copies": [{"name": "a"}, {"name": "a"}]}]},
        ]
        for k, data in enumerate(bad):
            with self.subTest(k=k):
                code, _, err = run(self.tmp, self.write("bad.json", data))
                self.assertEqual(code, 2, err)
        code, _, err = run(self.tmp, self.tmp / "missing.json")
        self.assertEqual(code, 2)
        args = argparse.Namespace(repo=str(self.tmp), config=None, json=False, md=False, strict=False)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(tx.run_cli(args), 2)
        self.assertIn("no twins.json", err.getvalue())

    def test_rev_without_git_is_a_config_error(self) -> None:
        cfg = dict(demo_config(), rev="HEAD")
        with mock.patch("pmap.twins_harvest.subprocess.run", side_effect=FileNotFoundError("git")):
            code, _, err = run(self.tmp, self.write("rev.json", cfg))
        self.assertEqual(code, 2)
        self.assertIn("git not found", err)

    def test_found_at_repo_root(self) -> None:
        self.write("twins.json", {"groups": [{"name": "g", "inputs": {"extra": ["play://x.example:1"]},
                                               "copies": [{"name": "a", "run": [PY, "{repo}/a.py"]},
                                                          {"name": "c", "run": [PY, "{repo}/c.py"]}]}]})
        args = argparse.Namespace(repo=str(self.tmp), config=None, json=True, md=False, strict=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(tx.run_cli(args), 0)
        self.assertEqual(json.loads(out.getvalue())["groups"][0]["rows"], [])

    def test_yaml_block_style(self) -> None:
        yaml = textwrap.dedent(f"""\
            groups:
              - name: demo
                inputs:
                  from_tests: "play://.*"
                  extra:
                    - "play://synthetic.example:2"
                reference: a
                copies:
                  - name: a
                    language: Python
                    files:
                      - a.py
                    run:
                      - "{PY}"
                      - "{{repo}}/a.py"
                  - name: kotlin
                    language: Kotlin
                    files:
                      - StreamLink.kt
            """)
        cfg = tx.load_config(self.write("twins.yaml", yaml))
        g = cfg.groups[0]
        self.assertEqual((g.name, g.from_tests, g.extra, g.reference),
                         ("demo", "play://.*", ["play://synthetic.example:2"], "a"))
        self.assertEqual(g.copies[0].run, [PY, "{repo}/a.py"])
        self.assertIsNone(g.copies[1].run)

    def test_not_executed_copies_and_strict(self) -> None:
        cfg = demo_config(judge=None, copies=[
            {"name": "a", "files": ["a.py"], "run": [PY, "{repo}/a.py"]},
            {"name": "kotlin", "language": "Kotlin", "files": ["StreamLink.kt"]},
            {"name": "broken", "build": [PY, "-c", "import sys; sys.exit('error: no compiler here')"],
             "run": [PY, "{repo}/a.py"]},
            {"name": "shell", "build": "test -d {work} && test -f {repo}/a.py && echo built > marker",
             "run": "cat marker >/dev/null && " + " ".join(shlex.quote(a) for a in [PY, str(self.tmp / "a.py")])},
            {"name": "typo", "run": [str(self.tmp / "no-such-binary")]},
        ])
        code, out, err = run(self.tmp, self.write("t.json", cfg), strict=True)
        self.assertEqual(code, 1)
        self.assertIn("kotlin (Kotlin): no run command (attribution only)", out)
        self.assertIn("broken: build failed (exit 1: error: no compiler here)", out)
        self.assertIn("typo: not run: cannot start", out)
        self.assertIn("ran shell", out)
        self.assertIn("broken did not run", err)
        self.assertIn("typo did not run", err)
        self.assertNotIn("kotlin did not run", err)
        self.assertIn("agree on 13 of 13 inputs: 12 identical outputs (no judge), 1 rejected by every copy", out)

    def test_work_dirs_never_collide(self) -> None:
        build = "test -z \"$(ls -A)\" && touch mine"  # fails when another copy's files are in {work}
        cfg = demo_config(judge=None, reference=None, copies=[
            {"name": "swift (macOS)", "build": build, "run": [PY, "{repo}/a.py"]},
            {"name": "swift macOS", "build": build, "run": [PY, "{repo}/a.py"]},
            {"name": "judge", "build": build, "run": [PY, "{repo}/a.py"]},
        ])
        code, out, _ = run(self.tmp, self.write("t.json", cfg), json=True)
        self.assertTrue(all(c["executed"] for c in json.loads(out)["groups"][0]["copies"]), out)

    def test_strict_fails_when_nothing_was_compared(self) -> None:
        cases = {
            "no input": demo_config(judge=None, inputs={"from_tests": "onlyy\\..*"}),
            "one copy": demo_config(judge=None, copies=[{"name": "a", "run": [PY, "{repo}/a.py"]},
                                                        {"name": "b", "files": ["b.py"]}]),
            "judge broken": demo_config(judge=[str(self.tmp / "no-such-checker"), "{file}"]),
        }
        for name, cfg in cases.items():
            with self.subTest(name):
                code, out, err = run(self.tmp, self.write("t.json", cfg), strict=True)
                self.assertEqual(code, 1, (out, err))
        _code, out, _ = run(self.tmp, self.write("t.json", cases["one copy"]))
        self.assertIn("differences: not compared (only a ran)", out)
        _code, out, _ = run(self.tmp, self.write("t.json", cases["one copy"]), json=True)
        self.assertFalse(json.loads(out)["groups"][0]["compared"])

    def test_judge_that_cannot_start_judges_nothing(self) -> None:
        for judge in ([str(self.tmp / "no-such-checker"), "{file}"], "no-such-checker-xyz check -c {file}"):
            with self.subTest(judge=judge):
                code, out, _ = run(self.tmp, self.write("t.json", demo_config(judge=judge)), json=True)
                group = json.loads(out)["groups"][0]
                self.assertEqual(code, 0)
                self.assertTrue(group["judge_note"].startswith("judge did not run: "), group["judge_note"])
                self.assertEqual((group["judge"], group["judge_refusals"]), ({}, []))
                self.assertFalse([r for r in group["rows"] if r["kind"] == "judge"])

    def test_a_judge_that_times_out_refuses_nothing(self) -> None:
        slow = self.write("slow_judge.py", "import json, sys, time\n"
                                           "if '[' in json.load(open(sys.argv[1]))['server']:\n    time.sleep(30)\n")
        cfg = demo_config(judge=[PY, str(slow), "{file}"], timeout=2)
        code, out, _ = run(self.tmp, self.write("t.json", cfg), json=True)
        group = json.loads(out)["groups"][0]
        self.assertFalse([r for r in group["rows"] if r["kind"] == "judge"])
        self.assertEqual(group["not_judged"]["b"]["why"], "timed out after 2s")
        self.assertEqual(group["judge_refusals"], [])

    def test_a_hanging_copy_costs_one_timeout_and_leaves_no_process(self) -> None:
        cfg = demo_config(judge=None, copies=[{"name": "a", "run": [PY, "{repo}/a.py"]},
                                              {"name": "hang", "run": "sleep 37.25; cat", "timeout": 1}])
        started = time.time()
        code, out, err = run(self.tmp, self.write("t.json", cfg), strict=True)
        self.assertLess(time.time() - started, 15)
        self.assertIn("hang: not run: timed out after 1s", out)
        self.assertIn("hang did not run", err)
        if shutil.which("pgrep"):
            time.sleep(0.3)
            left = subprocess.run(["pgrep", "-f", "sleep 37.25"], capture_output=True, text=True).stdout.split()
            self.assertEqual(left, [])

    def test_an_error_that_names_the_work_dir_has_a_stable_fingerprint(self) -> None:
        fail = self.write("fail.py", "import sys\nfor _ in sys.stdin:\n    pass\nsys.exit('fatal error at ' + sys.argv[0])\n")
        cfg = demo_config(judge=None, copies=[{"name": "a", "run": [PY, "{repo}/a.py"]},
                                              {"name": "b", "build": ["cp", str(fail), "{work}/b.py"],
                                               "run": [PY, "{work}/b.py"]}])
        prints = []
        for _ in range(2):
            _code, out, _ = run(self.tmp, self.write("t.json", cfg), json=True)
            rows = json.loads(out)["groups"][0]["rows"]
            prints.append(sorted(r["fingerprint"] for r in rows))
            self.assertTrue(any("{work}/b.py" in str(r["values"]) for r in rows), rows)
            self.assertFalse(any("pmap-twins-" in str(r["values"]) for r in rows), rows)
        self.assertEqual(prints[0], prints[1])

    def test_output_lines_split_on_newline_only(self) -> None:
        echo = self.write("echo.py", "import json, sys\nfor line in sys.stdin:\n"
                                     "    print(json.dumps({'text': line.strip() + '\\u2028x\\x85y'}, ensure_ascii=False))\n")
        cfg = demo_config(judge=None, copies=[{"name": "a", "run": [PY, str(echo)]}, {"name": "b", "run": [PY, str(echo)]}])
        _code, out, _ = run(self.tmp, self.write("t.json", cfg), json=True)
        group = json.loads(out)["groups"][0]
        self.assertEqual([c["note"] for c in group["copies"]], ["", ""])
        self.assertEqual(group["rows"], [])

    def test_writer_not_found_says_what_was_searched(self) -> None:
        self.write("d.py", "from dataclasses import dataclass\n\n\n@dataclass\nclass Cfg:\n    buffer_ms: int = 1300\n")
        files = RepoFiles(self.tmp)
        w = tx.find_writer(files, ["d.py"], "buffer_ms", 1300, {})
        self.assertFalse(w["found"])
        self.assertIn("may come from a struct field", w["note"])
        self.assertIn("possibly written by a field named buffer_ms at d.py:6", w["note"])


@unittest.skipUnless(shutil.which("swiftc") and shutil.which("rustc"), "swiftc and rustc needed")
class TestCompiledFixture(unittest.TestCase):
    """tests/fixtures/twins: the Swift and Rust copies compiled, the Python copy run, the Kotlin one only attributed,
    with a small checker standing in for the player's own config check."""

    CHECK = ("import json, sys\ncfg = json.load(open(sys.argv[1]))\nsrc = cfg['sources'][0]\n"
             "if not 0 < src['port'] <= 65535:\n    sys.exit('FATAL: port %d out of range' % src['port'])\n"
             "for rule in cfg['playback']['rules']:\n    for p in rule['peer_cidr']:\n"
             "        if '[' in p:\n            sys.exit('FATAL: cannot parse prefix %r' % p)\n")

    def test_swift_rust_python(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "check.py").write_text(self.CHECK, encoding="utf-8")
            cfg = {"groups": [{
                "name": "stream link -> player config",
                "inputs": {"from_tests": r"(?:stream|podcast)://.*"},
                "reference": "rust",
                "judge": [PY, "{config_dir}/check.py", "{file}"],
                "timeout": 300,
                "copies": [
                    {"name": "rust", "language": "Rust", "files": ["rust/main.rs"],
                     "build": ["rustc", "-o", "{work}/twin", "{repo}/rust/main.rs"], "run": ["{work}/twin"]},
                    {"name": "swift", "language": "Swift", "files": ["swift/main.swift"],
                     "build": ["swiftc", "-o", "{work}/twin", "{repo}/swift/main.swift"], "run": ["{work}/twin"]},
                    {"name": "python", "language": "Python", "files": ["python/stream_link.py"],
                     "run": [PY, "{repo}/python/stream_link.py"]},
                    {"name": "kotlin", "language": "Kotlin", "files": ["kotlin/StreamLink.kt"]},
                ],
            }]}
            (tmp / "twins.json").write_text(json.dumps(cfg), encoding="utf-8")
            code, out, _ = run(FIXTURE, tmp / "twins.json", json=True)
            self.assertEqual(code, 0, out)
            group = json.loads(out)["groups"][0]
            ran = {c["name"]: c["executed"] for c in group["copies"]}
            self.assertEqual(ran, {"rust": True, "swift": True, "python": True, "kotlin": False}, group["copies"])
            rows = {(r["kind"], r["path"]): r for r in group["rows"]}
            host = rows[("value", "sources[0].host")]
            self.assertEqual(host["partition"], [["rust", "python"], ["swift"]])
            self.assertEqual(host["status"], "undocumented")  # a comment that states a fact explains nothing
            self.assertEqual(host["writers"]["swift"]["path"], "swift/main.swift")
            self.assertTrue(host["writers"]["swift"]["found"])
            self.assertIn("swift/main.swift:23: // URLComponents keeps the brackets of an IPv6 host.",
                          host["writers"]["swift"]["comments_near"])
            self.assertFalse(host["writers"]["kotlin"]["ran"])
            self.assertIn(("value", "playback.rules[0].peer_cidr"), rows)
            reject = rows[("reject", "")]
            self.assertEqual(reject["first_input"], "stream://listener@example.com:999999#bad")
            self.assertEqual(reject["values"]["swift"], "produced output; the judge refused it: FATAL: port 999999 "
                                                        "out of range")
            eq = rows[("absent", "sources[0].eq")]
            self.assertEqual(eq["absent_in"], ["python"])
            self.assertEqual(eq["status"], "documented")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
