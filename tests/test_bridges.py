"""Links between languages (pmap/langs/bridges.py) and the note an agent gets after an edit (pmap/hints.py).

`flutter_player` is an audio player: Flutter UI, Swift for macOS (with a NetworkExtension provider), Kotlin for
Android, C++ for Windows and a Rust core reached through FFI, JNI and flutter_rust_bridge. Android deliberately does
not handle the `status` method of the control channel. `tauri_player` and `uniffi_player` cover Tauri commands and
UniFFI.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pmap import hints
from pmap.hook import run
from pmap.langs import bridges
from pmap.mermaid import format_agent_context
from pmap.models import ActualMap
from pmap.scanner import ProjectScanner
from tests.test_langs import FIXTURES, LangCase, needs

DART = "sym:lib/player/platform_player.dart:PlatformPlayer"
SWIFT = "sym:macos/Runner/PlayerChannel.swift:PlayerChannel.handle"
KOTLIN = "sym:android/app/src/main/kotlin/com/example/player/MainActivity.kt:MainActivity.configureFlutterEngine"
CPP = "sym:windows/runner/player_channel.cpp:PlayerChannel.HandleMethodCall"
FFI = "sym:rust/src/ffi.rs"


class BridgeCase(LangCase):
    def link(self, src: str, dst: str, via: str, confidence: str = "resolved"):
        found = [e for e in self.map.edges if e.from_id == src and e.to_id == dst and e.bridge]
        self.assertTrue(found, f"no link {src} -> {dst}")
        self.assertEqual(found[0].bridge["via"], via)
        self.assertEqual(found[0].confidence, confidence, f"{src} -> {dst}")
        return found[0]


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestFlutterPlayer(BridgeCase):
    project = "flutter_player"

    def test_channel_methods_reach_every_platform(self) -> None:
        for target, line in ((SWIFT, 24), (KOTLIN, 17), (CPP, 15)):
            e = self.link(f"{DART}.connect", target, "channel")
            self.assertEqual((e.bridge["name"], e.bridge["method"]), ("audio/player", "connect"))
            self.assertEqual(e.bridge["to"]["line"], line)  # the `case "connect"` branch, not the whole handler
        self.assertEqual(e.location.path, "lib/player/platform_player.dart")

    def test_channel_name_from_a_constant(self) -> None:
        # Dart: MethodChannel(kControlChannel); Swift: name: controlName; Kotlin: MethodChannel(messenger, channelName)
        names = {e.bridge["name"] for e in bridges.bridge_edges(self.map) if e.bridge["via"] == "channel"}
        self.assertEqual(names, {"audio/player"})

    def test_method_a_platform_does_not_handle_is_a_gap(self) -> None:
        gaps = bridges.gaps(self.map)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual((gap.from_id, gap.confidence), (f"{DART}.status", "unresolved"))
        self.assertEqual((gap.bridge["method"], gap.bridge["missing"]), ("status", "Kotlin (android)"))
        self.link(f"{DART}.status", SWIFT, "channel")
        self.link(f"{DART}.status", CPP, "channel")

    def test_native_calls_back_into_dart(self) -> None:
        e = self.link("sym:macos/Runner/PlayerChannel.swift:PlayerChannel.revoked", f"{DART}._onNativeCall", "channel")
        self.assertEqual(e.bridge["method"], "onRevoked")

    def test_event_channel_reaches_the_stream_handler(self) -> None:
        self.link(f"{DART}.states", "sym:macos/Runner/PlayerChannel.swift:StateStreamHandler.onListen", "event-channel")
        # Kotlin's handler is an anonymous object: the function that registers it stands for it
        self.link(f"{DART}.states", "sym:android/app/src/main/kotlin/com/example/player/PlayerStateStream.kt:"
                                    "PlayerStateStream.attach", "event-channel")

    def test_c_abi_from_dart_swift_and_cpp(self) -> None:
        self.link("sym:lib/core/core_bindings.dart:CoreBindings.version", f"{FFI}:player_core_version", "ffi")
        self.link("sym:lib/core/core_bindings.dart:CoreBindings.parseConfig", f"{FFI}:player_core_parse_config", "ffi")
        self.link("sym:lib/core/core_bindings.dart:playerCoreStats", f"{FFI}:player_core_stats", "ffi")  # @Native
        self.link("sym:macos/Runner/ExtensionController.swift:ExtensionController.connect", f"{FFI}:player_core_start",
                  "ffi")
        self.link(CPP, f"{FFI}:player_core_start", "ffi")

    def test_header_declaration_gives_way_to_the_definition(self) -> None:
        self.assertNoEdge(CPP, "sym:native/player_core.h:player_core_start")
        self.assertFalse([e for e in self.map.errors if e.path == "native/player_core.h"])  # `extern "C"` guards parse

    def test_replaced_unknowns_are_gone(self) -> None:
        version = "sym:lib/core/core_bindings.dart:CoreBindings.version"
        self.assertNoEdge(version, "unresolved:fn")  # the looked-up function pointer
        self.assertNoEdge("sym:macos/Runner/ExtensionController.swift:ExtensionController.connect",
                          "unresolved:manager.connection.startVPNTunnel")

    def test_jni_and_flutter_rust_bridge(self) -> None:
        kotlin = "sym:android/app/src/main/kotlin/com/example/player/NativeCore.kt:NativeCore"
        self.link(f"{kotlin}.start", f"{FFI}:Java_com_example_player_NativeCore_start", "jni")
        self.link(f"{kotlin}.stop", f"{FFI}:Java_com_example_player_NativeCore_stop", "jni")
        self.link("sym:lib/src/rust/api/config.dart:validateConfig", "sym:rust/src/api/config.rs:validate_config", "frb")

    def test_system_hand_offs_are_possible_links(self) -> None:
        swift = "sym:macos/Runner/ExtensionController.swift:ExtensionController"
        provider = "sym:macos/Extension/ExtensionProvider.swift:ExtensionProvider"
        self.link(f"{swift}.connect", f"{provider}.startTunnel", "network-extension", "inferred")
        self.link(f"{swift}.disconnect", f"{provider}.stopTunnel", "network-extension", "inferred")
        kotlin = "sym:android/app/src/main/kotlin/com/example/player"
        self.link(f"{kotlin}/MainActivity.kt:MainActivity.startPlayback", f"{kotlin}/PlaybackService.kt:PlaybackService."
                  "onStartCommand", "android-intent", "inferred")

    def test_links_survive_the_map_file(self) -> None:
        again = ActualMap.from_dict(json.loads(json.dumps(self.map.to_dict())))
        self.assertEqual(len(bridges.bridge_edges(again)), len(bridges.bridge_edges(self.map)))
        self.assertEqual(bridges.gaps(again)[0].bridge["missing"], "Kotlin (android)")

    def test_web_payload_carries_links_and_gaps(self) -> None:
        from pmap.web import build_payload

        payload = build_payload(self.map, None, "flutter_player")
        rows = [e for e in payload["edges"] if e.get("br")]
        self.assertTrue(any(e["br"] == {"v": "channel", "n": "audio/player", "m": "connect",
                                        "to": ["macos/Runner/PlayerChannel.swift", 24]} for e in rows))
        self.assertEqual(payload["bridges"]["gaps"][0]["missing"], "Kotlin (android)")
        self.assertIn("channel", [g["via"] for g in payload["bridges"]["groups"]])

    def test_overview_and_check_name_the_links_and_the_gap(self) -> None:
        text = format_agent_context(self.map, max_chars=3000)
        self.assertIn("Links between languages", text)
        self.assertIn('[GAP] channel audio/player: "status"', text)
        self.assertIn("JNI: Kotlin → Rust (NativeCore.start, NativeCore.stop)", text)
        check = bridges.format_check(self.map)
        self.assertIn("[!] GAPS", check)
        self.assertEqual(bridges.check_dict(self.map)["gaps"][0]["method"], "status")


@needs("dart", "kotlin", "swift")
class TestPlatformGuard(unittest.TestCase):
    """A call made only on some platforms (`if (Platform.isIOS)`) is no gap on the others."""

    def test_guarded_call_is_not_a_gap(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "app"
        shutil.copytree(FIXTURES / "flutter_player", root)
        dart = root / "lib/player/platform_player.dart"
        dart.write_text(dart.read_text(encoding="utf-8").replace(
            "Future<String> status() async => await _control.invokeMethod<String>('status') ?? 'unknown';",
            "Future<String> status() async {\n    if (!Platform.isMacOS && !Platform.isWindows) return 'unknown';\n"
            "    return await _control.invokeMethod<String>('status') ?? 'unknown';\n  }"), encoding="utf-8")
        actual = ProjectScanner().scan_repository(root)
        self.assertEqual(bridges.gaps(actual), [])


@needs("swift")
class TestExtensionOfTheSamePlatform(unittest.TestCase):
    """An app starts its own extension: with an iOS and a macOS provider, each app reaches the one beside it."""

    def test_each_app_reaches_its_own_provider(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        src = FIXTURES / "flutter_player/macos"
        for platform in ("macos", "ios"):
            for rel in ("Runner/ExtensionController.swift", "Extension/ExtensionProvider.swift"):
                (tmp / platform / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src / rel, tmp / platform / rel)
        links = [e for e in ProjectScanner().scan_repository(tmp).edges
                 if e.bridge and e.bridge["via"] == "network-extension"]
        self.assertEqual(len(links), 4)  # start and stop, from each app
        for e in links:
            platform = e.from_id.split(":")[1].split("/")[0]
            self.assertTrue(e.to_id.startswith(f"sym:{platform}/Extension/ExtensionProvider.swift:"), e.to_id)
            self.assertEqual(e.bridge["to"]["path"], f"{platform}/Extension/ExtensionProvider.swift")


@needs("dart", "kotlin", "swift")
class TestPluginStyleChannels(BridgeCase):
    """Written like real plugins: the method name goes through helpers (`_send('pause', id)` → an extension `call`
    → `invokeMethod(method)`), one file serves two channels, Swift aliases `call.method`."""

    project = "plugin_channels"
    DART = "sym:lib/src/player_platform.dart:PlayerPlatform"
    KT = "sym:android/src/main/kotlin/dev/example/player/PlayerPlugin.kt:PlayerPlugin"
    SWIFT = "sym:darwin/Classes/PlayerPlugin.swift:PlayerPlugin"

    def test_method_names_through_helpers(self) -> None:
        e = self.link(f"{self.DART}.pause", f"{self.KT}.methodHandler", "channel")
        self.assertEqual((e.bridge["name"], e.bridge["method"], e.bridge["to"]["line"]), ("dev.example/player", "pause", 34))
        self.link(f"{self.DART}.init", f"{self.KT}.globalMethodHandler", "channel")

    def test_each_channel_gets_its_own_handler(self) -> None:
        self.link(f"{self.DART}.pause", f"{self.SWIFT}.handleAsync", "channel")  # via addMethodCallDelegate → handle
        self.link(f"{self.DART}.init", f"{self.SWIFT}.handleGlobalMethodCall", "channel")
        self.assertNoEdge(f"{self.DART}.init", f"{self.KT}.methodHandler")

    def test_gap_on_the_platform_without_the_branch(self) -> None:
        gaps = bridges.gaps(self.map)
        self.assertEqual([(g.bridge["method"], g.bridge["missing"]) for g in gaps], [("resume", "Swift")])


@needs("dart", "kotlin", "swift")
class TestPigeon(BridgeCase):
    """Pigeon's generated `*.g.dart` stays on the map, and a call reaches the app's implementation of the API."""

    project = "pigeon_player"

    def test_generated_api_reaches_each_implementation(self) -> None:
        dart = "sym:lib/src/messages.g.dart:PlayerHostApi.connect"
        e = self.link(dart, "sym:ios/Runner/PlayerApi.swift:PlayerApi.connect", "pigeon")
        self.assertEqual((e.bridge["name"], e.bridge["method"]), ("PlayerHostApi", "connect"))
        self.link(dart, "sym:android/app/src/main/kotlin/dev/example/player/PlayerApi.kt:PlayerApi.connect", "pigeon")

    def test_app_code_calls_the_generated_api(self) -> None:
        self.assertEdge("sym:lib/main.dart:connectTo", "sym:lib/src/messages.g.dart:PlayerHostApi.connect", "resolved")


@needs("typescript", "rust")
class TestTauri(BridgeCase):
    project = "tauri_player"

    def test_invoke_reaches_the_command(self) -> None:
        self.link("sym:src/player.ts:connect", "sym:src-tauri/src/main.rs:connect_stream", "tauri")
        self.link("sym:src/player.ts:status", "sym:src-tauri/src/main.rs:player_status", "tauri")


@needs("swift", "rust")
class TestUniFFI(BridgeCase):
    project = "uniffi_player"

    def test_generated_swift_reaches_rust(self) -> None:
        gen = "sym:ios/Generated/player_core.swift"
        self.link(f"{gen}:connect", "sym:rust/src/lib.rs:connect", "uniffi")
        self.link(f"{gen}:PlayerClient.status", "sym:rust/src/lib.rs:PlayerClient.status", "uniffi")


CONTRACTS = FIXTURES / "contracts_player"
NATIVE_CORE = "sym:android/app/src/main/kotlin/dev/example/player/NativeCore.kt:NativeCore"
JNI_RS = "sym:rust/src/jni.rs:Java_dev_example_player_NativeCore"
CONTROL_KT = "android/app/src/main/kotlin/dev/example/player/ControlHandler.kt"


def _of_kind(actual: ActualMap, kind: str) -> list:
    return [e for e in bridges.mismatches(actual) if e.bridge["mismatch"]["kind"] == kind]


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestContracts(BridgeCase):
    """Both ends of a link are known and do not agree. `contracts_player` (an audio player) holds one case of each
    kind, and beside each the cases that must stay silent: equal counts, `(void)`, a variadic function, a macro
    argument, a Swift trailing closure, Kotlin overloads, a type alias pmap does not follow, a platform without the
    feature, a channel used on one platform only."""

    project = "contracts_player"

    def test_one_of_each_kind(self) -> None:
        kinds = sorted(e.bridge["mismatch"]["kind"] for e in bridges.mismatches(self.map))
        self.assertEqual(kinds, ["c-arity", "c-arity", "channel-name", "event-channel-name", "jni-arity", "jni-type"])
        self.assertEqual(len(bridges.mismatch_list(self.map)), 5)  # the two sites of the C function make one entry

    def test_jni_parameter_count(self) -> None:
        e = self.link(f"{NATIVE_CORE}.setVolume", f"{JNI_RS}_setVolume", "jni")
        self.assertEqual(e.bridge["mismatch"], {"kind": "jni-arity", "detail": {
            "label": "NativeCore.setVolume", "lang": "kotlin", "def_lang": "rust", "receiver": "this", "declares": 2,
            "takes": 1}})
        # a member of a Kotlin `object`: an instance method, so the second JNI parameter is the object, not a class
        self.assertEqual(bridges.mismatch_text(e), "JNI NativeCore.setVolume: Kotlin declares 2 parameters "
                                                   "(android/app/src/main/kotlin/dev/example/player/NativeCore.kt:14), "
                                                   "Rust takes 1 after env and this (rust/src/jni.rs:23)")

    def test_jni_type_that_cannot_be_the_declared_one(self) -> None:
        e = self.link(f"{NATIVE_CORE}.load", f"{JNI_RS}_load", "jni")
        self.assertEqual(e.bridge["mismatch"]["kind"], "jni-type")
        self.assertEqual(e.bridge["mismatch"]["detail"]["params"],
                         [{"index": 1, "declares": "uri: String", "takes": "uri: jboolean"}])
        self.assertIn("Kotlin declares parameter 1 as uri: String", bridges.mismatch_text(e))

    def test_jni_silent_when_equal_or_unsure(self) -> None:
        # start: `JString<'local>` and a trailing comma; seek: Rust `i64` is `jlong`; setRate: `Rate` is an alias;
        # gain: two Kotlin overloads share one JNI name; version: a C++ export behind JNIEXPORT / JNICALL
        for name in ("start", "stop", "seek", "setRate", "gain", "gain#2", "version"):
            edges = [e for e in self.map.edges if e.from_id == f"{NATIVE_CORE}.{name}" and e.bridge]
            self.assertTrue(edges, name)
            self.assertFalse([e for e in edges if e.bridge.get("mismatch")], name)

    def test_c_abi_parameter_count(self) -> None:
        [entry] = [m for m in bridges.mismatch_list(self.map) if m["kind"] == "c-arity"]
        self.assertEqual(entry["text"], "C ABI player_core_open: Rust takes 2 parameters (rust/src/ffi.rs:7); "
                                        "native/player_core.h:10 declares 1; ios/Runner/CoreBridge.swift:6 passes 1")
        # the header declaration is no call: it has no edge of its own, the function's links name it
        self.assertFalse([e for e in self.map.edges if e.from_id == "sym:native/player_core.h:player_core_open"])
        header = [{"at": "native/player_core.h:10", "lang": "cpp", "count": 1}]
        call = self.link("sym:ios/Runner/CoreBridge.swift:CoreBridge.open", f"{FFI}:player_core_open", "ffi")
        self.assertEqual(call.bridge["mismatch"]["detail"], {"def_lang": "rust", "takes": 2, "declarations": header,
                                                             "role": "passes", "lang": "swift", "count": 1})
        binding = self.link("sym:lib/core/native_core.dart:NativeCore.open", f"{FFI}:player_core_open", "ffi")
        self.assertEqual(binding.bridge["mismatch"]["detail"], {"def_lang": "rust", "takes": 2,
                                                                "declarations": header})  # its own count agrees
        self.assertEqual(bridges.mismatch_text(binding), "C ABI player_core_open: Rust takes 2 parameters "
                                                         "(rust/src/ffi.rs:7); native/player_core.h:10 declares 1")

    def test_c_abi_silent_when_equal_or_unsure(self) -> None:
        self.assertEqual({e.bridge["name"] for e in _of_kind(self.map, "c-arity")}, {"player_core_open"})
        # a macro argument, a trailing closure, a variadic C function, Dart typedefs, `lookup<NativeFunction<…>>`
        # and `@Native` with the right count
        for src in ("sym:native/player_glue.cpp:glue_rewind", "sym:ios/Runner/CoreBridge.swift:CoreBridge.listen",
                    "sym:lib/core/native_core.dart:NativeCore.log", "sym:lib/core/native_core.dart:NativeCore.open",
                    "sym:lib/core/native_core.dart:NativeCore.seek", "sym:lib/core/native_core.dart:playerCorePosition",
                    "sym:native/player_glue.cpp:glue_close"):
            edges = [e for e in self.map.edges if e.from_id == src and e.bridge and e.bridge["via"] == "ffi"]
            self.assertTrue(edges, src)
            self.assertFalse([e for e in edges if e.bridge.get("mismatch", {}).get("detail", {}).get("role")], src)

    def test_event_channel_registered_under_another_name(self) -> None:
        [e] = _of_kind(self.map, "event-channel-name")
        self.assertEqual((e.from_id, e.bridge["name"], e.bridge["missing"], e.confidence),
                         ("sym:lib/player/player_channel.dart:PlayerChannel", "player/levels", "Swift (ios)",
                          "unresolved"))
        self.assertEqual(e.bridge["mismatch"]["detail"], {"platform": "ios", "registered": "player/level", "others": 0})
        self.assertEqual(bridges.mismatch_text(e), "event channel player/levels (lib/player/player_channel.dart:11): "
                                                   "no iOS code in this project registers this name; iOS code "
                                                   "registers player/level, which no Dart code listens to "
                                                   "(ios/Runner/StatsChannel.swift:8)")
        # iOS has no visualizer at all: its one orphan stands beside the closest name only
        self.assertNotIn("player/visualizer", {m.bridge["name"] for m in bridges.mismatches(self.map)})
        self.assertEqual(bridges.gaps(self.map)[0].bridge["name"], "player/control")  # not a gap of its own

    def test_method_channel_registered_under_another_name(self) -> None:
        [e] = _of_kind(self.map, "channel-name")
        self.assertEqual((e.bridge["name"], e.bridge["mismatch"]["detail"]["registered"], e.bridge["to"]),
                         ("player/media_library", "player/medialibrary",
                          {"path": "ios/Runner/LibraryChannel.swift", "line": 6}))
        self.assertIn("no iOS code in this project registers a handler under this name; iOS code handles "
                      "player/medialibrary, which no Dart code calls", bridges.mismatch_text(e))
        self.assertNotIn("player/focus", {m.bridge["name"] for m in bridges.mismatches(self.map)})  # Android only

    def test_platform_without_the_feature_stays_silent(self) -> None:
        # the equalizer is Android's only: iOS registers nothing in the `eq` namespace, so nothing is said about it
        for name in ("eq/settings", "eq/bands"):
            self.assertTrue([e for e in bridges.bridge_edges(self.map) if e.bridge["name"] == name
                             and e.confidence == "resolved" and e.bridge["to"]["path"].startswith("android/")], name)
            self.assertFalse([e for e in bridges.problems(self.map) if e.bridge["name"] == name], name)

    def test_gap_names_a_branch_no_dart_code_sends(self) -> None:
        gaps = {g.bridge["method"]: g for g in bridges.gaps(self.map)}
        self.assertEqual(sorted(gaps), ["release", "shuffle"])
        self.assertEqual(gaps["release"].bridge["orphan"], {"method": "relase", "path": CONTROL_KT, "line": 24,
                                                            "platform": "android"})
        self.assertNotIn("orphan", gaps["shuffle"].bridge)  # every iOS branch is a method Dart sends
        self.assertTrue(bridges.describe(gaps["release"]).endswith(
            f'(Android handles "relase" at {CONTROL_KT}:24, which no Dart code sends)'))

    def test_check_overview_payload_and_map_file(self) -> None:
        from pmap.web import build_payload

        check = bridges.format_check(self.map)
        self.assertIn("[!] SIGNATURES AND NAMES THAT DIFFER", check)
        self.assertIn(f'does not handle it ({CONTROL_KT}:17) (Android handles "relase" at {CONTROL_KT}:24, which no '
                      'Dart code sends)', check)
        # the header declaration that differs is no call: the links counted stay those of the calls
        self.assertTrue(check.startswith("Links between languages: 39 (C ABI 13, channel 12, JNI 9, event channel 5)"),
                        check)
        self.assertEqual(len(bridges.problems(self.map)), 8)  # 2 gaps and 6 edges whose sides differ
        self.assertNotIn("[OK]", check)
        data = bridges.check_dict(self.map)
        self.assertEqual((len(data["gaps"]), len(data["mismatches"])), (2, 5))
        lines = bridges.summary_lines(self.map)
        self.assertEqual([line.split(" ", 1)[0] for line in lines[:6]], ["[GAP]"] * 2 + ["[MISMATCH]"] * 4)
        self.assertEqual(lines[5], "[MISMATCH] +2 more: `pmap check` lists them")  # the overview keeps 3
        self.assertTrue(lines[6].startswith("channel eq/settings: Dart → Kotlin"), lines[6])
        self.assertIn("[MISMATCH] JNI NativeCore.setVolume: Kotlin declares 2 parameters",
                      format_agent_context(self.map, max_chars=4000))
        payload = build_payload(self.map, None, "contracts_player")
        self.assertEqual(len(payload["bridges"]["mismatches"]), 5)
        self.assertEqual(payload["bridges"]["gaps"][1]["orphan"]["method"], "relase")
        again = ActualMap.from_dict(json.loads(json.dumps(self.map.to_dict())))
        self.assertEqual(bridges.mismatch_list(again), bridges.mismatch_list(self.map))
        e = self.link(f"{NATIVE_CORE}.setVolume", f"{JNI_RS}_setVolume", "jni")
        self.assertIn(" — JNI NativeCore.setVolume: Kotlin declares 2", bridges.describe(e))  # `pmap show`


def _scan_contracts(case: unittest.TestCase, *edits: tuple) -> ActualMap:
    """A copy of `contracts_player` with edits (path, old text or "" to append, new text), scanned."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, True)
    root = tmp / "app"
    shutil.copytree(CONTRACTS, root, ignore=shutil.ignore_patterns("._*"))
    for rel, old, new in edits:
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        case.assertEqual(text.count(old), 1 if old else text.count(old), (rel, old))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.replace(old, new, 1) if old else text + new, encoding="utf-8")
    return ProjectScanner().scan_repository(root)


