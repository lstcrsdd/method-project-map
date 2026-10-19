"""MCP server, agent connection (in a sandboxed home), session-start hook, and the web UI endpoints."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pmap import connect as agents
from pmap.hook import project_root, run as run_hook
from pmap.mcp import PmapServer, serve_stdio
from pmap.scanner import ProjectScanner
from pmap.web import _make_handler, _State, build_payload, render_page

ROOT_DIR = Path(__file__).resolve().parent.parent
TOY_REPO = ROOT_DIR / "examples" / "toy_project"
TOY_DESIGN = ROOT_DIR / "examples" / "design.yaml"


class TestMcp(unittest.TestCase):
    def call(self, server, method, params=None, msg_id=1):
        return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})

    def test_protocol_and_tools(self) -> None:
        server = PmapServer(str(TOY_REPO))
        init = self.call(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(init["serverInfo"]["name"], "pmap")
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = {t["name"] for t in self.call(server, "tools/list")["result"]["tools"]}
        self.assertEqual(names, {"pmap_overview", "pmap_find", "pmap_show", "pmap_source", "pmap_check", "pmap_stale", "pmap_scan"})

        def tool(tool_name, **args):
            result = self.call(server, "tools/call", {"name": tool_name, "arguments": args})["result"]
            return result["isError"], result["content"][0]["text"]

        overview = tool("pmap_overview")[1]
        self.assertIn("## app/db/", overview)
        self.assertIn("- connection.py", overview)
        self.assertIn("OrderService.create_order", tool("pmap_find", name="create_order")[1])
        err, text = tool("pmap_source", ref="sym:app.db.connection:execute_raw_sql")
        self.assertFalse(err)
        self.assertIn("def execute_raw_sql", text)
        self.assertNotIn("class DatabaseConnection", text)  # only the requested symbol
        err, text = tool("pmap_show", ref="missing")
        self.assertTrue(err)
        self.assertEqual(self.call(server, "tools/call", {"name": "nope"})["error"]["code"], -32602)

    def test_check_uses_design_next_to_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "toy"
            shutil.copytree(TOY_REPO, repo)
            shutil.copy(TOY_DESIGN, repo / "design.yaml")
            server = PmapServer(str(repo))
            text = server.check({})
            self.assertIn("ui -> db", text)

    def test_stdio_transport(self) -> None:
        stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\nnot json\n')
        stdout = io.StringIO()
        serve_stdio(str(TOY_REPO), stdin=stdin, stdout=stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(replies[0], {"jsonrpc": "2.0", "id": 7, "result": {}})
        self.assertEqual(replies[1]["error"]["code"], -32700)


class SandboxHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        env = {"PMAP_USER_HOME": str(self.home), "PMAP_HOME": str(self.home / ".pmap"),
               "CLAUDE_CONFIG_DIR": str(self.home / ".claude"), "CODEX_HOME": str(self.home / ".codex")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        self._cli = mock.patch.object(agents, "_claude_cli", return_value=None)  # never touch the real CLI
        self._cli.start()

    def tearDown(self) -> None:
        self._cli.stop()
        self._env.stop()
        self._tmp.cleanup()


class TestConnect(SandboxHome):
    def test_connect_is_idempotent_and_disconnect_restores(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        original = {"model": "opus", "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        (claude / "settings.json").write_text(json.dumps(original), encoding="utf-8")
        (claude / "CLAUDE.md").write_text("# My rules\n", encoding="utf-8")
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('model = "x"\n', encoding="utf-8")

        agents.connect(claude=True, codex=True)
        agents.connect(claude=True, codex=True)
        settings = json.loads((claude / "settings.json").read_text())
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(len(settings["hooks"]["SessionStart"]), 2)
        self.assertEqual((claude / "CLAUDE.md").read_text().count("pmap:start"), 1)
        self.assertEqual((codex / "config.toml").read_text().count("[mcp_servers.pmap]"), 1)
        self.assertTrue((claude / "skills" / "project-map" / "SKILL.md").exists())
        self.assertNotIn("{{PMAP}}", (claude / "skills" / "project-map" / "SKILL.md").read_text())
        self.assertTrue(agents.status()["claude"]["connected"])
        self.assertTrue(agents.status()["codex"]["connected"])
        self.assertTrue((claude / "settings.json.bak-pmap").exists())

        agents.disconnect(claude=True, codex=True)
        self.assertEqual(json.loads((claude / "settings.json").read_text()), original)
        self.assertEqual((claude / "CLAUDE.md").read_text(), "# My rules\n")
        self.assertEqual((codex / "config.toml").read_text(), 'model = "x"\n')
        self.assertFalse(agents.status()["claude"]["connected"])

    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_launcher_ignores_a_pmap_package_in_the_project(self) -> None:
        launcher = agents.install_runtime()
        project = self.home / "project"
        (project / "pmap").mkdir(parents=True)
        (project / "pmap" / "__init__.py").write_text('raise SystemExit("project pmap imported")\n')
        (project / "json.py").write_text('raise SystemExit("project json imported")\n')
        result = subprocess.run([str(launcher), "--help"], cwd=project, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: pmap", result.stdout)

    def test_language_grammars_go_to_their_own_environment(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["-m", "venv"]:  # pretend the environment was created
                python = agents.venv_python()
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(agents.subprocess, "run", side_effect=fake_run):
            report = agents.install_languages(log=lambda *_: None)
        self.assertEqual(calls[0][1:3], ["-m", "venv"])
        self.assertIn("tree-sitter-typescript", calls[1])
        self.assertTrue(any("Runtime" in line for line in report))
        if os.name != "nt":  # the launcher now runs on the environment's Python
            self.assertIn(str(agents.venv_python()), agents.launcher_path().read_text())

    def test_existing_unmanaged_codex_server_is_left_alone(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('[mcp_servers.pmap]\ncommand = "custom"\n', encoding="utf-8")
        report = agents.connect(claude=False, codex=True)
        self.assertTrue(any("left unchanged" in line for line in report))
        self.assertEqual((codex / "config.toml").read_text().count("[mcp_servers.pmap]"), 1)


class TestHook(SandboxHome):
    def capture(self, payload: dict) -> str:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(run_hook("session-start", json.dumps(payload)), 0)
        return out.getvalue()

    def test_overview_in_python_project(self) -> None:
        text = self.capture({"cwd": str(TOY_REPO)})
        context = json.loads(text)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Method Project Map (pmap)", context)
        self.assertIn("app/db/", context)
        self.assertLess(len(context), 10000)  # Claude Code caps hook context at 10k characters

    def test_silent_outside_projects(self) -> None:
        empty = self.home / "notes"
        empty.mkdir()
        (empty / "todo.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.capture({"cwd": str(empty)}), "")
        self.assertEqual(self.capture({"cwd": "/definitely/missing/dir"}), "")
        with mock.patch("os.getcwd", return_value=str(empty)), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(run_hook("session-start", "not json"), 0)  # bad input falls back to the cwd
        self.assertEqual(out.getvalue(), "")

    def test_project_root_without_markers(self) -> None:
        repo = self.home / "work" / "svc"
        (repo / "pkg").mkdir(parents=True)
        (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        self.assertEqual(project_root(repo), repo.resolve())


class TestWeb(SandboxHome):
    def test_payload_and_static_page(self) -> None:
        from pmap.loader import find_design

        actual = ProjectScanner().scan_repository(TOY_REPO)
        payload = build_payload(actual, find_design(TOY_REPO, str(TOY_DESIGN)), "toy")
        self.assertEqual(payload["design"]["divergences"][0][:2], ["ui", "db"])
        self.assertTrue(any(e.get("v") for e in payload["edges"]))  # divergent edges are flagged
        html = render_page(payload, live=False).decode()
        self.assertIn('"project":"toy_project"', html)
        self.assertNotIn("__PMAP_", html)

    def test_live_server_endpoints_and_csrf_guard(self) -> None:
        state = _State(TOY_REPO, None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn(state.token, page)
            graph = json.loads(urllib.request.urlopen(base + "/api/graph").read())
            self.assertGreater(len(graph["nodes"]), 10)
            src = json.loads(urllib.request.urlopen(base + "/api/source?id=sym:app.models.order:Order").read())
            self.assertIn("class Order", src["text"])

            def post(headers):
                req = urllib.request.Request(base + "/api/connect", data=b'{"claude": true}', headers=headers, method="POST")
                try:
                    return urllib.request.urlopen(req).status
                except urllib.error.HTTPError as exc:
                    return exc.code

            self.assertEqual(post({"Content-Type": "application/json"}), 403)
            self.assertEqual(post({"Content-Type": "text/plain", "X-Pmap-Token": state.token}), 403)
            self.assertEqual(post({"Content-Type": "application/json", "X-Pmap-Token": state.token,
                                   "Origin": "https://evil.example"}), 403)
            self.assertEqual(post({"Content-Type": "application/json", "X-Pmap-Token": state.token}), 200)
            self.assertTrue((self.home / ".claude" / "skills" / "project-map" / "SKILL.md").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()


    def test_switch_project_reveal_and_pick(self) -> None:
        from pmap.web import desktop

        state = _State(ROOT_DIR, None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        ok_headers = {"Content-Type": "application/json", "X-Pmap-Token": state.token}

        def post(path, body, headers=ok_headers):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b"{}")

        try:
            with mock.patch.object(desktop, "reveal") as reveal, \
                    mock.patch.object(desktop, "pick_folder", return_value=None) as pick:
                # every action that runs a local program needs the page token
                for path in ("/api/open", "/api/pick", "/api/reveal"):
                    self.assertEqual(post(path, {}, {"Content-Type": "application/json"})[0], 403)
                self.assertEqual(post("/api/open", {"path": str(self.home)})[0], 400)  # no Python files
                self.assertEqual(post("/api/open", {"path": "relative/path"})[0], 400)
                self.assertEqual(post("/api/open", {"path": str(TOY_REPO / "app" / "models" / "order.py")})[0], 400)

                status, body = post("/api/open", {"path": str(TOY_REPO)})
                self.assertEqual((status, body["name"]), (200, "toy_project"))
                graph = json.loads(urllib.request.urlopen(base + "/api/graph").read())
                self.assertEqual(graph["project"], "toy_project")
                project = json.loads(urllib.request.urlopen(base + "/api/project").read())
                self.assertEqual(project["recent"][0], str(TOY_REPO.resolve()))  # kept in the sandboxed ~/.pmap

                self.assertEqual(post("/api/reveal", {"id": "sym:app.models.order:Order"})[0], 200)
                reveal.assert_called_with(TOY_REPO.resolve() / "app" / "models" / "order.py")
                self.assertEqual(post("/api/reveal", {})[0], 200)
                reveal.assert_called_with(TOY_REPO.resolve())
                self.assertEqual(post("/api/reveal", {"id": "sym:no.such:Thing"})[0], 404)
                self.assertEqual(post("/api/reveal", {"path": "app/models"})[0], 200)
                self.assertEqual(post("/api/reveal", {"path": "../.."})[0], 403)
                self.assertEqual(post("/api/open", {"path": str(self.home)})[1]["code"], "no_python")

                self.assertEqual(post("/api/pick", {"prompt": "x"}), (200, {"cancelled": True}))
                pick.return_value = ROOT_DIR
                self.assertEqual(post("/api/pick", {})[1]["path"], str(ROOT_DIR.resolve()))
        finally:
            httpd.shutdown()
            httpd.server_close()



class TestDesktop(SandboxHome):
    """`pmap ui` and the app shortcut; nothing here opens a window or touches the real home folder."""

    def test_idle_server_stops_and_cleans_up(self) -> None:
        from pmap.web import serve

        ui = self.home / ".pmap" / "ui.json"
        result = {}
        thread = threading.Thread(target=lambda: result.update(code=serve(TOY_REPO, port=0, idle_exit=0.4, ui_file=ui)))
        with contextlib.redirect_stdout(io.StringIO()):
            thread.start()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "the idle server did not stop")
        self.assertEqual(result["code"], 0)
        self.assertFalse(ui.exists())

    def test_second_launch_reuses_the_running_server(self) -> None:
        from pmap import web

        state = _State(ROOT_DIR, None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        web._write_private(web.ui_file(), json.dumps({"url": url, "token": state.token, "pid": -1}))
        try:
            with mock.patch.object(web.webbrowser, "open") as browser, mock.patch.object(web, "serve") as new_server:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(web.launch_ui(str(TOY_REPO)), 0)
                browser.assert_called_once_with(url)
                new_server.assert_not_called()
            self.assertEqual(state.repo_root, TOY_REPO.resolve())  # switched through the page token
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_start_project_prefers_the_last_one(self) -> None:
        from pmap import web

        with mock.patch.object(web.desktop, "pick_folder") as pick:
            web.desktop.remember_project(TOY_REPO.resolve())
            self.assertEqual(web.start_project(None), TOY_REPO.resolve())
            pick.assert_not_called()
        (self.home / ".pmap" / "recent.json").unlink()
        with mock.patch.object(web.desktop, "can_pick_folder", return_value=True), \
                mock.patch.object(web.desktop, "pick_folder", return_value=None):
            self.assertIsNone(web.start_project(None))  # the user cancelled the folder dialog
        with mock.patch.object(web.desktop, "can_pick_folder", return_value=False), \
                mock.patch.object(Path, "cwd", return_value=TOY_REPO):
            self.assertEqual(web.start_project(None), TOY_REPO.resolve())

    def test_shortcut_on_each_platform(self) -> None:
        from pmap.web import desktop

        agents.install_runtime()
        with mock.patch.object(desktop, "platform_name", return_value="linux"), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "share")}):
            entry = desktop.install_shortcut()
            self.assertIn(f'Exec="{agents.launcher_path()}" ui', entry.read_text())
            self.assertEqual(desktop.remove_shortcut(), entry)
            self.assertFalse(entry.exists())

        def fake_osacompile(cmd, **kwargs):
            (Path(cmd[cmd.index("-o") + 1]) / "Contents" / "Resources").mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(desktop, "platform_name", return_value="mac"), \
                mock.patch.object(desktop.subprocess, "run", side_effect=fake_osacompile) as run:
            app = desktop.install_shortcut()
            self.assertEqual(app, self.home / "Applications" / "Method Project Map.app")
            self.assertIn(str(agents.launcher_path()), run.call_args[0][0][-1])
            desktop.install_shortcut()  # our own app is replaced
            desktop.remove_shortcut()
            self.assertFalse(app.exists())
            (app / "Contents").mkdir(parents=True)  # someone else's app with the same name stays
            with self.assertRaises(RuntimeError):
                desktop.install_shortcut()
            self.assertTrue(app.exists())

        with mock.patch.object(desktop, "platform_name", return_value="windows"), \
                mock.patch.dict(os.environ, {"APPDATA": str(self.home / "AppData")}), \
                mock.patch.object(desktop.subprocess, "run") as run:
            link = desktop.install_shortcut()
            self.assertEqual(link.name, "Method Project Map.lnk")
            env = run.call_args[1]["env"]
            self.assertEqual(env["PMAP_LNK"], str(link))
            self.assertTrue(env["PMAP_LNK_ARGS"].startswith("-I -c ") and env["PMAP_LNK_ARGS"].endswith(" ui"))


if __name__ == "__main__":
    unittest.main()
