"""Interactive map in the browser: `pmap serve` (live, local) and `pmap html` (one self-contained file).

Standard library only; the page itself has no external dependencies, so it works offline on any platform.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from pmap import PMF_VERSION, __version__
from pmap.loader import MapCache, find_design
from pmap.models import ActualMap, DesignMap
from pmap.query import resolve_ref, source_of
from pmap.reflexion import check_reflexion
from pmap.web import desktop

STATIC = Path(__file__).resolve().parent / "static"
_DEP_KINDS = ("imports", "calls", "inherits")
_POST_PATHS = ("/api/connect", "/api/disconnect", "/api/open", "/api/pick", "/api/reveal")
MAX_PROJECT_FILES = 20000  # a folder with more source files than this is not one project (home, a disk)
IDLE_EXIT = 15 * 60  # `pmap ui` stops this long after the last request from an open page (the page polls every 4 s)


def _package_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


def build_payload(actual: ActualMap, design: Optional[DesignMap], repo_name: str) -> Dict[str, Any]:
    """Everything the page needs, in a compact shape (contains-edges are implied by `parent`)."""
    nodes = []
    unresolved: Dict[str, list] = {}
    for e in actual.edges:
        if e.kind in _DEP_KINDS and e.confidence == "unresolved":
            unresolved.setdefault(e.from_id, []).append(e.target_symbol or e.to_id)
    for n in actual.nodes:
        row: Dict[str, Any] = {"id": n.id, "k": n.kind, "n": n.name, "q": n.qualified_name}
        if n.parent_id:
            row["p"] = n.parent_id
        if n.external:
            row["x"] = 1
        if n.location is not None:
            row["f"] = n.location.path
            row["a"] = n.location.line_start
            row["b"] = n.location.line_end
            if n.kind == "module":
                row["g"] = _package_of(n.location.path)
        if n.signature:
            row["s"] = n.signature
        if n.docstring_summary:
            row["d"] = n.docstring_summary
        if n.id in unresolved:
            row["u"] = sorted(set(unresolved[n.id]))[:25]
            row["uc"] = len(unresolved[n.id])
        nodes.append(row)

    report = check_reflexion(design, actual) if design is not None else None
    divergent = set()
    if report is not None:
        for div in report.divergences:
            for ev in div.evidence:
                divergent.add((ev.from_node, ev.to_node))

    edges = []
    for e in actual.edges:
        if e.kind not in _DEP_KINDS or e.confidence == "unresolved":
            continue
        row = {"s": e.from_id, "t": e.to_id, "k": e.kind[0], "c": e.confidence[0]}
        if e.candidates and len(e.candidates) > 1:
            row["m"] = e.candidates
        if e.location is not None:
            row["l"] = e.location.line
        if e.type_only:
            row["y"] = 1
        if (e.from_id, e.to_id) in divergent:
            row["v"] = 1
        edges.append(row)

    payload: Dict[str, Any] = {
        "project": design.project_name if design else repo_name,
        "generated_at": actual.generated_at,
        "pmf_version": PMF_VERSION,
        "pmap_version": __version__,
        "nodes": nodes,
        "edges": edges,
        "errors": [e.to_dict() for e in actual.errors],
        "languages": actual.languages,
        "design": None,
    }
    if report is not None and design is not None:
        payload["design"] = {
            "subsystems": [{"id": s.id, "name": s.name, "paths": s.paths, "d": s.description} for s in design.subsystems],
            "allowed": [[d.from_subsystem, d.to_subsystem] for d in design.allowed_dependencies],
            "convergences": [[c.from_subsystem, c.to_subsystem, len(c.evidence)] for c in report.convergences],
            "divergences": [[d.from_subsystem, d.to_subsystem, len(d.evidence)] for d in report.divergences],
            "absences": [[a.from_subsystem, a.to_subsystem] for a in report.absences],
            "membership": {k: v for k, v in report.node_to_subsystem.items() if k.startswith("mod:")},
            "warnings": report.warnings,
        }
    return payload


def render_page(payload: Optional[Dict[str, Any]], live: bool, token: str = "") -> bytes:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    data = "null" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")  # keep the JSON from closing the <script> element
    html = (html.replace("__PMAP_DATA__", data).replace("__PMAP_LIVE__", "true" if live else "false")
            .replace("__PMAP_TOKEN__", token))
    return html.encode("utf-8")


def export_html(repo_root: Path, output: Path, design: Optional[str] = None) -> Path:
    cache = MapCache()
    actual = cache.get(repo_root)
    payload = build_payload(actual, find_design(repo_root, design), repo_root.name)
    output.write_bytes(render_page(payload, live=False))
    return output


class _State:
    def __init__(self, repo_root: Path, design: Optional[str]) -> None:
        self.repo_root = repo_root
        self.design_path = design
        self.cache = MapCache()
        self.lock = threading.Lock()
        self.version = 0
        self._last: Optional[ActualMap] = None
        self.token = secrets.token_urlsafe(24)  # proves a settings-changing request comes from our own page
        self.last_seen = time.monotonic()  # monotonic: a sleeping laptop does not count as idle time

    def current(self) -> ActualMap:
        with self.lock:
            actual = self.cache.get(self.repo_root)
            if actual is not self._last:
                self._last = actual
                self.version += 1
            return actual

    def switch(self, repo_root: Path) -> None:
        """Show another project; it is scanned on the next request."""
        with self.lock:
            self.repo_root = repo_root
            self.design_path = None  # an explicit --design belonged to the first project
            self.cache = MapCache()
            self._last = None
            self.version += 1
        desktop.remember_project(repo_root)


class ProjectFolderError(ValueError):
    """Why a folder cannot be opened; `code` lets the page say it in the user's language."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_project_folder(raw: str) -> Path:
    """The folder the user asked to open, or ProjectFolderError."""
    from pmap.hook import count_source_files

    if not raw or not raw.strip():
        raise ProjectFolderError("empty", "empty path")
    path = Path(raw.strip().strip('"')).expanduser()
    if not path.is_absolute():
        raise ProjectFolderError("relative", "use a full path, e.g. /Users/me/project or C:\\code\\project")
    path = path.resolve()
    if not path.is_dir():
        raise ProjectFolderError("not_dir", f"not a folder: {path}")
    if path == Path.home().resolve() or path == Path(path.anchor):
        raise ProjectFolderError("too_wide", "that is your home folder or a whole disk; choose one project folder")
    files = count_source_files(path, MAX_PROJECT_FILES)
    if files == 0:
        raise ProjectFolderError("no_python", f"no source files (Python, TypeScript, JavaScript, Go, Java, Kotlin, C#, Swift, Dart, Rust, C/C++) in {path}")
    if files > MAX_PROJECT_FILES:
        raise ProjectFolderError("too_wide", f"more than {MAX_PROJECT_FILES} source files in {path}")
    return path


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"pmap/{__version__}"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            state.last_seen = time.monotonic()
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, render_page(None, live=True, token=state.token), "text/html; charset=utf-8")
                elif url.path == "/api/connection":
                    from pmap.connect import status

                    self._json(status())
                elif url.path == "/api/graph":
                    actual = state.current()
                    design = find_design(state.repo_root, state.design_path)
                    payload = build_payload(actual, design, state.repo_root.name)
                    payload["version"] = state.version
                    self._json(payload)
                elif url.path == "/api/project":
                    self._json({"path": str(state.repo_root), "name": state.repo_root.name,
                                "recent": desktop.recent_projects(), "platform": desktop.platform_name(),
                                "can_pick": desktop.can_pick_folder()})
                elif url.path == "/api/status":
                    state.current()
                    self._json({"version": state.version})
                elif url.path == "/api/source":
                    actual = state.current()
                    node = resolve_ref(actual, (query.get("id") or [""])[0])
                    if node is None:
                        self._json({"error": "unknown node"}, HTTPStatus.NOT_FOUND)
                    else:
                        self._json({"text": source_of(node, state.repo_root, context_lines=2)})
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page went away (reload, closed tab) before the answer was sent
            except Exception as exc:  # keep serving; report the problem to the page
                self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802
            state.last_seen = time.monotonic()
            url = urlparse(self.path)
            if url.path not in _POST_PATHS:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            # Changes user config or runs local programs: only our own page may ask
            # (token from the page, same origin, JSON body).
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if self.headers.get("X-Pmap-Token") != state.token or (origin and origin.split("://", 1)[-1] != host) \
                    or "application/json" not in (self.headers.get("Content-Type") or ""):
                self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                from pmap import connect as agents

                if url.path == "/api/connect":
                    report = agents.connect(claude=bool(body.get("claude", True)), codex=bool(body.get("codex")),
                                            gemini=bool(body.get("gemini")))
                    self._json({"report": report})
                elif url.path == "/api/disconnect":
                    report = agents.disconnect(claude=bool(body.get("claude", True)), codex=bool(body.get("codex")),
                                               gemini=bool(body.get("gemini")))
                    self._json({"report": report})
                elif url.path == "/api/pick":
                    prompt = str(body.get("prompt") or "Choose a project folder")[:200]
                    chosen = desktop.pick_folder(state.repo_root.parent, prompt)
                    if chosen is None:
                        self._json({"cancelled": True})
                    else:
                        self._open(str(chosen))
                elif url.path == "/api/open":
                    self._open(str(body.get("path") or ""))
                else:
                    self._reveal(str(body.get("id") or ""), str(body.get("path") or ""))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _open(self, raw: str) -> None:
            try:
                path = check_project_folder(raw)
            except ProjectFolderError as exc:
                self._json({"error": str(exc), "code": exc.code, "path": raw}, HTTPStatus.BAD_REQUEST)
                return
            state.switch(path)
            self._json({"path": str(path), "name": path.name})

        def _reveal(self, ref: str, rel_path: str) -> None:
            """Show a node's file, a folder inside the project, or the project itself in the file manager."""
            root = state.repo_root.resolve()
            target = root
            if ref:
                node = resolve_ref(state.current(), ref)
                if node is None or node.location is None:
                    self._json({"error": "unknown node"}, HTTPStatus.NOT_FOUND)
                    return
                target = (root / node.location.path).resolve()
            elif rel_path:
                target = (root / rel_path).resolve()
            if not target.is_relative_to(root) or not target.exists():  # never leave the project
                self._json({"error": "not in the project"}, HTTPStatus.FORBIDDEN)
                return
            desktop.reveal(target)
            self._json({"shown": str(target)})

    return Handler


