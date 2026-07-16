# Contributing

## Repository layout

```
fabconvert/                 project root (pyproject.toml, README.md, docs/)
├── tests/                  pytest suite + fixtures
├── docs/                   user documentation
└── fabconvert/             the importable Python package
    ├── __init__.py         public re-exports + __version__
    ├── api.py              high-level read/write/convert
    ├── cli.py              click + rich command line
    ├── core/               intermediate geometry model (geometry.py, units)
    ├── alignment/          Alignment + Units + detection
    ├── formats/            svg_io / dxf_io / gerber_io
    └── gui/                PySide6 app (optional [gui] extra)
```

- `pyproject.toml` uses an **explicit** `packages = [...]` list (all five
  subpackages) — there is no auto-discovery. If you add a new subpackage, add
  it to that list.
- The package **version lives only in `fabconvert/__init__.py`**
  (`__version__`); `pyproject.toml` reads it dynamically via
  `[tool.setuptools.dynamic] version = {attr = "fabconvert.__version__"}`.
  Never duplicate the version as a literal.

## Setup

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # + GUI:  ".[gui,dev]"
.venv/Scripts/python -m pytest
```

A headless GUI smoke harness can run without a display:

```bash
QT_QPA_PLATFORM=offscreen python -c "..."      # construct MainWindow, run convert_one across files
```

## The golden rules

These are enforced by the library structure (and a few by tests). They keep
round trips lossless and the alignment logic in one place:

0. **No format module flips Y or converts units on its own — that's
   `Alignment`'s job.** Every reader builds an `Alignment`; every writer
   consumes one. The flip is taken about the **bounding-box midline**, never
   about zero. Centralising this is what made the original scattered,
   untested Y-axis bugs impossible.
1. **Gerber trace width is read from `%ADD` aperture metadata, never rendered
   geometry.** Width rides on the primitive's `stroke_width` (mm) and isn't
   baked into the centreline.
2. **DXF has no width property; DXF output is a filled outline.** Documented,
   never silently worked around.
4. **No unit/scale guess without a fixture test** (`tests/test_alignment.py`).
   A manual `unit_override` always wins over a heuristic, though.
5. **Pads/vias (filled `Circle`, `stroke_width=None`) are structurally distinct
   from stroked traces** — kept in separate `GeometrySet` lists on purpose.

## What you may / may not change

- **Untouchable (stable public core):** everything under `fabconvert/core/`,
  `fabconvert/alignment/`, `fabconvert/formats/`, plus `fabconvert/api.py`
  and `fabconvert/__init__.py`. The CLI/GUI wrappers depend only on their
  public surface. If you genuinely need to change one of these, do it as a
  deliberate, tested change to the core, not as part of a CLI/GUI task.
- **`fabconvert/cli.py`** is a thin wrapper meant to be extended; the
  `convert in out --unit ...` behaviour must stay backward compatible.
- **`fabconvert/gui/`** is the GUI surface — feel free to add features, but
  keep PySide6 **lazily imported** inside `launch()`/`main()` so a CLI-only
  install never pulls it, and keep `convertworker.py` Qt-free and testable
  headlessly.

## CLI: a note on exit codes

`cli.py` uses click with `standalone_mode=False` and translates exceptions to
return codes itself. To signal a non-zero exit from a subcommand, **`raise
SystemExit(<code>)`** — not `click.exceptions.Exit`. Under
`standalone_mode=False`, click silently swallows its own `Exit` (the call
returns `None`, losing the exit code); `SystemExit` is a `BaseException` so
it propagates to `main()`'s `except SystemExit` which converts it to a
return code. (The broad `except Exception` last-resort can't catch it, so it
isn't accidentally re-swallowed.)

## Tests

The bundled suite under `tests/` round-trips real KiCad fixtures at 1 µm
tolerance. If you add a format path or a detection heuristic, add a fixture
test — golden rule #4.
