"""Candidate copies of one piece of logic in several languages (pmap/twins.py): pairing, grouping, constants,
reach, the `pmap twins` command and the note after an edit.

`tests/fixtures/twins` is one stream link -> audio player config builder written four times (Swift, Rust, Kotlin,
Python) plus an older Swift builder. The Swift copy keeps the brackets of an IPv6 host and has no port range check,
and the Python copy leaves out `eq` on purpose (README.md); none of that is visible to the static search, which only
says where the candidates are. The tests that scan the fixture need the tree-sitter grammars; the rest build small
maps by hand.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from pmap import hints, twins
from pmap.cli import main
from pmap.hook import run
from pmap.models import ActualEdge, ActualMap, ActualNode, CodeLocation, EdgeLocation
from pmap.scanner import ProjectScanner
from tests.test_langs import needs

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "twins"
SWIFT = "sym:swift/main.swift:buildConfig"
RUST = "sym:rust/main.rs:build_config"
KOTLIN = "sym:kotlin/StreamLink.kt:buildConfig"
PYTHON = "sym:python.stream_link:build_config"
LEGACY = "sym:swift/LegacyConfig.swift:legacyConfig"


def vocab(node_id: str, language: str, keys, name: str = "", path: str = "", roles=None) -> twins.FunctionVocabulary:
    return twins.FunctionVocabulary(node_id, language, path or f"{node_id}.{language}", 1, 10, name or node_id,
                                    {k: i + 1 for i, k in enumerate(keys)}, dict(roles or {}))


def node(nid: str, path: str, start: int, end: int, kind: str = "function") -> ActualNode:
    return ActualNode(id=nid, kind=kind, name=nid.split(":")[-1].split(".")[-1], qualified_name=nid,
                      location=CodeLocation(path, start, end, "x"))


def call(a: str, b: str, path: str = "x", bridge=None) -> ActualEdge:
    return ActualEdge(a, b, "calls", "resolved", location=EdgeLocation(path, 1), bridge=bridge)


class HandMadeMap(unittest.TestCase):
    """Source files written to a temp folder and a map whose nodes are given by hand: no grammar needed."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.nodes = []
        self.edges = []

    def write(self, rel: str, text: str, functions=(), newline: str = "\n") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
        lines = text.count("\n") + 1
        self.nodes.append(node(f"mod:{rel}", rel, 1, lines, "module"))
        for name, start, end in functions:
            self.nodes.append(node(f"sym:{rel}:{name}", rel, start, end))

    def actual(self) -> ActualMap:
        return ActualMap("0.2.0", "", str(self.root), nodes=self.nodes, edges=self.edges)

    def vocabs(self) -> dict:
        return {v.node_id: v for v in twins.vocabularies(self.actual(), self.root)}

    def keys(self, nid: str) -> set:
        found = self.vocabs()
        self.assertIn(nid, found, f"{nid} has no vocabulary")
        return set(found[nid].keys)


