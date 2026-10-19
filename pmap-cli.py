#!/usr/bin/env python3
"""Executable wrapper for the `pmap` CLI."""

import os
import sys

# Ensure repository root is on sys.path when invoked directly
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pmap.cli import main

if __name__ == "__main__":
    sys.exit(main())
