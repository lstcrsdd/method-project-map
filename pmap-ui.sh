#!/bin/sh
# Open the interactive Method Project Map in the browser (macOS / Linux).
#   ./pmap-ui.sh                    the last project you opened, or choose a folder
#   ./pmap-ui.sh <project folder>   that project
# Running it again reuses the open map; the map stops by itself a while after its browser tab is closed.
HERE="$(cd "$(dirname "$0")" && pwd)"
# the newest Python 3 available: maps are parsed with its grammar
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$(command -v "$candidate")"; break
  fi
done
[ -z "$PY" ] && { echo "Method Project Map needs Python 3.9 or newer." >&2; exit 1; }
# ~/.pmap/venv (from `pmap langs install`) carries the grammars for languages besides Python
[ -x "$HOME/.pmap/venv/bin/python" ] && PY="$HOME/.pmap/venv/bin/python"
exec "$PY" -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "$HERE" ui "$@"
