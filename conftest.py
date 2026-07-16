"""pytest rootdir hook: ensure the repo root (the ``fabconvert`` package
source) is on ``sys.path`` ahead of any stale / non-editable site-packages
``fabconvert`` copy.

The tests live in ``tests/`` (no ``__init__.py``); pytest's default rootdir
insertion prepends the *test-file's* directory (``tests/``) to ``sys.path``,
not the repo root.  Without this conftest an installed (non-editable) copy of
``fabconvert`` in site-packages can shadow the local working-tree source
during ``pytest`` even though a plain ``python -c`` from the repo root resolves
to the local source — producing test results that don't reflect the edited
file.  This is the canonical pytest pattern for the "library in repo root,
tests in ``tests/`` without ``__init__.py``" layout, and makes the install
mode a no-op: tests always import the source you're editing.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