@needs("kotlin", "rust")
class TestStrictCheckFailsOnMismatches(unittest.TestCase):
    """`pmap check --strict` fails a CI job on a signature whose two sides differ, as it does on a gap."""

    def check(self, rust_params: str) -> int:
        from pmap.cli import main

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        kt = tmp / "android/src/main/kotlin/dev/example/player/NativeCore.kt"
        kt.parent.mkdir(parents=True)
        kt.write_text("package dev.example.player\n\nobject NativeCore {\n"
                      "    external fun setVolume(level: Int, fadeMs: Int)\n}\n", encoding="utf-8")
        rs = tmp / "rust/src/jni.rs"
        rs.parent.mkdir(parents=True)
        rs.write_text("use jni::objects::JClass;\nuse jni::sys::jint;\nuse jni::JNIEnv;\n\n#[no_mangle]\n"
                      "pub extern \"system\" fn Java_dev_example_player_NativeCore_setVolume(_env: JNIEnv, _this: JClass, "
                      f"{rust_params}) {{}}\n", encoding="utf-8")
        with mock.patch("sys.stdout"):
            return main(["check", str(tmp), "--strict"])

    def test_mismatch_fails_strict_and_agreement_passes(self) -> None:
        self.assertEqual(self.check("level: jint"), 1)
        self.assertEqual(self.check("level: jint, fade_ms: jint"), 0)


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestContractsWhenUnsure(unittest.TestCase):
    """Copies of `contracts_player` with one change each: a new difference is named, anything uncertain is not."""

    def scan(self, *edits: tuple) -> ActualMap:
        return _scan_contracts(self, *edits)

    def kinds(self, actual: ActualMap) -> list:
        return sorted(m["kind"] for m in bridges.mismatch_list(actual))

    def test_dart_binding_with_another_count(self) -> None:
        actual = self.scan(("lib/core/native_core.dart", "Int32 Function(Pointer<Utf8> path, Uint32 flags)",
                            "Int32 Function(Pointer<Utf8> path)"),
                           ("lib/core/native_core.dart", "int Function(Pointer<Utf8> path, int flags)",
                            "int Function(Pointer<Utf8> path)"))
        [entry] = [m for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"]
        self.assertIn("lib/core/native_core.dart:19 declares 1", entry["text"])

    def test_every_site_in_check_three_in_the_overview(self) -> None:
        actual = self.scan(("lib/core/native_core.dart", "Int32 Function(Pointer<Utf8> path, Uint32 flags)",
                            "Int32 Function(Pointer<Utf8> path)"),
                           ("lib/core/native_core.dart", "int Function(Pointer<Utf8> path, int flags)",
                            "int Function(Pointer<Utf8> path)"),
                           ("native/player_glue.cpp", "", "\nint32_t glue_open() {\n  return player_core_open(\"x\");\n}\n"))
        [entry] = [m for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"]
        self.assertEqual(len(entry["sites"]), 4)
        self.assertIn(entry["text"], bridges.format_check(actual))
        [line] = [s for s in bridges.summary_lines(actual) if s.startswith("[MISMATCH] C ABI")]
        self.assertEqual(line, "[MISMATCH] " + entry["short"])
        self.assertTrue(line.endswith("ios/Runner/CoreBridge.swift:6 passes 1; +1 more"), line)

    def test_header_without_prototype_is_unknown(self) -> None:
        actual = self.scan(("native/player_core.h", "int32_t player_core_open(const char *path);",
                            "int32_t player_core_open();"))
        [entry] = [m for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"]
        self.assertEqual([s["at"] for s in entry["sites"]], ["ios/Runner/CoreBridge.swift:6"])

    def test_declarations_that_disagree_or_a_macro_silence_the_function(self) -> None:
        second = self.scan(("native/player_core_v2.h", "", "int32_t player_core_open(const char *path, "
                                                            "uint32_t flags);\n"))
        self.assertNotIn("c-arity", self.kinds(second))
        macro = self.scan(("native/player_core.h", "int32_t player_core_open(const char *path);",
                           "int32_t player_core_open(const char *path);\n"
                           "#define player_core_open(path) player_core_open_flags(path, 0)"))
        self.assertNotIn("c-arity", self.kinds(macro))

    def test_channel_name_unknown_on_either_side(self) -> None:
        dart = self.scan(("lib/player/player_channel.dart", "class PlayerChannel {",
                          "EventChannel channelFor(String name) => EventChannel(name);\n\nclass PlayerChannel {"))
        self.assertNotIn("event-channel-name", self.kinds(dart))
        self.assertIn("channel-name", self.kinds(dart))
        ios = self.scan(("ios/Runner/StatsChannel.swift", "  static func register(",
                         "  static func spectrum(with messenger: FlutterBinaryMessenger, id: String) {\n"
                         "    let channel = FlutterEventChannel(name: \"player/\" + id, binaryMessenger: messenger)\n"
                         "    channel.setStreamHandler(LevelStream())\n  }\n\n  static func register("))
        self.assertNotIn("event-channel-name", self.kinds(ios))

    def test_call_made_on_one_platform_only(self) -> None:
        focus_mode = ("ios/Runner/FocusChannel.swift", "",
                      "import Flutter\n\nfinal class FocusChannel: NSObject {\n"
                      "  static func register(with messenger: FlutterBinaryMessenger) {\n"
                      "    let channel = FlutterMethodChannel(name: \"player/foucs\", binaryMessenger: messenger)\n"
                      "    channel.setMethodCallHandler { call, result in\n"
                      "      if call.method == \"request\" { result(true) } else { result(FlutterMethodNotImplemented) }\n"
                      "    }\n  }\n}\n")
        guarded = self.scan(focus_mode)  # Dart asks for focus behind `Platform.isAndroid`
        self.assertEqual([m["name"] for m in bridges.mismatch_list(guarded) if m["kind"] == "channel-name"],
                         ["player/media_library"])
        unguarded = self.scan(focus_mode, ("lib/player/player_channel.dart", "if (Platform.isAndroid) {", "{"))
        self.assertEqual(sorted(m["name"] for m in bridges.mismatch_list(unguarded) if m["kind"] == "channel-name"),
                         ["player/focus", "player/media_library"])

    def test_registered_name_some_dart_code_uses(self) -> None:
        actual = self.scan(("lib/player/player_channel.dart", "class PlayerChannel {",
                            "const String legacyLevelChannel = 'player/level';\n\nclass PlayerChannel {"))
        self.assertNotIn("event-channel-name", self.kinds(actual))

    def test_unsent_branch_needs_every_dart_send_known(self) -> None:
        private = self.scan(("lib/player/player_channel.dart",
                             "Future<void> release() => _control.invokeMethod('release');",
                             "Future<void> release() => _send('release');\n\n"
                             "  Future<void> _send(String method) => _control.invokeMethod(method);"))
        self.assertEqual([g.bridge.get("orphan", {}).get("method") for g in bridges.gaps(private)
                          if g.bridge["method"] == "release"], ["relase"])
        public = self.scan(("lib/player/player_channel.dart", "Future<void> pause() =>",
                            "Future<void> send(String method) => _control.invokeMethod(method);\n\n"
                            "  Future<void> pause() =>"))
        self.assertEqual([g.bridge["method"] for g in bridges.gaps(public)], ["shuffle", "release"])
        self.assertFalse([g for g in bridges.gaps(public) if g.bridge.get("orphan")])

    def test_comment_inside_a_parameter_list(self) -> None:
        # squashed onto one line, `// a, b` would add parameters: the text without comments is counted instead
        actual = self.scan(("rust/src/jni.rs", "    uri: JString<'local>,\n",
                            "    uri: JString<'local>, // the track, a file path or a URL\n"),
                           ("native/player_core.h", "void player_core_seek(int64_t position_ms, int32_t whence);",
                            "void player_core_seek(int64_t position_ms /* from the start, in ms */, int32_t whence);"),
                           ("android/app/src/main/kotlin/dev/example/player/NativeCore.kt",
                            "external fun seek(positionMs: Long)", "external fun seek(\n        positionMs: Long, "
                                                                     "// from the start, in ms\n    )"))
        self.assertEqual(self.kinds(actual), ["c-arity", "channel-name", "event-channel-name", "jni-arity",
                                              "jni-type"])
        [entry] = [m for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"]
        self.assertEqual(entry["name"], "player_core_open")

    def test_a_header_that_agrees_adds_no_link(self) -> None:
        original = self.scan()
        fixed = self.scan(("native/player_core.h", "int32_t player_core_open(const char *path);",
                           "int32_t player_core_open(const char *path, uint32_t flags);"))
        [entry] = [m for m in bridges.mismatch_list(fixed) if m["kind"] == "c-arity"]
        self.assertEqual([s["at"] for s in entry["sites"]], ["ios/Runner/CoreBridge.swift:6"])
        self.assertEqual(bridges.check_dict(fixed)["links"], bridges.check_dict(original)["links"])

    def test_cpp_call_that_may_mean_a_namespaced_function(self) -> None:
        actual = self.scan(("native/player_glue.cpp", "", "\nnamespace glue {\n"
                                                           "int32_t player_core_open(const char *path) { return 0; }\n"
                                                           "void reopen() { player_core_open(\"current\"); }\n"
                                                           "}  // namespace glue\n"))
        [entry] = [m for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"]
        self.assertEqual([s["at"] for s in entry["sites"]], ["native/player_core.h:10",
                                                             "ios/Runner/CoreBridge.swift:6"])

    def test_channel_of_another_package(self) -> None:
        # a plugin in the same repository registers a near name in the same namespace: its own, not the app's
        actual = self.scan(("plugins/spectrum/pubspec.yaml", "", "name: spectrum\n"),
                           ("plugins/spectrum/ios/Classes/SpectrumPlugin.swift", "",
                            "import Flutter\n\nfinal class SpectrumPlugin {\n"
                            "  static func register(with messenger: FlutterBinaryMessenger) {\n"
                            "    let channel = FlutterEventChannel(name: \"player/visualiser\", binaryMessenger: "
                            "messenger)\n    channel.setStreamHandler(LevelStream())\n  }\n}\n"))
        [e] = [e for e in bridges.mismatches(actual) if e.bridge["mismatch"]["kind"] == "event-channel-name"]
        self.assertEqual((e.bridge["name"], e.bridge["mismatch"]["detail"]["others"]), ("player/levels", 0))

    def test_name_quoted_in_a_file_pmap_does_not_read(self) -> None:
        actual = self.scan(("ios/Runner/LevelsChannel.m", "",
                            "#import <Flutter/Flutter.h>\n\nvoid RegisterLevels(NSObject<FlutterBinaryMessenger> *m) {\n"
                            "  FlutterEventChannel *c = [FlutterEventChannel eventChannelWithName:@\"player/levels\"\n"
                            "                                                     binaryMessenger:m];\n}\n"))
        self.assertNotIn("event-channel-name", self.kinds(actual))
        self.assertIn("channel-name", self.kinds(actual))


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


_STREAM_SWIFT = ("final class LevelStream: NSObject, FlutterStreamHandler {\n"
                 "  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink)"
                 " -> FlutterError? {\n    return nil\n  }\n\n"
                 "  func onCancel(withArguments arguments: Any?) -> FlutterError? {\n    return nil\n  }\n}\n")
_LEVELS_DART = ("import 'package:flutter/services.dart';\n\nclass Stats {\n"
                "  static const EventChannel _levels = EventChannel('player/levels');\n\n"
                "  Stream<dynamic> levels() => _levels.receiveBroadcastStream();\n}\n")
_LEVEL_SWIFT = ("import Flutter\n\nfinal class StatsChannel {\n"
                "  static func register(with messenger: FlutterBinaryMessenger) {\n"
                "    let level = FlutterEventChannel(name: \"player/level\", binaryMessenger: messenger)\n"
                "    level.setStreamHandler(LevelStream())\n  }\n}\n\n" + _STREAM_SWIFT)
_CRATE = '[package]\nname = "player_core"\nversion = "0.1.0"\n\n[lib]\ncrate-type = ["cdylib", "staticlib"]\n'


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestContractsStaySilent(unittest.TestCase):
    """Cases where a difference would be a guess: the project leaves a doubt, so nothing is reported. Each is a small
    audio player project, or a copy of `contracts_player` with one change."""

    def project(self, files: dict) -> ActualMap:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        _write(tmp / "app", files)
        return ProjectScanner().scan_repository(tmp / "app")

    def contracts(self, *edits: tuple) -> ActualMap:
        return _scan_contracts(self, *edits)

    def kinds(self, actual: ActualMap) -> list:
        return sorted(m["kind"] for m in bridges.mismatch_list(actual))

    def c_abi(self, actual: ActualMap) -> dict:
        return {m["name"]: [f"{s['at']} {s['role']} {s['count']}" for s in m["sites"]]
                for m in bridges.mismatch_list(actual) if m["kind"] == "c-arity"}

    # ---------------------------------------------------------------- the C ABI

    def test_c_caller_whose_argument_is_a_macro(self) -> None:
        # C files are language "c": the macro rules of C++ hold for them too
        actual = self.contracts(("native/player_rewind.c", "",
                                 "#include \"player_core.h\"\n\n#define REWIND_ARGS 0, 0\n\n"
                                 "void rewind_track(void) {\n  player_core_seek(REWIND_ARGS);\n}\n"),
                                ("native/player_rewind.c", "", "\nvoid seek_start(void) {\n  player_core_seek(0);\n}\n"),
                                ("native/player_seek_compat.c", "",
                                 "/* Older callers pass only the position. */\n"
                                 "#define player_core_seek(ms) (player_core_seek)((ms), 0)\n"))
        self.assertEqual(list(self.c_abi(actual)), ["player_core_open"])

    def test_lowercase_macro_or_a_condition_inside_the_arguments(self) -> None:
        actual = self.contracts(("native/player_glue.cpp", "", "\n#define seek_args 0, 0\n\n"
                                 "void glue_seek_start() {\n  player_core_seek(seek_args);\n}\n\n"
                                 "void glue_seek_gapless(int64_t ms) {\n  player_core_seek(ms, 0\n"
                                 "#ifdef PLAYER_GAPLESS\n                   , 1\n#endif\n  );\n}\n"))
        self.assertEqual(list(self.c_abi(actual)), ["player_core_open"])

    def test_function_like_macro_in_a_header_pmap_reads_for_nothing_else(self) -> None:
        actual = self.project({
            "rust/Cargo.toml": _CRATE,
            "rust/src/lib.rs": "#[no_mangle]\npub extern \"C\" fn core_seek(position_ms: i64, whence: i32) {}\n",
            "native/compat.h": "#pragma once\n// Older callers pass only the position.\n"
                               "#define core_seek(ms) core_seek_from_start(ms)\n",
            "native/use.cpp": "#include \"compat.h\"\n\nvoid rewind_all() {\n  core_seek(0);\n}\n"})
        self.assertEqual(len([e for e in bridges.bridge_edges(actual) if e.bridge["via"] == "ffi"]), 1)
        self.assertEqual(self.kinds(actual), [])

    def test_parameter_present_only_under_a_condition(self) -> None:
        actual = self.contracts(("rust/src/ffi.rs", "pub extern \"C\" fn player_core_close() {}",
                                 "pub extern \"C\" fn player_core_close(#[cfg(feature = \"fade\")] fade_ms: u32) {}"),
                                ("native/player_core.h", "void player_core_seek(int64_t position_ms, int32_t whence);",
                                 "void player_core_seek(int64_t position_ms, int32_t whence\n#if PLAYER_GAPLESS\n"
                                 "                      , int32_t gapless\n#endif\n);"))
        self.assertEqual(list(self.c_abi(actual)), ["player_core_open"])

    def test_definition_the_parser_missed(self) -> None:
        # another library defines the name too, but its own definition sits in a file the parser could not read
        actual = self.project({
            "libs/zipkit/zip_compat.c": "/* A second zip library. */\nint zip_first_entry(void *file, int flags) {\n"
                                        "  (void)file;\n  return flags;\n}\n",
            "libs/minizip/unzip.c": "#include \"unzip.h\"\n\nextern int ZEXPORT zip_close_entry(void *file)\n{\n"
                                    "    int err = 0;\n#ifdef HAVE_AES\n    if (file == 0)\n    {\n        err = 1;\n"
                                    "#else\n    if (file == 0) {\n        err = 2;\n#endif\n    }\n    return err;\n}\n\n"
                                    "extern int ZEXPORT zip_first_entry(void *file)\n{\n    return file == 0;\n}\n",
            "Sources/Player/Playlist.swift": "final class Playlist {\n  func rewind(_ zip: UnsafeMutableRawPointer)"
                                             " -> Int32 {\n    return zip_first_entry(zip)\n  }\n}\n"})
        self.assertIn("libs/minizip/unzip.c", [e.path for e in actual.errors if e.kind == "partial_parse"])
        self.assertEqual(self.kinds(actual), [])

    def test_two_copies_of_the_api_header(self) -> None:
        header = "#pragma once\nint zip_first_entry(void *file, int flags);\n"
        files = {"libs/zipkit/zip.c": "#include \"zip.h\"\n\nint zip_first_entry(void *file, int flags) {\n"
                                      "  (void)file;\n  return flags;\n}\n",
                 "libs/zipkit/zip.h": header,
                 "Sources/Player/Playlist.swift": "final class Playlist {\n  func rewind(_ zip: UnsafeMutableRawPointer)"
                                                  " -> Int32 {\n    return zip_first_entry(zip)\n  }\n}\n"}
        one = self.project(files)
        self.assertEqual(list(self.c_abi(one)), ["zip_first_entry"])  # one library: the call differs
        two = self.project(dict(files, **{"third_party/zip/include/zip.h": header}))
        self.assertEqual(self.kinds(two), [])

    def test_swift_local_function_of_the_same_name(self) -> None:
        actual = self.project({
            "libs/codec/codec_util.h": "#pragma once\n#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n"
                                       "static inline int scale_volume(int level, int numerator, int denominator) {\n"
                                       "    return level * numerator / denominator;\n}\n\n"
                                       "#ifdef __cplusplus\n}\n#endif\n",
            "Sources/Player/Volume.swift": "struct VolumeControl {\n    func apply(_ level: Int) -> Int {\n"
                                           "        func scale_volume(_ x: Int) -> Int {\n            return x / 2\n"
                                           "        }\n        return scale_volume(level)\n    }\n}\n"})
        self.assertEqual(self.kinds(actual), [])
        closure = self.contracts(("ios/Runner/CoreBridge.swift", "  func open(path: String) -> Bool {",
                                  "  let player_core_open: (String) -> Int32 = { _ in 0 }\n\n"
                                  "  func open(path: String) -> Bool {"))
        self.assertEqual(self.c_abi(closure), {"player_core_open": ["native/player_core.h:10 declares 1"]})

    def test_header_that_nothing_binds_to_the_definition(self) -> None:
        files = {"rust/Cargo.toml": _CRATE,
                 "rust/src/lib.rs": "use std::ffi::c_void;\n\n#[no_mangle]\n"
                                    "pub extern \"C\" fn engine_free(handle: *mut c_void, flags: u32) {}\n",
                 "desktop/third_party/mixer/include/mixer.h": "// A prebuilt mixer library (libmixer.a).\n#pragma once\n"
                                                              "void *engine_new(void);\nvoid engine_free(void *handle);\n"}
        alone = self.project(files)
        self.assertEqual(bridges.check_dict(alone), {"links": 0, "gaps": [], "mismatches": []})
        called = dict(files, **{"ios/Runner/Engine.swift": "final class Engine {\n  func close(_ h: OpaquePointer) {\n"
                                                           "    engine_free(h, 0)\n  }\n}\n"})
        self.assertEqual(self.kinds(self.project(called)), [])  # Swift does not see that header
        bridged = dict(called, **{"ios/Runner/Runner-Bridging-Header.h":
                                  "#include \"../../desktop/third_party/mixer/include/mixer.h\"\n"})
        self.assertEqual(self.c_abi(self.project(bridged)),
                         {"engine_free": ["desktop/third_party/mixer/include/mixer.h:4 declares 1"]})

    def test_project_files_are_read_only_when_a_difference_is_found(self) -> None:
        with mock.patch.object(bridges._Linker, "c_index", autospec=True,
                               side_effect=bridges._Linker.c_index) as index:
            ProjectScanner().scan_repository(FIXTURES / "flutter_player")
            self.assertEqual(index.call_count, 0)
            ProjectScanner().scan_repository(CONTRACTS)
            self.assertEqual(index.call_count, 1)
            self.assertEqual(index.call_args[0][1], {"player_core_open"})

    # ---------------------------------------------------------------- JNI

    def test_suspend_and_context_receivers_and_static_natives(self) -> None:
        actual = self.project({
            "android/app/src/main/kotlin/dev/example/audio/Engine.kt":
                "package dev.example.audio\n\nobject Engine {\n    external suspend fun prepare(a: Int): Int\n\n"
                "    context(Session)\n    external fun tag(a: Int)\n}\n",
            "android/app/src/main/java/dev/example/audio/Native.java":
                "package dev.example.audio;\n\npublic class Native {\n"
                "  public static native int open(String path, int flags);\n  public native void close(int fadeMs);\n}\n",
            "rust/Cargo.toml": _CRATE,
            "rust/src/lib.rs": "use jni::objects::JObject;\nuse jni::sys::jint;\nuse jni::JNIEnv;\n\n#[no_mangle]\n"
                               "pub extern \"system\" fn Java_dev_example_audio_Engine_prepare(_env: JNIEnv, _: JObject,"
                               " a: jint, cont: JObject) -> JObject { cont }\n\n#[no_mangle]\n"
                               "pub extern \"system\" fn Java_dev_example_audio_Engine_tag(_env: JNIEnv, _: JObject,"
                               " session: JObject, a: jint) {}\n",
            "android/app/src/main/cpp/native.c":
                "#include <jni.h>\n\nJNIEXPORT jint JNICALL Java_dev_example_audio_Native_open(JNIEnv *env, jclass cls,"
                " jstring path) { return 0; }\n"
                "JNIEXPORT void JNICALL Java_dev_example_audio_Native_close(JNIEnv *env, jobject thiz) {}\n"})
        texts = [m["text"] for m in bridges.mismatch_list(actual)]
        self.assertEqual(texts, [
            "JNI Native.close: Java declares 1 parameter (android/app/src/main/java/dev/example/audio/Native.java:5), "
            "C takes 0 after env and this (android/app/src/main/cpp/native.c:4)",
            "JNI Native.open: Java declares 2 parameters (android/app/src/main/java/dev/example/audio/Native.java:4), "
            "C takes 1 after env and class (android/app/src/main/cpp/native.c:3)"])

    def test_primitives_of_one_size(self) -> None:
        actual = self.project({
            "android/app/src/main/kotlin/dev/example/Mixer.kt":
                "package dev.example\n\nobject Mixer {\n    external fun mute(on: Boolean)\n\n"
                "    external fun channel(index: Short)\n\n    external fun gain(level: Int)\n\n"
                "    external fun pan(position: Float)\n}\n",
            "rust/Cargo.toml": _CRATE,
            "rust/src/lib.rs": "use jni::objects::JObject;\nuse jni::JNIEnv;\n\n"
                               + "".join(f"#[no_mangle]\npub extern \"system\" fn Java_dev_example_Mixer_{n}(_env: JNIEnv,"
                                         f" _this: JObject, {p}) {{}}\n\n"
                                         for n, p in (("mute", "on: i8"), ("channel", "index: u16"),
                                                      ("gain", "level: u32"), ("pan", "position: i32")))})
        [entry] = bridges.mismatch_list(actual)
        self.assertEqual(entry["detail"]["params"], [{"index": 1, "declares": "position: Float",
                                                      "takes": "position: i32"}])

    # ---------------------------------------------------------------- channels

    def test_dart_channel_name_built_at_run_time(self) -> None:
        actual = self.project({
            "pubspec.yaml": "name: player\n",
            "lib/stats.dart": _LEVELS_DART.replace(
                "\n}\n", "\n\n  Stream<dynamic> meter(String kind) => "
                         "EventChannel('player/$kind').receiveBroadcastStream();\n}\n"),
            "ios/Runner/StatsChannel.swift": _LEVEL_SWIFT})
        self.assertEqual(self.kinds(actual), [])

    def test_dart_code_this_pass_does_not_read(self) -> None:
        files = {"pubspec.yaml": "name: player\n", "lib/stats.dart": _LEVELS_DART,
                 "ios/Runner/StatsChannel.swift": _LEVEL_SWIFT}
        self.assertEqual(self.kinds(self.project(files)), ["event-channel-name"])
        generated = dict(files, **{"lib/stats.g.dart": "// GENERATED CODE - DO NOT MODIFY BY HAND\n"
                                                       "part of 'stats.dart';\n\n"
                                                       "const EventChannel _$masterLevel = EventChannel('player/level');"
                                                       "\n"})
        self.assertEqual(self.kinds(self.project(generated)), [])
        skipped = dict(files, **{"lib/src/env/master_meter.dart": "import 'package:flutter/services.dart';\n\n"
                                                                  "const masterLevel = EventChannel('player/level');\n"})
        self.assertEqual(self.kinds(self.project(skipped)), [])
        # the note beside a gap too: a generated Dart file sends the branch's name
        actual = self.contracts(("lib/player/control.g.dart", "", "// GENERATED CODE - DO NOT MODIFY BY HAND\n"
                                                                 "const legacyRelease = 'relase';\n"))
        self.assertEqual([g.bridge.get("orphan") for g in bridges.gaps(actual)], [None, None])

    def test_name_registered_outside_the_platform_folders(self) -> None:
        package = self.project({
            "pubspec.yaml": "name: player\n", "lib/stats.dart": _LEVELS_DART,
            "ios/Runner/StatsChannel.swift": _LEVEL_SWIFT,
            "PlayerKit/Sources/PlayerKit/Levels.swift":
                "import Flutter\n\npublic enum PlayerKit {\n"
                "  public static func register(with messenger: FlutterBinaryMessenger) {\n"
                "    let levels = FlutterEventChannel(name: \"player/levels\", binaryMessenger: messenger)\n"
                "    levels.setStreamHandler(LevelStream())\n  }\n}\n"})
        self.assertEqual(self.kinds(package), [])
        self.assertEqual([(e.from_id, e.bridge["via"], e.confidence) for e in bridges.bridge_edges(package)],
                         [("sym:lib/stats.dart:Stats.levels", "event-channel", "resolved")])
        host = self.project({
            "flutter_module/pubspec.yaml": "name: player_module\n", "flutter_module/lib/stats.dart": _LEVELS_DART,
            "flutter_module/host/app/src/main/java/com/acme/web/LevelsBridge.kt":
                "package com.acme.web\n\nimport io.flutter.plugin.common.BinaryMessenger\n"
                "import io.flutter.plugin.common.EventChannel\n\nobject LevelsBridge {\n"
                "    fun attach(messenger: BinaryMessenger) {\n"
                "        EventChannel(messenger, \"player/level\").setStreamHandler(LevelHandler())\n    }\n}\n\n"
                "class LevelHandler : EventChannel.StreamHandler {\n"
                "    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {}\n"
                "    override fun onCancel(arguments: Any?) {}\n}\n",
            "flutter_module/host/app/src/main/java/com/acme/player/Streams.kt":
                "package com.acme.player\n\nimport io.flutter.plugin.common.BinaryMessenger\n"
                "import io.flutter.plugin.common.EventChannel\n\nobject Streams {\n"
                "    fun attach(messenger: BinaryMessenger) {\n"
                "        EventChannel(messenger, \"player/levels\").setStreamHandler(com.acme.web.LevelHandler())\n"
                "    }\n}\n"})
        self.assertEqual(self.kinds(host), [])  # `com/acme/web` is a Kotlin package, not the web platform
        self.assertEqual(bridges.platform_of("host/app/src/main/java/com/acme/web/A.kt", "kotlin"), "kotlin")
        self.assertEqual(bridges.platform_of("android/app/src/main/kotlin/dev/web/A.kt", "kotlin"), "android")

    def test_channels_of_a_namespace_whose_names_are_not_near(self) -> None:
        routes = ("import Flutter\n\n// Route changes for the native settings screen.\nfinal class Routes {\n"
                  "  static func register(with messenger: FlutterBinaryMessenger) {\n"
                  "    FlutterEventChannel(name: \"player/airplay_routes\", binaryMessenger: messenger)"
                  ".setStreamHandler(LevelStream())\n"
                  "    FlutterEventChannel(name: \"player/carplay_state\", binaryMessenger: messenger)"
                  ".setStreamHandler(LevelStream())\n  }\n}\n\n" + _STREAM_SWIFT)
        actual = self.project({"pubspec.yaml": "name: player\n", "lib/stats.dart": _LEVELS_DART,
                               "ios/Runner/Routes.swift": routes})
        self.assertEqual(self.kinds(actual), [])
        near = self.project({"pubspec.yaml": "name: player\n", "lib/stats.dart": _LEVELS_DART,
                             "ios/Runner/Routes.swift": routes.replace("player/carplay_state", "player/level")
                                                        .replace("player/airplay_routes", "player/levels_db")})
        [entry] = bridges.mismatch_list(near)
        self.assertTrue(entry["text"].endswith("which no Dart code listens to (ios/Runner/Routes.swift:7) (and 1 more "
                                               "channel of that namespace that no Dart code uses)"), entry["text"])

    def test_method_name_built_at_run_time(self) -> None:
        actual = self.project({
            "pubspec.yaml": "name: player\n",
            "lib/control.dart": "import 'package:flutter/services.dart';\n\nclass Control {\n"
                                "  static const MethodChannel _control = MethodChannel('player/control');\n\n"
                                "  Future<void> play() => _control.invokeMethod('play');\n\n"
                                "  Future<void> stop() => _control.invokeMethod('stop');\n\n"
                                "  Future<void> setMode(String mode) => _control.invokeMethod('set${mode}Mode');\n}\n",
            "android/app/src/main/kotlin/dev/example/ControlHandler.kt":
                "package dev.example\n\nimport io.flutter.plugin.common.BinaryMessenger\n"
                "import io.flutter.plugin.common.MethodCall\nimport io.flutter.plugin.common.MethodChannel\n\n"
                "class ControlHandler(messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {\n"
                "    private val channel = MethodChannel(messenger, \"player/control\")\n\n"
                "    init {\n        channel.setMethodCallHandler(this)\n    }\n\n"
                "    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {\n"
                "        when (call.method) {\n            \"play\" -> result.success(null)\n"
                "            \"setRepeatMode\" -> result.success(null)\n"
                "            \"setShuffleMode\" -> result.success(null)\n"
                "            else -> result.notImplemented()\n        }\n    }\n}\n"})
        self.assertEqual([(g.bridge["method"], g.bridge.get("orphan")) for g in bridges.gaps(actual)],
                         [("stop", None)])  # what `set${mode}Mode` sends is known at run time only
        self.assertIsNone(bridges._method_literal("'set${mode}Mode'", "dart"))
        self.assertEqual(bridges._method_literal("r'cost$'", "dart"), "cost$")
        self.assertEqual(bridges._method_literal("'cost\\$'", "dart"), "cost\\$")
        self.assertIsNone(bridges._method_literal('"set\\(mode)"', "swift"))

    def test_dart_plugin_class_runs_on_its_platform_only(self) -> None:
        actual = self.project({
            "pubspec.yaml": "name: player_notify\n\nflutter:\n  plugin:\n    platforms:\n      android:\n"
                            "        package: dev.example.notify\n        pluginClass: NotifyPlugin\n"
                            "        dartPluginClass: AndroidNotify  # loaded on Android only\n      macos:\n"
                            "        pluginClass: NotifyPlugin\n      web:\n        pluginClass: NotifyWeb\n"
                            "        fileName: src/notify_web.dart\n",
            "lib/notify.dart": "import 'package:flutter/services.dart';\n\nclass Notify {\n"
                               "  static const MethodChannel _channel = MethodChannel('player/notify');\n\n"
                               "  Future<void> show() => _channel.invokeMethod('show');\n\n"
                               "  Future<void> cancel() => _channel.invokeMethod('cancel');\n}\n",
            "lib/src/android_notify.dart": "import 'package:flutter/services.dart';\n\nclass AndroidNotify {\n"
                                           "  static const MethodChannel _channel = MethodChannel('player/notify');\n\n"
                                           "  Future<void> exactAlarms() => _channel.invokeMethod('requestExactAlarms');"
                                           "\n}\n",
            "lib/src/notify_web.dart": "import 'package:flutter/services.dart';\n\nclass NotifyWeb {\n"
                                       "  static const MethodChannel _channel = MethodChannel('player/notify');\n\n"
                                       "  Future<void> permission() => _channel.invokeMethod('webPermission');\n}\n",
            "android/src/main/kotlin/dev/example/notify/NotifyPlugin.kt":
                "package dev.example.notify\n\nimport io.flutter.plugin.common.MethodCall\n"
                "import io.flutter.plugin.common.MethodChannel\n\n"
                "class NotifyPlugin : MethodChannel.MethodCallHandler {\n"
                "    private lateinit var channel: MethodChannel\n\n"
                "    fun attach(messenger: io.flutter.plugin.common.BinaryMessenger) {\n"
                "        channel = MethodChannel(messenger, \"player/notify\")\n"
                "        channel.setMethodCallHandler(this)\n    }\n\n"
                "    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {\n"
                "        when (call.method) {\n            \"show\" -> result.success(null)\n"
                "            \"cancel\" -> result.success(null)\n"
                "            \"requestExactAlarms\" -> result.success(true)\n"
                "            else -> result.notImplemented()\n        }\n    }\n}\n",
            "macos/Classes/NotifyPlugin.swift":
                "import FlutterMacOS\n\npublic class NotifyPlugin: NSObject, FlutterPlugin {\n"
                "  public static func register(with registrar: FlutterPluginRegistrar) {\n"
                "    let channel = FlutterMethodChannel(name: \"player/notify\", binaryMessenger: registrar.messenger)\n"
                "    let instance = NotifyPlugin()\n"
                "    registrar.addMethodCallDelegate(instance, channel: channel)\n  }\n\n"
                "  public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {\n"
                "    switch call.method {\n    case \"show\":\n      result(nil)\n"
                "    default:\n      result(FlutterMethodNotImplemented)\n    }\n  }\n}\n"})
        self.assertEqual([(g.bridge["method"], g.bridge["missing"]) for g in bridges.gaps(actual)],
                         [("cancel", "Swift (macos)")])
        self.assertEqual(bridges._pubspec_platforms("flutter:\n  plugin:\n    platforms:\n      ios:\n"
                                                    "        dartPluginClass: 'IOSNotify'\n"
                                                    "dependencies:\n  flutter:\n    sdk: flutter\n"),
                         {"ios": {"dartPluginClass": "IOSNotify"}})

    # ---------------------------------------------------------------- what is shown

    def test_overview_keeps_three_lines_and_numbers_sort_by_value(self) -> None:
        n = 12
        actual = self.project({
            "android/app/src/main/kotlin/dev/example/audio/Bindings.kt":
                "package dev.example.audio\n\nclass Bindings {\n"
                + "".join(f"    external fun configure{i}(a: Int, b: Int, c: Int)\n\n" for i in range(n)) + "}\n",
            "rust/Cargo.toml": _CRATE,
            "rust/src/lib.rs": "use jni::objects::JObject;\nuse jni::sys::jint;\nuse jni::JNIEnv;\n\n"
                               + "".join(f"#[no_mangle]\npub extern \"system\" fn Java_dev_example_audio_Bindings_"
                                         f"configure{i}(_env: JNIEnv, _this: JObject, value: jint) {{}}\n\n"
                                         for i in range(n))})
        found = bridges.mismatch_list(actual)
        self.assertEqual([m["name"].rsplit("_", 1)[1] for m in found][:3], ["configure0", "configure1", "configure2"])
        self.assertEqual(found[-1]["name"].rsplit("_", 1)[1], "configure11")
        lines = bridges.summary_lines(actual)
        self.assertEqual([line[:30] for line in lines[:3]], ["[MISMATCH] JNI Bindings.config"] * 3)
        self.assertEqual(lines[3], "[MISMATCH] +9 more: `pmap check` lists them")
        self.assertTrue(lines[4].startswith("JNI: Kotlin → Rust"), lines)
        self.assertEqual(len(bridges.format_check(actual).splitlines()), 3 + n)  # `pmap check` lists them all
        self.assertLess(len(format_agent_context(actual, max_chars=2000)), 2000)
        self.assertEqual(sorted(["a.c:10", "a.c:9", "a.c:100"], key=bridges._natural), ["a.c:9", "a.c:10", "a.c:100"])

    def test_header_note_does_not_name_the_header_as_a_caller(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "app"
        shutil.copytree(CONTRACTS, root, ignore=shutil.ignore_patterns("._*"))
        with mock.patch.dict(os.environ, {"PMAP_HOME": str(tmp / "home")}), \
                mock.patch.object(hints, "refresh_in_background", lambda root: None), mock.patch("sys.stdout") as out:
            run("post-edit", json.dumps({"tool_name": "Edit", "cwd": str(root), "session_id": "s1", "tool_input": {
                "file_path": str(root / "native/player_core.h"), "old_string": "x",
                "new_string": "int32_t player_core_open(const char *path);"}}))
        printed = "".join(call.args[0] for call in out.write.call_args_list)
        text = json.loads(printed)["hookSpecificOutput"]["additionalContext"] if printed.strip() else ""
        self.assertIn("rust/src/ffi.rs:7", text)
        self.assertNotIn("← native/player_core.h:10", text)


class TestComparingSides(unittest.TestCase):
    def test_parameter_lists(self) -> None:
        sig = "( mut env: JNIEnv<'local>, _class: JClass<'local>, uri: JString<'local>, ) -> jint"
        self.assertEqual(bridges._params(sig, "rust", "a.rs"), ["mut env: JNIEnv<'local>", "_class: JClass<'local>",
                                                               "uri: JString<'local>"])
        self.assertEqual(len(bridges._params('(ctx: *mut c_void, cb: extern "C" fn(*mut c_void, i32))', "rust",
                                             "a.rs")), 2)
        self.assertEqual(bridges._params("(void *ctx, void (*cb)(void *, int32_t)): void", "cpp", "a.h"),
                         ["void *ctx", "void (*cb)(void *, int32_t)"])
        self.assertEqual(bridges._params("(void): void", "cpp", "a.h"), [])
        self.assertIsNone(bridges._params("(): int", "cpp", "a.h"))  # C: no prototype
        self.assertEqual(bridges._params("(): int", "cpp", "a.cpp"), [])
        self.assertIsNone(bridges._params("(const char *fmt, ...): void", "cpp", "a.c"))
        self.assertIsNone(bridges._params("(PLAYER_ARGS): void", "cpp", "a.h"))
        self.assertEqual(bridges._params("(String... names): void", "java", "A.java"), ["String... names"])
        self.assertIsNone(bridges._params("(a: Int, b: Int", "kotlin", "A.kt"))  # cut short
        self.assertIsNone(bridges._group("(a,,b)", 0))

    def test_jni_types(self) -> None:
        kotlin = lambda t: bridges._jvm_category(t, "kotlin")  # noqa: E731
        rust = lambda t: bridges._jni_category(t, "rust")  # noqa: E731
        self.assertTrue(bridges._contradicts(kotlin("String"), rust("jboolean")))
        self.assertTrue(bridges._contradicts(kotlin("Int"), rust("jni::sys::jlong")))
        self.assertTrue(bridges._contradicts(kotlin("Int?"), rust("jint")))  # boxed: an Integer object
        self.assertTrue(bridges._contradicts(kotlin("ByteArray"), rust("JString<'a>")))
        self.assertTrue(bridges._contradicts(kotlin("IntArray"), rust("JByteArray<'a>")))
        self.assertFalse(bridges._contradicts(kotlin("Long"), rust("i64")))
        self.assertFalse(bridges._contradicts(kotlin("String"), rust("JObject")))
        self.assertFalse(bridges._contradicts(kotlin("ByteArray"), rust("jarray")))
        self.assertFalse(bridges._contradicts(kotlin("Handle"), rust("jlong")))  # maybe a typealias
        # primitives compare by size and kind: the same bits pass, whatever the name
        self.assertFalse(bridges._contradicts(kotlin("Boolean"), rust("i8")))
        self.assertFalse(bridges._contradicts(kotlin("Byte"), rust("u8")))
        self.assertFalse(bridges._contradicts(kotlin("Char"), rust("i16")))
        self.assertFalse(bridges._contradicts(kotlin("Short"), rust("u16")))
        self.assertFalse(bridges._contradicts(kotlin("Int"), rust("u32")))
        self.assertTrue(bridges._contradicts(kotlin("Int"), rust("u8")))
        self.assertTrue(bridges._contradicts(kotlin("Int"), rust("f32")))
        self.assertTrue(bridges._contradicts(kotlin("Long"), bridges._jni_category("uint32_t", "cpp")))
        self.assertFalse(bridges._contradicts(kotlin("Int"), rust("usize")))  # its size depends on the target
        self.assertFalse(bridges._contradicts(kotlin("String"), rust("&JString")))
        self.assertTrue(bridges._contradicts(bridges._jvm_category("Context", "java"), rust("jint")))

    def test_each_orphan_stands_beside_one_name(self) -> None:
        paired = bridges._pair(["audio/levels", "audio/visualizer"], [("audio/level", 1)])
        self.assertEqual(paired, {"audio/levels": ("audio/level", 1, 0)})
        self.assertEqual(bridges._distance("disconect", "disconnect"), 1)


class TestCommentsAndText(unittest.TestCase):
    def test_comments_are_blanked_and_strings_kept(self) -> None:
        src = 'a = "http://x" // MethodChannel("no")\n/* invokeMethod("no") */ b = \'c\'\n'
        out = bridges._blank_comments(src)
        self.assertEqual(out.count("\n"), 2)
        self.assertIn('"http://x"', out)
        self.assertNotIn("MethodChannel", out)
        self.assertNotIn("invokeMethod", out)

    def test_jni_names(self) -> None:
        self.assertEqual(bridges._jni_escape("com/my_app/Outer$Inner"), "com_my_1app_Outer_00024Inner")
        self.assertEqual(bridges._lower_camel(["crate", "api", "my_mod", "MyType", "do_it"]),
                         "crateApiMyModMyTypeDoIt")


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestPostEditNote(unittest.TestCase):
    """`pmap hook post-edit`: what an agent is told after Claude Code's Edit or Codex's apply_patch."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "flutter_player"
        shutil.copytree(FIXTURES / "flutter_player", self.root)
        env = mock.patch.dict(os.environ, {"PMAP_HOME": str(self.tmp / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        quiet = mock.patch.object(hints, "refresh_in_background", lambda root: None)
        quiet.start()
        self.addCleanup(quiet.stop)

    def note(self, payload: dict) -> str:
        payload.setdefault("cwd", str(self.root))
        payload.setdefault("session_id", "s1")
        with mock.patch("sys.stdout") as out:
            run("post-edit", json.dumps(payload))
        printed = "".join(call.args[0] for call in out.write.call_args_list)
        return json.loads(printed)["hookSpecificOutput"]["additionalContext"] if printed.strip() else ""

    def edit(self, rel: str, new: str, old: str = "x") -> dict:
        return {"tool_name": "Edit", "tool_input": {"file_path": str(self.root / rel), "old_string": old,
                                                     "new_string": new}}

    def test_dart_channel_call_names_every_platform_and_the_caller(self) -> None:
        text = self.note(self.edit("lib/player/platform_player.dart",
                                   "  Future<bool> connect(String server, String coreVersion) async {"))
        self.assertIn("PlatformPlayer.connect (lib/player/platform_player.dart:16)", text)
        self.assertIn("macos/Runner/PlayerChannel.swift:24  PlayerChannel.handle", text)
        self.assertIn("android/app/src/main/kotlin/com/example/player/MainActivity.kt:17", text)
        self.assertIn("windows/runner/player_channel.cpp:15", text)
        self.assertIn("lib/player/player_controller.dart:22  PlayerController.connect", text)
        # once per session
        self.assertEqual(self.note(self.edit("lib/player/platform_player.dart",
                                             "  Future<bool> connect(String server, String coreVersion) async {")), "")

    def test_branch_of_a_handler_names_its_callers_only(self) -> None:
        text = self.note(self.edit("macos/Runner/PlayerChannel.swift", "      result(playback.status())"))
        self.assertIn("lib/player/platform_player.dart:23  PlatformPlayer.status", text)
        self.assertNotIn("PlatformPlayer.connect", text)

    def test_status_gap_is_reported(self) -> None:
        text = self.note(self.edit("lib/player/platform_player.dart",
                                   "  Future<String> status() async => await _control.invokeMethod<String>('status') ?? "
                                   "'unknown';"))
        self.assertIn('"status" is not handled in Kotlin (android)', text)

    def test_codex_patch_on_the_rust_core(self) -> None:
        patch = ("*** Begin Patch\n*** Update File: rust/src/ffi.rs\n@@\n"
                 "     let text = unsafe { CStr::from_ptr(profile) }.to_string_lossy();\n"
                 "-    match config::parse(&text) {\n+    match config::parse(&text) {\n*** End Patch")
        text = self.note({"tool_name": "apply_patch", "tool_input": {"command": patch}, "session_id": "codex"})
        self.assertIn("player_core_start (rust/src/ffi.rs:15)", text)
        self.assertIn("macos/Runner/ExtensionController.swift:8  ExtensionController.connect  (Swift, C ABI", text)
        self.assertIn("windows/runner/player_channel.cpp:18", text)

    def test_c_header_and_definition_point_at_each_other(self) -> None:
        text = self.note(self.edit("rust/src/ffi.rs",
                                   "pub extern \"C\" fn player_core_start(profile: *const c_char) -> i32 {"))
        self.assertIn("native/player_core.h:10  declares player_core_start", text)
        text = self.note(self.edit("native/player_core.h", "int32_t player_core_start(const char *config);"))
        self.assertIn("rust/src/ffi.rs:15  player_core_start  (Rust, the definition of this declaration)", text)
        self.assertIn("macos/Runner/ExtensionController.swift:8  ExtensionController.connect", text)

    def test_deleted_declaration_names_what_used_it(self) -> None:
        text = self.note(self.edit("android/app/src/main/kotlin/com/example/player/NativeCore.kt", "",
                                   old="    external fun stop()\n"))
        self.assertIn("rust/src/ffi.rs:58  Java_com_example_player_NativeCore_stop", text)
        self.assertIn("PlaybackService.kt:19  PlaybackService.onDestroy", text)

    def test_body_edit_gets_one_line(self) -> None:
        text = self.note(self.edit("rust/src/engine.rs", "    SENT.store(0, Ordering::SeqCst);"))
        self.assertIn("start (rust/src/engine.rs:9)\n  used in 2 places in 1 other file (rust/src/ffi.rs:18, "
                      "rust/src/ffi.rs:52)", text)

    def change(self, rel: str, old: str, new: str) -> dict:
        """Make the edit on disk, as the agent's tool did, and return its Edit payload."""
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1)
        path.write_text(text.replace(old, new), encoding="utf-8")
        return {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new}}

    KOTLIN = "android/app/src/main/kotlin/com/example/player/MainActivity.kt"

    def test_edit_that_closes_a_gap_says_so(self) -> None:
        hints.current_map(self.root)  # the map a session start leaves, with the "status" gap on Android
        text = self.note(self.change(self.KOTLIN, '                "disconnect" -> {',
                                     '                "status" -> result.success("stopped")\n'
                                     '                "disconnect" -> {'))
        self.assertIn('✓ gap closed: channel audio/player "status" is handled in Kotlin (android) now', text)
        self.assertNotIn("is not handled", text)

    def test_renamed_dart_call_opens_gaps_and_drops_the_old_links(self) -> None:
        hints.current_map(self.root)
        rel = "lib/player/platform_player.dart"
        old = "  Future<void> disconnect() => _control.invokeMethod('disconnect');"
        new = "  Future<void> disconnect() => _control.invokeMethod('disconect');"
        self.change(rel, old, new)
        patch = f"*** Begin Patch\n*** Update File: {rel}\n@@\n-{old}\n+{new}\n*** End Patch"
        text = self.note({"tool_name": "apply_patch", "tool_input": {"command": patch}, "session_id": "codex"})
        for platform in ("Kotlin (android)", "Swift (macos)", "C/C++ (windows)"):
            self.assertIn(f'✗ new gap: channel audio/player: "disconect" is called from Dart ({rel}:21) but {platform} '
                          "does not handle it", text)
        self.assertNotIn('"disconnect")', text)  # the links the cached map still holds are gone

    def test_jni_signature_that_starts_or_stops_differing(self) -> None:
        hints.current_map(self.root)
        rel = "rust/src/ffi.rs"
        head = 'pub extern "system" fn Java_com_example_player_NativeCore_stop(_env: JNIEnv, _class: JClass'
        text = self.note(self.change(rel, head + ") {", head + ", fade_ms: jint) {"))
        self.assertIn("✗ the two sides differ now: JNI NativeCore.stop: Kotlin declares 0 parameters", text)
        hints.current_map(self.root)
        text = self.note(dict(self.change(rel, head + ", fade_ms: jint) {", head + ") {"), session_id="s2"))
        self.assertIn("✓ the two sides agree now: JNI NativeCore.stop", text)

    def test_write_cannot_tell_what_changed(self) -> None:
        hints.current_map(self.root)
        path = self.root / self.KOTLIN
        path.write_text(path.read_text(encoding="utf-8").replace('"disconnect" -> {', '"status" -> result.success("x")\n'
                                                                  '                "disconnect" -> {'), encoding="utf-8")
        text = self.note({"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "..."}})
        self.assertNotIn("gap closed", text)

    def test_silent_without_users_elsewhere(self) -> None:
        self.assertEqual(self.note(self.edit("lib/main.dart",
                                             "    return MaterialApp(home: HomePage(controller: controller));")), "")
        self.assertEqual(self.note({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "README.md"),
                                                                        "content": "x"}}), "")


class TestTextBeforeTheEdit(unittest.TestCase):
    """The note compares the links before and after an edit: the old text is rebuilt only when that is certain."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = self.tmp / "a.kt"
        self.file.write_text("val a = 1\nval b = 2\nval c = 2\n", encoding="utf-8")

    def before(self, data: dict):
        return hints.text_before({"cwd": str(self.tmp), "tool_input": data}, self.file)

    def test_edit_multiedit_and_replace_all(self) -> None:
        self.assertEqual(self.before({"file_path": "a.kt", "old_string": "val a = 0", "new_string": "val a = 1"}),
                         "val a = 0\nval b = 2\nval c = 2\n")
        self.assertEqual(self.before({"file_path": str(self.file), "edits": [
            {"old_string": "val a = 0", "new_string": "val a = 1"}, {"old_string": "val b = 0", "new_string": "val b = 2"}]}),
            "val a = 0\nval b = 0\nval c = 2\n")
        self.assertEqual(self.before({"file_path": "a.kt", "old_string": "= 3", "new_string": "= 2", "replace_all": True}),
                         "val a = 1\nval b = 3\nval c = 3\n")

    def test_uncertain_cases_give_none(self) -> None:
        self.assertIsNone(self.before({"file_path": "a.kt", "old_string": "= 3", "new_string": "= 2"}))  # twice
        self.assertIsNone(self.before({"file_path": "a.kt", "old_string": "val d = 4\n", "new_string": ""}))
        self.assertIsNone(self.before({"file_path": "a.kt", "content": "val a = 1\n"}))  # a Write

    def test_codex_patches(self) -> None:
        patch = ("*** Begin Patch\n*** Update File: a.kt\n@@\n-val a = 0\n+val a = 1\n@@\n-val c = 3\n+val c = 2\n"
                 "*** End Patch")
        self.assertEqual(self.before({"command": patch}), "val a = 0\nval b = 2\nval c = 3\n")
        self.assertEqual(self.before({"command": "*** Begin Patch\n*** Add File: a.kt\n+val a = 1\n*** End Patch"}), "")
        self.assertIsNone(self.before({"command": "*** Begin Patch\n*** Update File: a.kt\n@@\n-x\n+= 2\n*** End Patch"}))


class TestChangesFromPayload(unittest.TestCase):
    def test_patch_hunks_and_deleted_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "a.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n", encoding="utf-8")
        patch = ("*** Begin Patch\n*** Update File: a.py\n@@ def g():\n-    return 3\n+    return 2\n"
                 "*** Delete File: b.py\n*** End Patch")
        changes = hints.changes_from({"tool_name": "apply_patch", "cwd": str(tmp), "tool_input": {"command": patch}})
        self.assertEqual([(c.path.name, c.start, c.whole_file) for c in changes], [("a.py", 6, False), ("b.py", 1, True)])


if __name__ == "__main__":
    unittest.main()
