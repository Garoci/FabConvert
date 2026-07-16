# Format coverage

fabconvert reads and writes **SVG**, **DXF** (via `ezdxf`), and **Gerber**
(X2-flavoured, centreline + aperture-width model). All conversions funnel
through the same intermediate `GeometrySet` (mm, Y-down); there is no
format-to-format shortcut.

## Supported extensions

The extension set is identical for reading and writing (case-insensitive):

| Format | Extensions |
|--------|------------|
| SVG    | `.svg` |
| DXF    | `.dxf` |
| Gerber | `.gbr .gtl .gbo .gbs .gbl .gto .gts .gko .gm1` |

Unknown extensions raise `ValueError` from `read()`/`write()`. The CLI surfaces
this as "unrecognised input/output extension" (exit 1); the GUI as a dialog.

> Gerber variant suffixes (`.gtl` = top copper, `.gbo` = bottom silkscreen,
> `.gbl` = bottom copper, `.gto` = top silkscreen, `.gts`/`.gbs` = solder
> mask, `.gko` = keep-out, `.gm1` = board outline) are all treated the same
> way — they're conventional names for the same Gerber text format.

## Conversion matrix

All 6 directions are available through `convert`:

| From ↓ \ To → | SVG | DXF | Gerber |
|----------------|-----|-----|--------|
| **SVG**   | —   | ✓ | ✓ |
| **DXF**   | ✓ | — | ✓ |
| **Gerber**| ✓ | ✓ | — |

Round trips SVG→DXF→SVG and SVG→GBR→SVG are exercised by the bundled tests at
1 µm tolerance against real KiCad fixtures.

## What each format contributes / how it maps

### SVG (Y-down)
- Source of truth for the screen orientation. Alignment derived from
  `viewBox`/`width`/`height`; `mm` by default, `inch` when the `width`
  attribute is suffixed `in`/`inch`. `y_flip = False`.
- Stroked paths with width → centrelines; filled paths → filled `Path`/polygon.
- Arcs in SVG path data are approximated by their chord (an `Arc` primitive is
  also recorded, but KiCad SVGs use straight segments, so this is rare).

### DXF (Y-up, via `ezdxf`)
- `$INSUNITS` declares the unit; if it's 0/absent, fabconvert relies on a
  heuristic from the drawing extent, else defaults to mm. `--unit`/`unit_override`
  always wins.
- `y_flip = True` — the alignment flips about the **bounding-box midline**
  (`y' = ymax - y`), *not* about zero, so the geometry isn't translated.
- DXF has no width property. **DXF output is a filled outline**, documented and
  never silently worked around — the writer reconstructs filled outlines from
  the centreline + width model.
- `DxfReader` prints detection/auditor notes to stdout and falls back to
  `ezdxf.recover.readfile` on read failure, so a structurally-troublesome DXF may
  still read with recovered data rather than raising.

### Gerber (mm, no inherent Y-flip)
- `%MO` declares mm/inch. `%FS` sets the coordinate format. Apertures (`%ADD`)
  carry the trace width — Gerber trace width is read from **aperture metadata,
  never rendered geometry** (golden rule #1).
- `y_flip = True`. Pads/vias are filled `Circle`s with no stroke width — a
  different thing from a stroked trace (golden rule #5).
- The Gerber writer emits centreline traces + aperture widths; filled
  `ClosedPolygon`s are **dropped** (the writer prints a NOTE to stdout —
  there's no exception). The CLI prints the NOTE under `library notes:`;
  the GUI shows it in the info strip and pre-counts `geom.polygons` before
  writing to warn.

## Unsupported Gerber constructs

`fabconvert.formats.gerber_io.UnsupportedGerberConstruct` is raised on read for
constructs that can't be represented as centreline + width:

| Construct | Message |
|-----------|---------|
| AM macro apertures (`%AM`) | `AM macro aperture present` |
| Region fills (`G36`/`G37`) | `G36/G37 region fill present` |
| Step-and-repeat blocks (`%SR`) | `SR step-and-repeat block present` |
| Missing `%FS` coordinate format | `%FS coordinate-format string missing — cannot safely interpret` |
| Non-circular aperture (e.g. rectangle `R`) | `aperture D{code} is {shape}, not a circle` |
| No draws and no flashes | `no draws or flashes found` |

Catch it once and relay `str(e)`:

```python
from fabconvert.formats.gerber_io import UnsupportedGerberConstruct

try:
    fabconvert.read("macro.gbr")
except UnsupportedGerberConstruct as e:
    print("unsupported:", e)
```

The CLI prints a red panel with a friendly expansion ("macro apertures,
G36/G37 region fills, and step-and-repeat blocks aren't supported"); the GUI
shows the same text in a dialog.

## Width model — why round trips are lossless

A trace's width is carried on the primitive (`stroke_width` in mm), never baked
into the geometry. So:

- SVG stroke → `Path`/`Line`/`Arc` with `stroke_width` → Gerber aperture D
  with that width → SVG stroke of the same width.
- The *centreline* is what round-trips geometrically; the width is metadata.

Pads/vias (`Circle`, `stroke_width=None`, filled) are structurally separate from
stroked traces, so a pad doesn't accidentally become a zero-area trace and vice
versa.
