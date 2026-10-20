"""Links between languages (pmap/langs/bridges.py) and the note an agent gets after an edit (pmap/hints.py).

`flutter_vpn` is a VPN client: Flutter UI, Swift for macOS (with a NetworkExtension tunnel), Kotlin for Android,
C++ for Windows and a Rust core reached through FFI, JNI and flutter_rust_bridge. Android deliberately does not
handle the `status` method of the control channel. `tauri_vpn` and `uniffi_vpn` cover Tauri commands and UniFFI.
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

DART = "sym:lib/vpn/platform_vpn.dart:PlatformVpn"
SWIFT = "sym:macos/Runner/VpnChannel.swift:VpnChannel.handle"
KOTLIN = "sym:android/app/src/main/kotlin/com/example/vpn/MainActivity.kt:MainActivity.configureFlutterEngine"
CPP = "sym:windows/runner/vpn_channel.cpp:VpnChannel.HandleMethodCall"
FFI = "sym:rust/src/ffi.rs"


class BridgeCase(LangCase):
    def link(self, src: str, dst: str, via: str, confidence: str = "resolved"):
        found = [e for e in self.map.edges if e.from_id == src and e.to_id == dst and e.bridge]
        self.assertTrue(found, f"no link {src} -> {dst}")
        self.assertEqual(found[0].bridge["via"], via)
        self.assertEqual(found[0].confidence, confidence, f"{src} -> {dst}")
        return found[0]


@needs("dart", "swift", "kotlin", "cpp", "rust")
class TestFlutterVpn(BridgeCase):
    project = "flutter_vpn"

    def test_channel_methods_reach_every_platform(self) -> None:
        for target, line in ((SWIFT, 24), (KOTLIN, 17), (CPP, 15)):
            e = self.link(f"{DART}.connect", target, "channel")
            self.assertEqual((e.bridge["name"], e.bridge["method"]), ("app.vpn/control", "connect"))
            self.assertEqual(e.bridge["to"]["line"], line)  # the `case "connect"` branch, not the whole handler
        self.assertEqual(e.location.path, "lib/vpn/platform_vpn.dart")

    def test_channel_name_from_a_constant(self) -> None:
        # Dart: MethodChannel(kControlChannel); Swift: name: controlName; Kotlin: MethodChannel(messenger, channelName)
        names = {e.bridge["name"] for e in bridges.bridge_edges(self.map) if e.bridge["via"] == "channel"}
        self.assertEqual(names, {"app.vpn/control"})

    def test_method_a_platform_does_not_handle_is_a_gap(self) -> None:
        gaps = bridges.gaps(self.map)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual((gap.from_id, gap.confidence), (f"{DART}.status", "unresolved"))
        self.assertEqual((gap.bridge["method"], gap.bridge["missing"]), ("status", "Kotlin (android)"))
        self.link(f"{DART}.status", SWIFT, "channel")
        self.link(f"{DART}.status", CPP, "channel")

    def test_native_calls_back_into_dart(self) -> None:
        e = self.link("sym:macos/Runner/VpnChannel.swift:VpnChannel.revoked", f"{DART}._onNativeCall", "channel")
        self.assertEqual(e.bridge["method"], "onRevoked")

    def test_event_channel_reaches_the_stream_handler(self) -> None:
        self.link(f"{DART}.states", "sym:macos/Runner/VpnChannel.swift:StateStreamHandler.onListen", "event-channel")
        # Kotlin's handler is an anonymous object: the function that registers it stands for it
        self.link(f"{DART}.states", "sym:android/app/src/main/kotlin/com/example/vpn/VpnStateStream.kt:"
                                    "VpnStateStream.attach", "event-channel")

    def test_c_abi_from_dart_swift_and_cpp(self) -> None:
        self.link("sym:lib/core/core_bindings.dart:CoreBindings.version", f"{FFI}:vpn_core_version", "ffi")
        self.link("sym:lib/core/core_bindings.dart:CoreBindings.parseConfig", f"{FFI}:vpn_core_parse_config", "ffi")
        self.link("sym:lib/core/core_bindings.dart:vpnCoreStats", f"{FFI}:vpn_core_stats", "ffi")  # @Native
        self.link("sym:macos/Runner/TunnelController.swift:TunnelController.connect", f"{FFI}:vpn_core_start", "ffi")
        self.link(CPP, f"{FFI}:vpn_core_start", "ffi")

    def test_header_declaration_gives_way_to_the_definition(self) -> None:
        self.assertNoEdge(CPP, "sym:native/vpn_core.h:vpn_core_start")
        self.assertFalse([e for e in self.map.errors if e.path == "native/vpn_core.h"])  # `extern "C"` guards parse

    def test_replaced_unknowns_are_gone(self) -> None:
        version = "sym:lib/core/core_bindings.dart:CoreBindings.version"
        self.assertNoEdge(version, "unresolved:fn")  # the looked-up function pointer
        self.assertNoEdge("sym:macos/Runner/TunnelController.swift:TunnelController.connect",
                          "unresolved:manager.connection.startVPNTunnel")

    def test_jni_and_flutter_rust_bridge(self) -> None:
        kotlin = "sym:android/app/src/main/kotlin/com/example/vpn/NativeCore.kt:NativeCore"
        self.link(f"{kotlin}.start", f"{FFI}:Java_com_example_vpn_NativeCore_start", "jni")
        self.link(f"{kotlin}.stop", f"{FFI}:Java_com_example_vpn_NativeCore_stop", "jni")
        self.link("sym:lib/src/rust/api/config.dart:validateConfig", "sym:rust/src/api/config.rs:validate_config", "frb")

    def test_system_hand_offs_are_possible_links(self) -> None:
        swift = "sym:macos/Runner/TunnelController.swift:TunnelController"
        provider = "sym:macos/PacketTunnel/PacketTunnelProvider.swift:PacketTunnelProvider"
        self.link(f"{swift}.connect", f"{provider}.startTunnel", "network-extension", "inferred")
        self.link(f"{swift}.disconnect", f"{provider}.stopTunnel", "network-extension", "inferred")
        kotlin = "sym:android/app/src/main/kotlin/com/example/vpn"
        self.link(f"{kotlin}/MainActivity.kt:MainActivity.startTunnel", f"{kotlin}/TunnelService.kt:TunnelService."
                  "onStartCommand", "android-intent", "inferred")

    def test_links_survive_the_map_file(self) -> None:
        again = ActualMap.from_dict(json.loads(json.dumps(self.map.to_dict())))
        self.assertEqual(len(bridges.bridge_edges(again)), len(bridges.bridge_edges(self.map)))
        self.assertEqual(bridges.gaps(again)[0].bridge["missing"], "Kotlin (android)")

    def test_web_payload_carries_links_and_gaps(self) -> None:
        from pmap.web import build_payload

        payload = build_payload(self.map, None, "flutter_vpn")
        rows = [e for e in payload["edges"] if e.get("br")]
        self.assertTrue(any(e["br"] == {"v": "channel", "n": "app.vpn/control", "m": "connect",
                                        "to": ["macos/Runner/VpnChannel.swift", 24]} for e in rows))
        self.assertEqual(payload["bridges"]["gaps"][0]["missing"], "Kotlin (android)")
        self.assertIn("channel", [g["via"] for g in payload["bridges"]["groups"]])

    def test_overview_and_check_name_the_links_and_the_gap(self) -> None:
        text = format_agent_context(self.map, max_chars=3000)
        self.assertIn("Links between languages", text)
        self.assertIn('[GAP] channel app.vpn/control: "status"', text)
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
        shutil.copytree(FIXTURES / "flutter_vpn", root)
        dart = root / "lib/vpn/platform_vpn.dart"
        dart.write_text(dart.read_text(encoding="utf-8").replace(
            "Future<String> status() async => await _control.invokeMethod<String>('status') ?? 'unknown';",
            "Future<String> status() async {\n    if (!Platform.isMacOS && !Platform.isWindows) return 'unknown';\n"
            "    return await _control.invokeMethod<String>('status') ?? 'unknown';\n  }"), encoding="utf-8")
        actual = ProjectScanner().scan_repository(root)
        self.assertEqual(bridges.gaps(actual), [])


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

    project = "pigeon_vpn"

    def test_generated_api_reaches_each_implementation(self) -> None:
        dart = "sym:lib/src/messages.g.dart:VpnHostApi.connect"
        e = self.link(dart, "sym:ios/Runner/VpnApi.swift:VpnApi.connect", "pigeon")
        self.assertEqual((e.bridge["name"], e.bridge["method"]), ("VpnHostApi", "connect"))
        self.link(dart, "sym:android/app/src/main/kotlin/dev/example/vpn/VpnApi.kt:VpnApi.connect", "pigeon")

    def test_app_code_calls_the_generated_api(self) -> None:
        self.assertEdge("sym:lib/main.dart:connectTo", "sym:lib/src/messages.g.dart:VpnHostApi.connect", "resolved")


@needs("typescript", "rust")
class TestTauri(BridgeCase):
    project = "tauri_vpn"

    def test_invoke_reaches_the_command(self) -> None:
        self.link("sym:src/vpn.ts:connect", "sym:src-tauri/src/main.rs:connect_vpn", "tauri")
        self.link("sym:src/vpn.ts:status", "sym:src-tauri/src/main.rs:vpn_status", "tauri")


@needs("swift", "rust")
class TestUniFFI(BridgeCase):
    project = "uniffi_vpn"

    def test_generated_swift_reaches_rust(self) -> None:
        gen = "sym:ios/Generated/vpn_core.swift"
        self.link(f"{gen}:connect", "sym:rust/src/lib.rs:connect", "uniffi")
        self.link(f"{gen}:VpnClient.status", "sym:rust/src/lib.rs:VpnClient.status", "uniffi")


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
        self.root = self.tmp / "flutter_vpn"
        shutil.copytree(FIXTURES / "flutter_vpn", self.root)
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
        text = self.note(self.edit("lib/vpn/platform_vpn.dart",
                                   "  Future<bool> connect(String server, String coreVersion) async {"))
        self.assertIn("PlatformVpn.connect (lib/vpn/platform_vpn.dart:16)", text)
        self.assertIn("macos/Runner/VpnChannel.swift:24  VpnChannel.handle", text)
        self.assertIn("android/app/src/main/kotlin/com/example/vpn/MainActivity.kt:17", text)
        self.assertIn("windows/runner/vpn_channel.cpp:15", text)
        self.assertIn("lib/vpn/vpn_controller.dart:22  VpnController.connect", text)
        # once per session
        self.assertEqual(self.note(self.edit("lib/vpn/platform_vpn.dart",
                                             "  Future<bool> connect(String server, String coreVersion) async {")), "")

    def test_branch_of_a_handler_names_its_callers_only(self) -> None:
        text = self.note(self.edit("macos/Runner/VpnChannel.swift", "      result(tunnel.status())"))
        self.assertIn("lib/vpn/platform_vpn.dart:23  PlatformVpn.status", text)
        self.assertNotIn("PlatformVpn.connect", text)

    def test_status_gap_is_reported(self) -> None:
        text = self.note(self.edit("lib/vpn/platform_vpn.dart",
                                   "  Future<String> status() async => await _control.invokeMethod<String>('status') ?? "
                                   "'unknown';"))
        self.assertIn('"status" is not handled in Kotlin (android)', text)

    def test_codex_patch_on_the_rust_core(self) -> None:
        patch = ("*** Begin Patch\n*** Update File: rust/src/ffi.rs\n@@\n"
                 "     let text = unsafe { CStr::from_ptr(profile) }.to_string_lossy();\n"
                 "-    match config::parse(&text) {\n+    match config::parse(&text) {\n*** End Patch")
        text = self.note({"tool_name": "apply_patch", "tool_input": {"command": patch}, "session_id": "codex"})
        self.assertIn("vpn_core_start (rust/src/ffi.rs:15)", text)
        self.assertIn("macos/Runner/TunnelController.swift:8  TunnelController.connect  (Swift, C ABI", text)
        self.assertIn("windows/runner/vpn_channel.cpp:18", text)

    def test_c_header_and_definition_point_at_each_other(self) -> None:
        text = self.note(self.edit("rust/src/ffi.rs", "pub extern \"C\" fn vpn_core_start(profile: *const c_char) -> i32 {"))
        self.assertIn("native/vpn_core.h:10  declares vpn_core_start", text)
        text = self.note(self.edit("native/vpn_core.h", "int32_t vpn_core_start(const char *config);"))
        self.assertIn("rust/src/ffi.rs:15  vpn_core_start  (Rust, the definition of this declaration)", text)
        self.assertIn("macos/Runner/TunnelController.swift:8  TunnelController.connect", text)

    def test_deleted_declaration_names_what_used_it(self) -> None:
        text = self.note(self.edit("android/app/src/main/kotlin/com/example/vpn/NativeCore.kt", "",
                                   old="    external fun stop()\n"))
        self.assertIn("rust/src/ffi.rs:58  Java_com_example_vpn_NativeCore_stop", text)
        self.assertIn("TunnelService.kt:19  TunnelService.onDestroy", text)

    def test_body_edit_gets_one_line(self) -> None:
        text = self.note(self.edit("rust/src/tunnel.rs", "    SENT.store(0, Ordering::SeqCst);"))
        self.assertIn("start (rust/src/tunnel.rs:9)\n  used in 2 places in 1 other file (rust/src/ffi.rs:18, "
                      "rust/src/ffi.rs:52)", text)

    def test_silent_without_users_elsewhere(self) -> None:
        self.assertEqual(self.note(self.edit("lib/main.dart",
                                             "    return MaterialApp(home: HomePage(controller: controller));")), "")
        self.assertEqual(self.note({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "README.md"),
                                                                        "content": "x"}}), "")


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