class TestConstants(HandMadeMap):
    """A key written through a string constant counts as the key itself, resolved by its qualified name."""

    def test_rust_module_constant(self) -> None:
        self.write("src/build.rs", 'mod tag {\n    pub const LOCAL: &str = "local";\n}\n\nfn build() {\n'
                                   '    let a = tag::LOCAL;\n    let b = ("kind", "tag", "host");\n}\n',
                   [("build", 5, 8)])
        self.assertEqual(self.keys("sym:src/build.rs:build"), {"local", "kind", "tag", "host"})

    def test_rust_constant_from_another_file(self) -> None:
        self.write("src/tag.rs", 'pub const REMOTE: &str = "remote";\n')
        self.write("src/build.rs", 'use crate::tag;\nfn build() {\n    let a = (crate::tag::REMOTE, "kind", "tag", "host");\n}\n',
                   [("build", 2, 4)])
        self.assertIn("remote", self.keys("sym:src/build.rs:build"))

    def test_swift_enum_static_let(self) -> None:
        self.write("Tag.swift", 'enum Tag {\n    static let local = "local"\n    static let remote = "remote"\n}\n')
        self.write("Build.swift", 'func build() -> [String: Any] {\n    return ["tag": Tag.local, "kind": "x", '
                                  '"host": 1, "fallback": Tag.remote]\n}\n', [("build", 1, 3)])
        self.assertEqual(self.keys("sym:Build.swift:build"), {"local", "remote", "tag", "kind", "host", "fallback"})

    def test_csharp_java_kotlin_go_python(self) -> None:
        self.write("Tags.cs", 'static class Tags {\n    public const string Local = "local";\n}\n'
                              'class B {\n    void Build() { var x = (Tags.Local, "kind", "tag", "host"); }\n}\n',
                   [("B.Build", 5, 5)])
        self.write("Tags.java", 'class Tags {\n    public static final String LOCAL = "local";\n'
                                '    void build() { put(Tags.LOCAL, "kind", "tag", "host"); }\n}\n',
                   [("Tags.build", 3, 3)])
        self.write("Tags.kt", 'object Tag {\n    const val LOCAL = "local"\n}\n'
                              'fun build() = mapOf("kind" to Tag.LOCAL, "tag" to 1, "host" to 2)\n',
                   [("build", 4, 4)])
        self.write("tags.go", 'package tags\n\nconst (\n\tLocal string = "local"\n\tRemote = "remote"\n)\n\n'
                              'func Build() {\n\tm := map[string]string{"kind": Local, "tag": Remote, "host": ""}\n}\n',
                   [("Build", 8, 10)])
        self.write("tags.py", 'class Tag:\n    LOCAL = "local"\n\n\ndef build():\n'
                              '    return {"kind": Tag.LOCAL, "tag": 1, "host": 2}\n', [("build", 5, 6)])
        for nid in ("sym:Tags.cs:B.Build", "sym:Tags.java:Tags.build", "sym:Tags.kt:build", "sym:tags.py:build"):
            self.assertIn("local", self.keys(nid), nid)
        self.assertTrue({"local", "remote"} <= self.keys("sym:tags.go:Build"))

    def test_crlf_files_keep_their_constants(self) -> None:
        self.write("Tag.swift", 'enum Tag {\n    static let local = "local"\n}\n', newline="\r\n")
        self.write("Build.swift", 'func build() -> [String: Any] {\n    return ["tag": Tag.local, "kind": 1, '
                                  '"host": 2]\n}\n', [("build", 1, 3)], newline="\r\n")
        self.write("Tag.kt", 'object Tag {\n    const val REMOTE = "remote"\n}\n'
                             'fun build() = mapOf("kind" to Tag.REMOTE, "tag" to 1, "host" to 2)\n',
                   [("build", 4, 4)], newline="\r\n")
        self.write("tags.py", 'class Tag:\n    LOCAL = "local"\n\n\ndef build():\n'
                              '    return {"kind": Tag.LOCAL, "tag": 1, "host": 2}\n', [("build", 5, 6)], newline="\r\n")
        self.assertIn("local", self.keys("sym:Build.swift:build"))
        self.assertIn("remote", self.keys("sym:Tag.kt:build"))
        self.assertIn("local", self.keys("sym:tags.py:build"))

    def test_constants_resolve_within_one_language_family(self) -> None:
        self.write("Tag.swift", 'enum Tag {\n    static let local = "local"\n}\n')
        self.write("Build.kt", 'fun build() = mapOf("kind" to Tag.local, "tag" to 1, "host" to 2, "port" to 3)\n',
                   [("build", 1, 1)])
        self.assertNotIn("local", self.keys("sym:Build.kt:build"))

    def test_ambiguous_constant_resolves_to_nothing(self) -> None:
        self.write("a/tag.rs", 'pub const LOCAL: &str = "local";\n')
        self.write("b/tag.rs", 'pub const LOCAL: &str = "nearby";\n')
        self.write("build.rs", 'fn build() {\n    let a = (tag::LOCAL, "kind", "tag", "host", "port");\n}\n',
                   [("build", 1, 3)])
        keys = self.keys("sym:build.rs:build")
        self.assertNotIn("local", keys)
        self.assertNotIn("nearby", keys)

    def test_a_bare_name_resolves_only_inside_its_container(self) -> None:
        self.write("Keys.swift", 'enum Keys {\n    static let server = "server_addr"\n    static let port = "server_port"\n'
                                 '}\n\nfunc describe(server: String, port: Int) -> [String: Any] {\n'
                                 '    return ["aa": server, "bb": port, "cc": 1]\n}\n', [("describe", 6, 8)])
        self.assertEqual(self.keys("sym:Keys.swift:describe"), {"aa", "bb", "cc"})

    def test_python_keyword_arguments_are_not_constants(self) -> None:
        self.write("probe.py", 'import argparse\n\nPARSER = argparse.ArgumentParser(\n    prog="player-probe",\n'
                               '    description="probe",\n)\nDEFAULTS = dict(\n    server="probe_server",\n)\n\n\n'
                               'def run(server, prog, description):\n    return {"xx": server, "yy": prog, "zz": description}\n',
                   [("run", 12, 13)])
        self.assertEqual(self.keys("sym:probe.py:run"), {"xx", "yy", "zz"})

    def test_locals_comments_and_interpolation_are_not_keys(self) -> None:
        self.write("build.swift", 'func build(host: String) -> [String: Any] {\n    let local = "notakey"\n'
                                  '    // "commented"\n    return ["kind": "ab", "tag": "\\(host)", "host": local, '
                                  '"fallback": "has space", "mode": 1]\n}\n', [("build", 1, 5)])
        keys = self.keys("sym:build.swift:build")
        self.assertEqual(keys, {"notakey", "kind", "ab", "tag", "host", "fallback", "mode"})

    def test_write_and_read_roles(self) -> None:
        self.write("w.swift", 'func build() -> [String: Any] {\n    return ["kind": 1, "tag": 2, "host": 3]\n}\n',
                   [("build", 1, 3)])
        self.write("r.swift", 'func read(_ d: [String: Any]) {\n    let a = d["kind"] as? Int\n'
                              '    let b = d["tag"] as? Int\n    switch x { case "host": break }\n}\n', [("read", 1, 5)])
        found = self.vocabs()
        self.assertEqual(found["sym:w.swift:build"].roles, {"kind": "w", "tag": "w", "host": "w"})
        self.assertEqual(found["sym:r.swift:read"].roles, {"kind": "r", "tag": "r", "host": "r"})


