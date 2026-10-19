"""Desktop integration: the system folder picker, "show in Finder / Explorer", recent projects, the app shortcut.

The server calls the first three only for requests from its own page (see the token check in pmap.web).
Standard library only; every platform falls back to typing a path in the page.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from pmap.connect import BOOTSTRAP, home, launcher_path, pmap_home

MAX_RECENT = 8
PICK_TIMEOUT = 900  # seconds a folder dialog may stay open
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console flash for helper processes


def platform_name() -> str:
    if sys.platform == "darwin":
        return "mac"
    return "windows" if os.name == "nt" else "linux"


# ------------------------------------------------------------------ recent projects


def _recent_file() -> Path:
    return pmap_home() / "recent.json"


def recent_projects() -> List[str]:
    try:
        items = json.loads(_recent_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in items if isinstance(p, str) and Path(p).is_dir()][:MAX_RECENT]


def remember_project(path: Path) -> None:
    items = [str(path)] + [p for p in recent_projects() if p != str(path)]
    try:
        _recent_file().parent.mkdir(parents=True, exist_ok=True)
        _recent_file().write_text(json.dumps(items[:MAX_RECENT], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # a read-only home only loses the list


# ------------------------------------------------------------------ folder picker

_MAC_PICK = [
    "on run argv",
    "activate",
    "set chosen to choose folder with prompt (item 1 of argv) default location (POSIX file (item 2 of argv))",
    "return POSIX path of chosen",
    "end run",
]

_WINDOWS_PICK = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$d.Description = $env:PMAP_PICK_PROMPT; $d.SelectedPath = $env:PMAP_PICK_START; $d.ShowNewFolderButton = $false; "
    "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }"
)

_TK_PICK = (
    "import sys, tkinter\nfrom tkinter import filedialog\n"
    "root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
    "sys.stdout.write(filedialog.askdirectory(initialdir=sys.argv[1], title=sys.argv[2], mustexist=True) or '')\n"
)


def can_pick_folder() -> bool:
    system = platform_name()
    if system == "mac":
        return shutil.which("osascript") is not None
    if system == "windows":
        return shutil.which("powershell") is not None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return bool(shutil.which("zenity") or shutil.which("kdialog") or importlib.util.find_spec("tkinter"))


def pick_folder(start: Path, prompt: str) -> Optional[Path]:
    """Ask for a folder with the system dialog. None when the user cancels; RuntimeError when there is no dialog."""
    if not can_pick_folder():
        raise RuntimeError("no folder dialog on this system; type the path instead")
    system = platform_name()
    env = None
    if system == "mac":
        cmd = ["osascript"] + [arg for line in _MAC_PICK for arg in ("-e", line)] + [prompt, str(start)]
    elif system == "windows":
        cmd = ["powershell", "-NoProfile", "-STA", "-Command", _WINDOWS_PICK]
        env = dict(os.environ, PMAP_PICK_PROMPT=prompt, PMAP_PICK_START=str(start))
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={prompt}", f"--filename={start}{os.sep}"]
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", str(start), "--title", prompt]
    else:
        cmd = [sys.executable, "-I", "-c", _TK_PICK, str(start), prompt]
    result = subprocess.run(cmd, capture_output=True, timeout=PICK_TIMEOUT, env=env, creationflags=_NO_WINDOW)
    chosen = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0 or not chosen:
        return None  # cancelled
    return Path(chosen)


def show_message(text: str) -> None:
    """Best-effort message box for launches without a terminal (the app shortcut): errors must not vanish."""
    system = platform_name()
    try:
        if system == "mac":
            cmd = ["osascript", "-e", "on run argv", "-e", 'display alert "Method Project Map" message (item 1 of argv)',
                   "-e", "end run", text]
        elif system == "windows":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show($env:PMAP_MSG, 'Method Project Map')"]
        elif shutil.which("zenity"):
            cmd = ["zenity", "--error", "--title=Method Project Map", f"--text={text}"]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "Method Project Map", text]
        else:
            return
        subprocess.run(cmd, capture_output=True, timeout=PICK_TIMEOUT, env=dict(os.environ, PMAP_MSG=text),
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


# ------------------------------------------------------------------ show in file manager


def reveal(path: Path) -> None:
    """Show a file (selected) or a folder in Finder / Explorer / the desktop's file manager."""
    system = platform_name()
    if system == "mac":
        cmd = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
    elif system == "windows":
        # explorer parses its own command line: /select,"<path>" must stay one piece
        cmd = f'explorer /select,"{path}"' if path.is_file() else ["explorer", str(path)]
    else:
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if opener is None:
            raise RuntimeError("no file manager opener (xdg-open) found")
        folder = str(path if path.is_dir() else path.parent)
        cmd = [opener, "open", folder] if opener.endswith("gio") else [opener, folder]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=_NO_WINDOW)


