# Quick start

## Install

**Library + CLI** (no GUI dependencies — only `ezdxf`, `click`, `rich`):

```bash
pip install .
```

**With the GUI** (adds PySide6):

```bash
pip install ".[gui]"
```

**Development** tooling (`pytest`, `ruff`, `build`):

```bash
pip install ".[dev]"
```

You can combine extras (`pip install ".[gui,dev]"`). Use `pyproject.toml` as
the single source of truth for versions — the package version is read
dynamically from `fabconvert.__version__`, so `fabconvert --version` and the
installed wheel always agree.

## Your first conversion

### from Python

```python
import fabconvert

fabconvert.convert("board.svg", "board.dxf")      # SVG → DXF
fabconvert.convert("board.gbr", "board.svg")      # Gerber → SVG
fabconvert.convert("board.dxf", "board.gbr")      # DXF → Gerber
```

`convert` dispatches purely on the file extensions, so the six supported
directions (svg→dxf, svg→gbr, dxf→svg, gbr→svg, dxf→gbr, gbr→dxf) all "just
work" with one function.

### from the command line

```bash
fabconvert convert board.svg board.dxf
```

Output:
```
✓ SVG board.svg → DXF board.dxf
    detected unit: mm
    note: SVG (Y-down, mm) -> 1:1 intermediate
    bounds: 22.100 × 14.000 mm (xmin=0.000, ymin=0.000, xmax=22.100, ymax=14.000)
                  Entities
+-------------------------------------------+
| lines | arcs | circles | polygons | paths |
|-------|------+---------+----------+-------|
|     0 |    0 |       2 |        0 |    12 |
+-------------------------------------------+
    elapsed: 0.9 ms
```

### with the GUI

```bash
fabconvert gui
```

Drag `board.svg` into the window, pick an output format and directory, and
press **Convert**. Two side-by-side canvases show the input and the re-read
output so you can confirm orientation, Y-flip, and scale visually.

## Force a unit on ambiguous input

DXF and Gerber files can be unitless or ambiguous. Pass `unit_override`:

```python
from fabconvert.alignment import Units

fabconvert.convert("ambiguous.dxf", "out.svg", unit_override=Units.INCH)
```

or on the CLI:

```bash
fabconvert convert ambiguous.dxf out.svg --unit inch
```

Choices: `mm`, `inch`, `mil`, `cm`, `m`. The detected unit and a
`detection_note` (non-empty whenever a guess or override was made) are always
reported by `info`/`convert`/the GUI.

## Inspect a file without converting

```bash
fabconvert info board.gbr
```

## Batch a directory

```bash
fabconvert batch ./in ./out --to dxf --pattern "*.svg"
```

Every matching file with a supported extension is converted; one bad file does
not abort the batch. See [CLI reference](cli.md).