class TestSkipped(HandMadeMap):
    """Tests and their fixtures are inputs of the copies, not copies; generated and vendored code is nobody's copy."""

    BODY = '{\n    let a = ("kind", "tag", "host", "fallback", "playback");\n}\n'

    def ids(self) -> set:
        return set(self.vocabs())

    def test_test_files_fixtures_rust_test_modules_and_generated_files(self) -> None:
        self.write("tests/build.rs", "fn build() " + self.BODY, [("build", 1, 3)])
        self.write("Sources/BuildTests.swift", "func build() " + self.BODY, [("build", 1, 3)])
        self.write("windows/fixtures/main.rs", "fn build() " + self.BODY, [("build", 1, 3)])
        self.write("gen/out.rs", "// Code generated by protoc. DO NOT EDIT.\nfn build() " + self.BODY, [("build", 2, 4)])
        self.write("src/lib.rs", "fn build() " + self.BODY + "\n#[cfg(test)]\nmod tests {\n    fn t() " + self.BODY +
                   "}\n\n#[test]\nfn single() " + self.BODY, [("build", 1, 3), ("t", 7, 9), ("single", 13, 15)])
        self.assertEqual(self.ids(), {"sym:src/lib.rs:build"})

    def test_vendored_paths_gitattributes_and_submodules(self) -> None:
        for rel in ("third-party/td/a.rs", "3rdparty/b.rs", "third_party/c.rs", "ext/lib/d.rs", "mods/sub/e.rs",
                    "src/keep.rs"):
            self.write(rel, "fn build() " + self.BODY, [("build", 1, 3)])
        (self.root / ".gitattributes").write_text("ext/** linguist-vendored\n*.md -linguist-vendored\n")
        (self.root / ".gitmodules").write_text('[submodule "sub"]\n\tpath = mods/sub\n\turl = x\n')
        self.assertEqual(self.ids(), {"sym:src/keep.rs:build"})

    def test_three_keys_are_kept_for_named_pairs(self) -> None:
        self.write("src/lib.rs", 'fn small() {\n    let a = ("kind", "tag", "host");\n}\n', [("small", 1, 3)])
        self.write("src/two.rs", 'fn two() {\n    let a = ("kind", "tag");\n}\n', [("two", 1, 3)])
        self.assertEqual(self.ids(), {"sym:src/lib.rs:small"})