def serve(repo_root: Path, host: str = "127.0.0.1", port: int = 8765, design: Optional[str] = None,
          open_browser: bool = False, idle_exit: Optional[float] = None, ui_file: Optional[Path] = None) -> int:
    """Serve the live map. `idle_exit`: stop that many seconds after the last request (all pages closed).
    `ui_file`: where to leave the address and token so that a second `pmap ui` reuses this server."""
    state = _State(repo_root, design)
    state.current()  # scan before the first request
    desktop.remember_project(repo_root)
    httpd = None
    for candidate in range(port, port + 20):  # the default port may be taken; use the next free one
        try:
            httpd = ThreadingHTTPServer((host, candidate), _make_handler(state))
            break
        except OSError:
            continue
    if httpd is None:
        print(f"error: no free port in {port}-{port + 19}", file=sys.stderr)
        return 1
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"Method Project Map for {repo_root} at {url}  (Ctrl+C to stop)", flush=True)
    if ui_file is not None:
        _write_private(ui_file, json.dumps({"url": url, "token": state.token, "pid": os.getpid()}))
    if idle_exit:
        def stop_when_idle() -> None:
            while True:
                time.sleep(min(30.0, idle_exit / 3))
                if time.monotonic() - state.last_seen > idle_exit:
                    print("No map page has been open for a while; stopping.", flush=True)
                    httpd.shutdown()
                    return

        threading.Thread(target=stop_when_idle, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if ui_file is not None:
            _forget_ui(ui_file)
    return 0


# ------------------------------------------------------------------ `pmap ui`: the desktop entry point


def ui_file() -> Path:
    from pmap.connect import pmap_home

    return pmap_home() / "ui.json"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the page token
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _forget_ui(path: Path) -> None:
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def running_ui(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The `pmap ui` server that is already running, if any."""
    try:
        info = json.loads((path or ui_file()).read_text(encoding="utf-8"))
        with urllib.request.urlopen(info["url"] + "api/status", timeout=3) as resp:
            resp.read()
        return info
    except Exception:
        return None


def start_project(folder: Optional[str]) -> Optional[Path]:
    """The project `pmap ui` opens: the given folder, else the last one, else one the user picks, else the current folder."""
    if folder:
        return check_project_folder(str(Path(folder).expanduser().resolve()))
    for recent in desktop.recent_projects():
        try:
            return check_project_folder(recent)
        except ProjectFolderError:
            continue
    if desktop.can_pick_folder():
        chosen = desktop.pick_folder(Path.home(), "Choose a project folder for Method Project Map")
        return check_project_folder(str(chosen)) if chosen is not None else None
    return check_project_folder(str(Path.cwd()))


def launch_ui(folder: Optional[str] = None, port: int = 8765) -> int:
    """Open the map in the browser. A second launch reuses the running server instead of starting another."""
    info = running_ui()
    if info is not None:
        if folder:
            request = urllib.request.Request(
                info["url"] + "api/open", method="POST",
                data=json.dumps({"path": str(Path(folder).expanduser().resolve())}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Pmap-Token": info["token"]})
            try:
                urllib.request.urlopen(request, timeout=120).read()
            except urllib.error.HTTPError as exc:
                print(f"error: {json.loads(exc.read() or b'{}').get('error', exc)}", file=sys.stderr)
                return 1
        print(f"Method Project Map is already running at {info['url']}", flush=True)
        webbrowser.open(info["url"])
        return 0
    try:
        repo = start_project(folder)
    except (ProjectFolderError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if sys.stderr is None or not sys.stderr.isatty():  # started from the app shortcut: no terminal to read
            desktop.show_message(f"Cannot open the project: {exc}")
        return 1
    if repo is None:
        print("No folder chosen.")
        return 0
    return serve(repo, port=port, open_browser=True, idle_exit=IDLE_EXIT, ui_file=ui_file())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(serve(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))
