# fabconvert — documentation

**fabconvert** converts PCB fabrication files between **SVG ⇄ DXF ⇄ Gerber**.
It ships in three independently-installable shapes that share one library core:

- a **plain-Python library** — `import fabconvert; fabconvert.convert("a.svg", "b.dxf")`,
- a **colorful CLI** — `fabconvert convert | info | batch | gui | --version`,
- an **optional PySide6 desktop GUI** — drag, pick, convert, and *see* the
  geometry before and after (install with `pip install "fabconvert[gui]"`).

The library is deliberately Qt-free; the GUI is an opt-in extra, imported
lazily so a CLI-only install never pulls PySide6.

---

## Contents

1. [Quick start](quickstart.md) — install in two minutes, first conversion.
2. [CLI reference](cli.md) — every subcommand, flag, and exit code.
3. [Library / API reference](library.md) — `read` / `write` / `convert`, the
   geometry model, `Alignment` + `Units`, per-format coverage.
4. [GUI guide](gui.md) — install, layout, drag & drop, batch, error dialogs.
5. [Format coverage](formats.md) — what is read/written per format, plus the
   Gerber-construct limitations.
6. [Troubleshooting & errors](troubleshooting.md) — diagnosing common failures.
7. [Contributing](contributing.md) — layout, the "golden rules", running tests.
8. [Changelog](changelog.md).

The [README](../README.md) is a one-page condensed version of all three usage
modes; the pages here are the full reference.

---

## Design in one paragraph

Every reader converts a source file into one format-independent **intermediate
geometry model** (`GeometrySet` in millimetres, Y-down) plus an **`Alignment`**
that owns *all* coordinate transforms — unit scale, Y-flip, origin. Every writer
consumes that model and emits a destination file using its own `Alignment`.
There is **no format-to-format shortcut**: blindness to the destination is what
keeps round trips (SVG→DXF→SVG, SVG→GBR→SVG) lossless at micron tolerance
against real KiCad fixtures. The "golden rules" (no format module flips Y or
converts units on its own — that's `Alignment`'s job) live in the library
docstrings and in [Contributing](contributing.md).

## Project layout

```
fabconvert/
├── README.md                  one-page usage overview
├── pyproject.toml             packaging, extras, console scripts
├── docs/                      this directory
└── fabconvert/                the importable package
    ├── __init__.py            public re-exports + __version__
    ├── api.py                 high-level read/write/convert
    ├── cli.py                 click + rich command line
    ├── core/                  intermediate geometry model (+ units)
    ├── alignment/             Alignment + Units + detection heuristics
    ├── formats/               svg_io / dxf_io / gerber_io readers & writers
    └── gui/                   PySide6 app (optional [gui] extra)
```

`core/`, `alignment/`, `formats/`, `api.py`, and `__init__.py` are the
**stable public core** and are not modified by the CLI/GUI wrappers.