class TestPairing(unittest.TestCase):
    KEYS = ["kind", "tag", "host", "port", "eq", "preset", "peer_name", "peer_cidr"]

    def run_pairs(self, vocabs, linked=None):
        pairs, scores, _idf = twins.find_pairs(vocabs, linked=linked)
        return pairs, twins.group_pairs(vocabs, pairs, scores, linked)

    def test_mutual_best_across_languages_only(self) -> None:
        vocabs = [vocab("swift", "swift", self.KEYS, "buildConfig"), vocab("rust", "rust", self.KEYS, "build_config"),
                  vocab("swift2", "swift", self.KEYS[:7] + ["x1"], "build"),
                  vocab("noise", "kotlin", ["a", "b", "c", "d"])]
        pairs, groups = self.run_pairs(vocabs)
        self.assertEqual([(a, b) for _s, a, b in pairs], [("rust", "swift")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["core"], ["rust", "swift"])
        self.assertEqual(groups[0]["extra"], ["swift2"])  # same language: joins through its pair with Rust only

    def test_a_weak_or_small_extra_under_another_name_stays_out(self) -> None:
        keys = self.KEYS + ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8"]
        weak = ["kind", "tag", "host", "port", "eq", "preset", "w1", "w2"]
        vocabs = [vocab("swift", "swift", keys, "build"), vocab("rust", "rust", keys, "build"),
                  vocab("weak", "swift", weak, "transport_of"),
                  vocab("store", "swift", ["kind", "tag", "host", "port"], "load")]
        _pairs, groups = self.run_pairs(vocabs)
        self.assertEqual(groups[0]["extra"], [])

    def test_same_language_alone_never_pairs(self) -> None:
        pairs, groups = self.run_pairs([vocab("a", "swift", self.KEYS), vocab("b", "swift", self.KEYS)])
        self.assertEqual((pairs, groups), ([], []))

    def test_name_bonus_breaks_ties(self) -> None:
        vocabs = [vocab("r", "rust", self.KEYS, "parse_stream"), vocab("s1", "swift", self.KEYS, "other"),
                  vocab("s2", "swift", self.KEYS, "parseStream")]
        pairs, _groups = self.run_pairs(vocabs)
        self.assertEqual([(a, b) for _s, a, b in pairs], [("r", "s2")])
        self.assertEqual(twins.normalized_name("Builder.ParseStream"), twins.normalized_name("parse_stream"))

    def test_scores_are_not_capped_before_the_choice(self) -> None:
        # both Swift functions overlap Rust's fully; the exact twin has the same name and must win
        keys = self.KEYS + ["k9", "k10", "k11", "k12"]
        vocabs = [vocab("rust_dns", "rust", keys, "dns"), vocab("swift_dns", "swift", keys, "dns"),
                  vocab("swift_lane", "swift", keys[:11] + ["lane"], "dns")]
        pairs, _groups = self.run_pairs(vocabs)
        self.assertEqual([(a, b) for _s, a, b in pairs], [("rust_dns", "swift_dns")])
        self.assertGreater(pairs[0][0], 1.0)

    def test_library_code_wins_a_tie_over_an_example(self) -> None:
        vocabs = [vocab("sym:example/main", "dart", self.KEYS, "read", path="example/lib/main.dart"),
                  vocab("sym:lib/info", "dart", self.KEYS, "read", path="lib/info.dart"),
                  vocab("swift", "swift", self.KEYS, "read")]
        pairs, _groups = self.run_pairs(vocabs)
        self.assertEqual([(a, b) for _s, a, b in pairs], [("swift", "sym:lib/info")])

    def test_a_small_vocabulary_pairs_only_by_name_and_inclusion(self) -> None:
        big = ["stream", "podcast", "radio", "local", "cache"]
        same = [vocab("cs", "csharp", big[:3], "Parse"), vocab("rs", "rust", big, "parse")]
        self.assertEqual([(a, b) for _s, a, b in self.run_pairs(same)[0]], [("cs", "rs")])
        other = [vocab("cs", "csharp", big[:3], "Dispatch"), vocab("rs", "rust", big, "parse")]
        self.assertEqual(self.run_pairs(other)[0], [])

    def test_threshold_and_minimum_shared(self) -> None:
        vocabs = [vocab("a", "swift", self.KEYS), vocab("b", "rust", self.KEYS[:3] + ["p", "q", "r", "s", "t"])]
        self.assertEqual(self.run_pairs(vocabs)[0], [])

    def test_two_ends_of_a_link_are_not_copies(self) -> None:
        vocabs = [vocab("java", "java", self.KEYS), vocab("rust", "rust", self.KEYS)]
        pairs, _groups = self.run_pairs(vocabs, linked=lambda a, b: {a, b} == {"java", "rust"})
        self.assertEqual(pairs, [])
        actual = ActualMap("0.2.0", "", ".", edges=[call("java", "native"), call("native", "rust", bridge={"via": "jni"})])
        self.assertTrue(twins.linker(actual)("rust", "java"))
        self.assertFalse(twins.linker(actual)("rust", "native2"))

    def test_linked_members_are_not_merged_through_a_third_language(self) -> None:
        linked = lambda a, b: {a, b} == {"java", "rust"}  # noqa: E731
        vocabs = [vocab("java", "java", self.KEYS, "addRule"), vocab("rust", "rust", self.KEYS, "add_rule"),
                  vocab("swift", "swift", self.KEYS, "addRule")]
        _pairs, groups = self.run_pairs(vocabs, linked)
        for g in groups:
            self.assertFalse({"java", "rust"} <= set(g["members"]), g)

    def test_a_helper_of_a_copy_is_not_another_copy(self) -> None:
        keys = self.KEYS + ["k9", "k10"]
        vocabs = [vocab("cs", "csharp", keys, "Build"), vocab("rs", "rust", keys, "build"),
                  vocab("cs_tls", "csharp", keys[:9], "Build")]
        _pairs, groups = self.run_pairs(vocabs, linked=lambda a, b: {a, b} == {"cs", "cs_tls"})
        self.assertEqual(groups[0]["extra"], [])

    def test_a_writer_and_a_reader_are_two_ends(self) -> None:
        w = vocab("w", "dart", self.KEYS, "toMap", roles={k: "w" for k in self.KEYS})
        r = vocab("r", "swift", self.KEYS, "handle", roles={k: "r" for k in self.KEYS})
        both = vocab("b", "rust", self.KEYS, "to_map", roles={k: "w" for k in self.KEYS})
        self.assertTrue(twins.two_ends(w, r))
        self.assertFalse(twins.two_ends(w, both))

    def test_a_big_function_does_not_glue_two_groups(self) -> None:
        dns, rules = ["dns", "servers", "strategy", "detour", "d5"], ["rules", "fallback", "peer_cidr", "action", "r5"]
        vocabs = [vocab("rust_dns", "rust", dns), vocab("rust_rules", "rust", rules),
                  vocab("swift_dns", "swift", dns), vocab("swift_rules", "swift", rules),
                  vocab("py_all", "python", dns + rules)]
        _pairs, groups = self.run_pairs(vocabs)
        for g in groups:
            langs = [m.split("_")[0] for m in g["core"]]
            self.assertEqual(len(langs), len(set(langs)), g)


class TestFindTwinsByHand(HandMadeMap):
    BUILD = '{\n    return ["kind": 1, "tag": 2, "host": 3, "port": 4, "codec": 5, "peer_name": 6]\n}\n'

    def test_writer_and_reader_are_listed_apart_and_left_out_of_the_note(self) -> None:
        self.write("ios/Build.swift", "func toMap() -> [String: Any] " + self.BUILD, [("toMap", 1, 3)])
        self.write("android/Read.java", 'class Read {\n  void read(Map m) {\n    m.get("kind"); m.get("tag"); '
                                        'm.get("host"); m.get("port"); m.get("codec"); m.get("peer_name");\n  }\n}\n',
                   [("Read.read", 2, 4)])
        groups = twins.find_twins(self.actual(), self.root)
        self.assertEqual([g.kind for g in groups], ["ends"])
        self.assertEqual(twins.twins_for_hints(groups), {})
        self.assertIn("two ends of one format", twins.format_twins(groups))

    def test_footer_says_what_was_not_read(self) -> None:
        self.write("ios/Build.swift", "func build() -> [String: Any] " + self.BUILD, [("build", 1, 3)])
        self.write("src/build.rs", 'fn build() {\n    let a = ("kind", "tag", "host", "port", "codec", "peer_name");\n}\n',
                   [("build", 1, 3)])
        for name in ("a.mm", "b.mm", "c.m"):
            (self.root / "darwin").mkdir(exist_ok=True)
            (self.root / "darwin" / name).write_text("x")
        stats = {}
        groups = twins.find_twins(self.actual(), self.root, stats=stats)
        self.assertEqual(stats["unread"], {".m": 1, ".mm": 2})
        text = twins.format_twins(groups, languages={"swift": {"files": 1, "mapped": True},
                                                     "rust": {"files": 1, "mapped": True}}, stats=stats)
        self.assertIn("1 candidate group: functions in different languages that use the same rare string literals", text)
        self.assertIn("not proof", text)
        self.assertIn("not read: 1 .m file (no Objective-C reader), 2 .mm files (no Objective-C++ reader)", text)
        self.assertIn("read 2 functions with 3 or more literals in Rust, Swift", text)
        self.assertIn("no proven callers in the map", text)


class TestReach(unittest.TestCase):
    def reach(self, edges, target="t") -> str:
        nodes = [node("t", "src/core.rs", 1, 2), node("app", "src/app.rs", 1, 2), node("lib", "src/lib.rs", 1, 2),
                 node("test", "tests/it.rs", 1, 2), node("ex", "examples/demo.rs", 1, 2),
                 node("java", "android/Engine.java", 1, 2), node("jtest", "android/EngineTest.java", 1, 2)]
        actual = ActualMap("0.2.0", "", ".", nodes=nodes, edges=edges)
        return twins.reach_of(target, twins.reach_index(actual), {n.id: n.location.path for n in nodes})

    def test_kinds(self) -> None:
        self.assertEqual(self.reach([call("java", "t", bridge={"via": "jni"})]), "reached from FFI/JNI")
        self.assertEqual(self.reach([call("lib", "t"), call("app", "lib")]), "reached from app code")
        self.assertEqual(self.reach([call("lib", "t"), call("ex", "lib"), call("test", "t")]), "examples/tests only")
        self.assertEqual(self.reach([]), "no proven callers in the map")
        self.assertEqual(self.reach([call("jtest", "t", bridge={"via": "jni"})]), "examples/tests only")
        self.assertEqual(self.reach([call("lib", "t"), call("t", "lib")]), "reached from app code")  # a cycle

    def test_unproven_calls_do_not_count(self) -> None:
        edge = ActualEdge("app", "t", "calls", "inferred", location=EdgeLocation("src/app.rs", 1))
        self.assertEqual(self.reach([edge]), "no proven callers in the map")


class TestOutput(unittest.TestCase):
    GROUP = twins.TwinGroup("twin:abc", "buildConfig / build_config", 0.91, [
        twins.TwinMember("sym:a", "swift", "swift/main.swift", 14, 40, "buildConfig", 22, 21, "reached from app code",
                         score=0.95, partner="build_config", pair_shared=21, same_name=True),
        twins.TwinMember("sym:b", "rust", "rust/main.rs", 44, 80, "build_config", 26, 21, "reached from FFI/JNI",
                         score=0.95, partner="buildConfig", pair_shared=21, same_name=True),
        twins.TwinMember("sym:c", "swift", "swift/Old.swift", 4, 9, "old", 6, 6, "no proven callers in the map", True,
                         score=0.8, partner="build_config", pair_shared=6),
    ], ["preset", "peer_cidr"], more_evidence=3,
        links=[["sym:a", "sym:b", 0.95, 21], ["sym:b", "sym:c", 0.8, 6]])

    def test_text_and_markdown(self) -> None:
        text = twins.format_twins([self.GROUP])
        self.assertIn("buildConfig / build_config  (literal overlap 0.91, twin:abc)", text)
        self.assertIn("swift/main.swift:14  buildConfig  (Swift, reached from app code; overlap 0.95 with build_config: "
                      "21 of 22 literals, same name: yes; shares 21 of 22 with the group)", text)
        self.assertIn("(Swift, same language, no proven callers in the map; overlap 0.80 with build_config: 6 of 6 "
                      "literals, same name: no, small vocabulary;", text)
        self.assertIn('shared literals: "preset", "peer_cidr" (+3 more)', text)
        self.assertIn("- `rust/main.rs:44`", twins.format_twins([self.GROUP], markdown=True))
        self.assertIn("No candidate groups", twins.format_twins([]))

    def test_not_searched_when_languages_are_not_mapped(self) -> None:
        text = twins.format_twins([], languages={"python": {"files": 2, "mapped": True},
                                                 "swift": {"files": 2, "mapped": False},
                                                 "rust": {"files": 1, "mapped": False}})
        self.assertIn("Not searched: Rust, Swift are not mapped (`pmap langs install`)", text)
        self.assertNotIn("No candidate groups", text)

    def test_round_trip_and_hint_data(self) -> None:
        self.assertEqual(twins.TwinGroup.from_dict(self.GROUP.to_dict()), self.GROUP)
        data = twins.twins_for_hints([self.GROUP])
        # the old Swift builder pairs with Rust at 0.80 but over 6 literals under another name: too weak to name
        self.assertEqual(data["sym:b"]["others"], [["swift/main.swift", 14, "buildConfig", "swift", 21, 0.95]])
        self.assertNotIn("sym:c", data)
        lines = hints.twin_lines(data["sym:a"])
        self.assertEqual(lines, ["  possible copies in other languages (static: shared string literals; not run):",
                                 "    rust/main.rs:44 build_config (Rust; shares 21 of 22 literals)",
                                 "  if this change fixes behaviour, check them; copies may differ on purpose"])
        low = twins.TwinGroup("twin:low", "x", 0.9, self.GROUP.members, [], links=[["sym:a", "sym:b", 0.6, 21]])
        self.assertEqual(twins.twins_for_hints([low]), {})

    def test_note_caps_and_known_differences(self) -> None:
        entry = {"size": 9, "others": [[f"p{k}.rs", k, f"f{k}", "rust", 5, 0.9] for k in range(8)]}
        lines = hints.twin_lines(entry, ["python/x.py:4: Intended: this copy never writes `eq`"])
        self.assertIn("    (and 2 more: pmap twins)", lines)
        self.assertIn("  known difference: python/x.py:4: Intended: this copy never writes `eq`", lines)

    def test_exec_is_delegated(self) -> None:
        fake = types.ModuleType("pmap.twins_exec")
        seen = []
        fake.run_cli = lambda args: seen.append(args) or 7
        with mock.patch.dict(sys.modules, {"pmap.twins_exec": fake}):
            code = main(["twins", str(FIXTURE), "--exec", "--config", "/elsewhere/twins.json", "--strict", "--md"])
        self.assertEqual(code, 7)
        self.assertEqual((seen[0].config, seen[0].strict, seen[0].md), ("/elsewhere/twins.json", True, True))

    def test_strict_and_config_need_exec(self) -> None:
        for extra in (["--strict"], ["--config", "/nonexistent.json"]):
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["twins", str(FIXTURE)] + extra), 2)
            self.assertIn("need --exec", err.getvalue())


