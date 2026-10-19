#!/bin/sh
# Method Project Map installer for macOS and Linux.
#   ./install.sh            install the `pmap` command and connect Claude Code
#   ./install.sh --codex    ... connect Codex instead      ./install.sh --all   both
#   ./install.sh --none     only install the `pmap` command
# Everything goes under ~/.pmap (and, when connecting, ~/.claude or ~/.codex). Undo: ~/.pmap/bin/pmap disconnect --all
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
# the newest Python 3 available: maps are parsed with its grammar
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$(command -v "$candidate")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Method Project Map needs Python 3.9 or newer (python3 was not found or is too old)." >&2
  exit 1
fi
MODE="${1:---claude}"
if [ "$MODE" = "--none" ]; then
  "$PY" -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from pmap.connect import install_runtime; print("Installed:", install_runtime())' "$HERE"
else
  "$PY" -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "$HERE" connect "$MODE"
fi
"$PY" -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "$HERE" shortcut || true
echo
echo "Done. Open Method Project Map from your apps (\"Method Project Map\") or run:  $HOME/.pmap/bin/pmap ui"
case ":$PATH:" in
  *":$HOME/.pmap/bin:"*) ;;
  *) echo "Tip: add it to PATH:  echo 'export PATH=\"\$HOME/.pmap/bin:\$PATH\"' >> ~/.zshrc   (or ~/.bashrc)";;
esac