# ------------------------------------------------------------------ app shortcut (`pmap shortcut`)

_MARKER = "pmap-shortcut"  # inside our macOS app bundle: we only ever replace or remove an app we made


def shortcut_path() -> Path:
    """Where the "Method Project Map" shortcut lives: ~/Applications, the Start menu, or the desktop's app menu."""
    system = platform_name()
    if system == "mac":
        return home() / "Applications" / "Method Project Map.app"
    if system == "windows":
        appdata = Path(os.environ.get("APPDATA") or home() / "AppData" / "Roaming")
        return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Method Project Map.lnk"
    data = Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share")
    return data / "applications" / "method-project-map.desktop"


def _desktop_quote(arg: str) -> str:
    """Quote one Exec= argument as the Desktop Entry spec asks."""
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def install_shortcut() -> Path:
    """Create the "Method Project Map" app shortcut that runs `pmap ui`; the runtime must be installed (~/.pmap)."""
    launcher = launcher_path()
    if not launcher.exists():
        raise RuntimeError(f"{launcher} is missing; install the runtime first (install.sh / install.bat)")
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    system = platform_name()
    if system == "mac":
        if target.exists():
            if not (target / "Contents" / "Resources" / _MARKER).exists():
                raise RuntimeError(f"{target} exists and was not made by pmap; leaving it alone")
            shutil.rmtree(target)
        quoted = str(launcher).replace("\\", "\\\\").replace('"', '\\"')
        script = f'do shell script quoted form of "{quoted}" & " ui > /dev/null 2>&1 &"'
        subprocess.run(["osacompile", "-o", str(target), "-e", script], check=True, capture_output=True)
        (target / "Contents" / "Resources" / _MARKER).write_text("made by `pmap shortcut`\n", encoding="utf-8")
    elif system == "windows":
        python = Path(sys.executable)
        windowless = python.with_name("pythonw.exe")  # no console window behind the browser
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PMAP_LNK); "
              "$s.TargetPath = $env:PMAP_LNK_TARGET; $s.Arguments = $env:PMAP_LNK_ARGS; "
              "$s.WorkingDirectory = $env:USERPROFILE; $s.Description = 'Method Project Map'; $s.Save()")
        env = dict(os.environ, PMAP_LNK=str(target), PMAP_LNK_TARGET=str(windowless if windowless.exists() else python),
                   PMAP_LNK_ARGS=f'-I -c "{BOOTSTRAP}" "{pmap_home() / "lib"}" ui')
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True, env=env,
                       creationflags=_NO_WINDOW)
    else:
        target.write_text(
            "[Desktop Entry]\nType=Application\nName=Method Project Map\n"
            "Comment=Interactive map of a Python project\n"
            f"Exec={_desktop_quote(str(launcher))} ui\nTerminal=false\nCategories=Development;\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
    return target


def remove_shortcut() -> Optional[Path]:
    target = shortcut_path()
    if not target.exists():
        return None
    if platform_name() == "mac":
        if not (target / "Contents" / "Resources" / _MARKER).exists():
            raise RuntimeError(f"{target} was not made by pmap; leaving it alone")
        shutil.rmtree(target)
    else:
        target.unlink()
    return target