@needs("swift", "rust", "kotlin")
class TestFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.map = ProjectScanner().scan_repository(FIXTURE)
        cls.groups = twins.find_twins(cls.map, FIXTURE)

    def test_one_group_with_every_language(self) -> None:
        self.assertEqual(len(self.groups), 1)
        g = self.groups[0]
        self.assertEqual(g.kind, "copies")
        core = [m.node_id for m in g.members if not m.same_language]
        self.assertEqual(sorted(core), sorted([SWIFT, RUST, KOTLIN, PYTHON]))
        self.assertEqual([m.node_id for m in g.members if m.same_language], [LEGACY])
        self.assertGreaterEqual(g.score, twins.HIGH_SCORE)
        self.assertIn("peer_cidr", g.evidence + ["peer_cidr"] * bool(g.more_evidence))

    def test_constants_reach_the_vocabularies(self) -> None:
        found = {v.node_id: v.keys for v in twins.vocabularies(self.map, FIXTURE)}
        for nid in (SWIFT, RUST, KOTLIN, PYTHON):
            self.assertIn("remote", found[nid], nid)  # only ever written as Tag.remote, tag::REMOTE, Tag.REMOTE
        self.assertNotIn("sym:tests/test_links.py:StreamLinkTests.test_accepted", found)

    def test_reach(self) -> None:
        reach = {m.node_id: m.reach for m in self.groups[0].members}
        self.assertEqual(reach[RUST], "reached from app code")  # main()
        self.assertEqual(reach[KOTLIN], "no proven callers in the map")
        self.assertEqual(reach[LEGACY], "no proven callers in the map")

    def test_deterministic(self) -> None:
        again = twins.find_twins(ProjectScanner().scan_repository(FIXTURE), FIXTURE)
        self.assertEqual([g.to_dict() for g in again], [g.to_dict() for g in self.groups])

    def test_cli(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["twins", str(FIXTURE)]), 0)
        text = out.getvalue()
        self.assertIn("rust/main.rs:44  build_config  (Rust, reached from app code; overlap", text)
        self.assertIn("swift/LegacyConfig.swift:4  legacyConfig  (Swift, same language", text)
        self.assertIn("read 5 functions with 3 or more literals in Kotlin, Python, Rust, Swift; skipped 1 test file", text)
        out = io.StringIO()
        with redirect_stdout(out):
            main(["twins", str(FIXTURE), "--json", "--min-score", "1.5"])
        self.assertEqual(json.loads(out.getvalue())["groups"], [])


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestChannelHandlersAreNotCopies(unittest.TestCase):
    """Each platform's handler of one Flutter channel names the same methods; that is the two ends of a link (a
    missing branch is a gap), not a copy."""

    def test_handlers_of_one_channel_are_left_out(self) -> None:
        root = Path(__file__).resolve().parent / "fixtures" / "langs" / "flutter_player"
        groups = twins.find_twins(ProjectScanner().scan_repository(root), root)
        names = {m.name for g in groups for m in g.members}
        self.assertFalse(names & {"handle", "HandleMethodCall", "PlayerChannel::HandleMethodCall"}, groups)


