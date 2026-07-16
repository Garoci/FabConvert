# TheFabConvert (fabconvert)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](https://github.com/fabconvert/fabconvert)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](docs/contributing.md)
[![Formats](https://img.shields.io/badge/formats-SVG%20%C2%B7%20DXF%20%C2%B7%20Gerber-informational.svg)](#supported-formats)

**TheFabConvert** (`fabconvert`) converts PCB fabrication files between **SVG ⇄ DXF ⇄ Gerber** as a Python library, a CLI, and an optional desktop GUI.

## Motivation

Open-source tooling often covers one pair of formats, or embeds conversion inside a larger EDA stack. There has not been a small, dedicated open-source project that offers **direct, easy conversion between Gerber, DXF, and SVG in one place** — library + CLI + GUI — without pulling in a full CAD application. This project fills that gap.

## Current status

**Early stage / beta.** The public surface is usable for common centreline-style copper and outline work, but the API and format coverage are still evolving. Support for more fabrication file formats (especially PCB-related ones) is planned.

## Current limitations

Some constructs are **not yet supported** and will raise a clear error (or, for a few write paths, drop geometry with a note) rather than silently invent data.

### Gerber (read)

Raised as `fabconvert.formats.gerber_io.UnsupportedGerberConstruct`:

| Construct | Notes |
|-----------|--------|
| **G36 / G37** region mode (polygon fill) | Not supported yet — planned for a future release |
| **AM** macro apertures (`%AM`) | Not supported |
| **%SR** step-and-repeat | Not supported |
| Missing **%FS** coordinate format | Refused (cannot interpret coordinates safely) |
| Non-circular apertures (e.g. rectangle `R`) | Only circular apertures today |
| Empty files (no draws / flashes) | Refused |

On **write**, filled `ClosedPolygon`s (e.g. copper pours) are not emitted as Gerber regions; the writer notes the loss rather than inventing unsupported G36/G37 output.

### DXF

- DXF has **no native stroke-width property** for traces the way Gerber apertures do. Writers reconstruct **filled outlines** from centreline + width; readers recover width from closed outline "ribbons" via medial-axis / pairing heuristics when possible.
- Unit detection uses `$INSUNITS` plus heuristics; pass `--unit` / `unit_override` when the file is unitless or wrong.
- Recovery via `ezdxf` may still load structurally awkward files with auditor notes.

See [docs/formats.md](docs/formats.md) for the full conversion matrix and behaviour notes.

## Installation

```bash
git clone https://github.com/fabconvert/fabconvert.git
cd fabconvert
pip install .
```

### GUI extra

```bash
pip install .[gui]
```

### Development environment

```bash
pip install .[dev]
```

You can combine extras, e.g. `pip install ".[gui,dev]"`.

## CLI usage

After install, the `fabconvert` entry point is available:

```bash
fabconvert --help
fabconvert convert board.svg board.dxf
fabconvert convert board.gbr board.svg --unit mm
fabconvert info board.dxf
fabconvert batch ./in ./out --to gbr --pattern "*.svg"
```

Subcommands: `convert`, `info`, `batch`, `gui`. Use `--unit mm|inch|mil|cm|m` when input units are ambiguous.

## GUI usage

Install the GUI extra, then either:

```bash
fabconvert-gui
```

or:

```bash
fabconvert gui
```

Drag-and-drop conversion with a live before/after geometry preview (PySide6).

## Using it as a Python library

```python
import fabconvert
from fabconvert.alignment import Units
from fabconvert.formats.gerber_io import UnsupportedGerberConstruct

# One-shot conversion (dispatches on file extensions)
fabconvert.convert("board.svg", "board.dxf")
fabconvert.convert("board.gbr", "board.svg")

# Named helpers
fabconvert.svg_to_dxf("board.svg", "board.dxf")
fabconvert.dxf_to_svg("board.dxf", "board.svg", unit_override=Units.MM)
fabconvert.gerber_to_svg("board.gtl", "board.svg")

# Inspect intermediate geometry
geom, alignment = fabconvert.read("board.gtl")
print(alignment.detected_unit, geom.bounds())
fabconvert.write(geom, "board.svg")

try:
    fabconvert.read("regions.gbr")
except UnsupportedGerberConstruct as e:
    print("unsupported Gerber construct:", e)
```

## Supported formats

| Format | Extensions (read and write) |
|--------|-----------------------------|
| **SVG** | `.svg` |
| **DXF** | `.dxf` |
| **Gerber** | `.gbr`, `.gtl`, `.gbo`, `.gbs`, `.gbl`, `.gto`, `.gts`, `.gko`, `.gm1` |

All six conversion directions (SVG↔DXF↔Gerber) go through a shared intermediate model (`GeometrySet` in millimetres). Details: [docs/formats.md](docs/formats.md).

## Roadmap

This is only the beginning. The long-term goal is **broad support for common fabrication file formats**, especially PCB-related ones (richer Gerber region/macro support, more DXF entity types, and additional fab formats as the project grows).

## Contributing

Contributions, issues, and pull requests are welcome. See [docs/contributing.md](docs/contributing.md) for layout, setup, and golden rules.

Full docs index: [docs/index.md](docs/index.md) (quickstart, CLI, library API, GUI, troubleshooting, changelog).

## License

[MIT](https://opensource.org/licenses/MIT) — see `pyproject.toml` .