@needs("swift", "rust", "kotlin")
class TestPostEditNote(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "twins"
        shutil.copytree(FIXTURE, self.root)
        (self.root / "Makefile").write_text("all:\n", encoding="utf-8")  # a project marker
        env = mock.patch.dict(os.environ, {"PMAP_HOME": str(self.tmp / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        quiet = mock.patch.object(hints, "refresh_in_background", lambda root: None)
        quiet.start()
        self.addCleanup(quiet.stop)

    def note(self, rel: str, new: str) -> str:
        payload = {"tool_name": "Edit", "cwd": str(self.root), "session_id": "s1",
                   "tool_input": {"file_path": str(self.root / rel), "old_string": "x", "new_string": new}}
        with mock.patch("sys.stdout") as out:
            run("post-edit", json.dumps(payload))
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        return json.loads(printed)["hookSpecificOutput"]["additionalContext"] if printed.strip() else ""

    def test_edit_of_a_copy_names_the_candidates(self) -> None:
        text = self.note("rust/main.rs", "    let prefix = if host.contains(':') { \"/128\" } else { \"/32\" };")
        self.assertIn("may have hand-written copies in other languages", text)
        self.assertNotIn("keep it in step", text)
        self.assertIn("build_config (rust/main.rs:44)", text)
        self.assertIn("possible copies in other languages (static: shared string literals; not run):", text)
        self.assertIn("swift/main.swift:14 buildConfig (Swift; shares", text)
        self.assertIn("kotlin/StreamLink.kt:11 buildConfig (Kotlin; shares", text)
        self.assertIn("python/stream_link.py:17 build_config (Python; shares", text)
        self.assertIn("if this change fixes behaviour, check them; copies may differ on purpose", text)
        self.assertNotIn("known difference", text)

    def test_an_intended_difference_in_another_copy_is_named(self) -> None:
        text = self.note("rust/main.rs", '        source.push(("eq", obj(vec![("preset", text(preset)), '
                                         '("gain", text(gain))])));')
        self.assertIn("known difference: python/stream_link.py:4: Intended difference: this copy never writes `eq`",
                      text)

    def test_edit_elsewhere_says_nothing_about_twins(self) -> None:
        text = self.note("rust/main.rs", "        Json::Bool(b) => b.to_string(),")
        self.assertNotIn("possible copies", text)


if __name__ == "__main__":
    unittest.main()
