# Library / API reference

`fabconvert` is usable as plain Python with no GUI dependency. Everything in
this page is the public, stable surface. The high-level functions live in
`fabconvert.api` and are re-exported at the package root.

```python
import fabconvert                  # fabconvert.read / write / convert / ...
from fabconvert.alignment import Units
from fabconvert.core.geometry import GeometrySet, Line, Arc, Circle, ClosedPolygon, Path
from fabconvert.formats.gerber_io import UnsupportedGerberConstruct
```

## High-level API — `fabconvert.api`

### `read(path, *, unit_override=None)`

Read any supported file into the intermediate model.

```python
def read(path, *, unit_override: Optional[Units] = None
         ) -> tuple[GeometrySet, Alignment]: ...
```

- **Dispatches on the file extension** (case-insensitive):
  - `.svg` → `SvgReader` (SVG is Y-down; alignment derives from `viewBox`/
    `width`/`height`; SVG has no `unit_override`).
  - `.dxf` → `DxfReader` (unit can be guessed from `$INSUNITS`, the extent,
    or forced).
  - `.gbr .gtl .gbo .gbs .gbl .gto .gts .gko .gm1` → `GerberReader`
    (`%MO` declares mm/inch; `unit_override` wins).
- **Returns** `(GeometrySet, Alignment)`. The geometry is in **millimetres**,
  **Y-down** (the intermediate orientation).
- **`unit_override`** always wins over the heuristic / metadata — pass one of
  `Units.MM/INCH/MIL/CM/M`. `None` means "let detection decide".
- **Raises** `ValueError` for an unrecognized extension.

```python
geom, alignment = fabconvert.read("board.gtl")
print(alignment.detected_unit.value)   # "mm"
print(alignment.detection_note)         # e.g. "%MOIN*% (inches)"
print(geom.bounds())                    # (xmin, ymin, xmax, ymax) in mm
```

> The detection **note** is `""` only when units were explicit and no guess
> was made. `detected_unit` is always non-`None` on the `read()` path. The
> CLI/GUI call `read()` directly (rather than `convert()`) precisely so they
> can surface these.

### `write(geom, path)`

Write the intermediate model to a destination file, dispatching on extension.

```python
def write(geom: GeometrySet, path) -> None: ...
```

- Accepts the same extensions as `read()`. Picks the writer from the destination
  suffix (`SvgWriter` / `DxfWriter` / `GerberWriter`).
- **Raises** `ValueError` for an unrecognized output extension.

### `convert(src, dst, *, unit_override=None)`

One-shot `read` then `write`. The convenient entry point for scripts:

```python
fabconvert.convert("board.svg", "board.dxf")
fabconvert.convert("ambiguous.gbr", "board.svg", unit_override=Units.INCH)
```

> Note: `convert` discards the `Alignment` internally (`geom, _ = read(...)`).
> If you need the detected unit / note, call `read()` yourself — which is what
> the `info`/`convert` CLI commands and the GUI do.

### Named convenience functions

Mirrors of `convert` for each named direction:

```python
fabconvert.svg_to_dxf(src, dst)
fabconvert.svg_to_gerber(src, dst)
fabconvert.dxf_to_svg(src, dst, *, unit_override=None)
fabconvert.gerber_to_svg(src, dst, *, unit_override=None)
```

All seven (`read`, `write`, `convert`, and the four named functions) are in
`fabconvert.__all__`, so `from fabconvert import *` works and they're all
available as `fabconvert.<name>`.

## The geometry model — `fabconvert.core.geometry`

All coordinates in this module are in **millimetres** and in **intermediate
orientation** (Y increases downward — the SVG/screen convention). The
`alignment` module is responsible for flipping Y on the boundary between a
math-Y-up format (DXF, Gerber) and this model; format modules never flip Y on
their own.

### `GeometrySet`

A container that keeps primitive lists separate (rather than one polymorphic
list). This keeps the writers plain, branch-light loops and makes the
pad-vs-trace distinction structural.

```python
@dataclass
class GeometrySet:
    lines:    List[Line]
    arcs:     List[Arc]
    circles:  List[Circle]
    polygons: List[ClosedPolygon]
    paths:     List[Path]

    def __iter__(self) -> Iterator[object]  # yields every primitive
    def __len__(self) -> int                  # total primitive count
    @property
    def is_empty(self) -> bool
    def bounds(self) -> Optional[Tuple[float, float, float, float]]
        # (xmin, ymin, xmax, ymax) in mm, or None if empty.
```

`bounds()` accounts for half the stroke width on stroked primitives and the
full radius on filled circles, matching how the source converters computed
the viewBox.

### Primitives

All carry an optional `stroke_width: Optional[float]` **in mm**:
- `None` means "this is an outline / fill edge, not a trace" — *distinct*
  from `0.0` (a genuine, degenerate width).
- A non-`None` width is a **centreline trace** (`Path`/`Line`/`Arc` carrying
  its width) — the width is **not baked into the geometry** (golden rule #1),
  so it survives round trips even though the centreline is what's drawn.

#### `Line(x0, y0, x1, y1, stroke_width=None)`

A straight segment. `.points()` → `((x0,y0),(x1,y1))`; `.length()` → mm.

#### `Arc(cx, cy, radius, start_angle, end_angle, stroke_width=None)`

A circular arc. Convention (matches both source converters exactly):
- `cx, cy` centre in mm; `radius` in mm.
- `start_angle`, `end_angle` are in **degrees**, measured in intermediate
  (Y-down) space.
- The arc sweeps from `start_angle` to `end_angle` in the **counterclockwise**
  sense in intermediate space.
- `.endpoints()` returns the two endpoints via standard `cos/sin(radians)`.
- `.sweep_deg()` returns the swept angle in `[0, 360)` (handles wrap).

> Because the model is Y-down and a screen canvas is also Y-down,
  `endpoints()`/sampling map directly to pixels with **no axis flip** — that's
  how the GUI's `GeometryCanvas` matches the library's own orientation.

#### `Circle(cx, cy, radius, stroke_width=None)`

A filled circle (pad / via / drill) when `stroke_width is None`; a stroked
ring when a width is set. Filled pads and stroked traces are structurally
distinct (golden rule #5).

#### `ClosedPolygon(points)`

A filled closed outline (DXF copper region, hatch, etc.). No width.

#### `Path(segments, stroke_width=None, filled=False, closed=False)`

An ordered polyline. `-stroke_width` and `filled=True` → a filled outline
instead. `.is_stroked()` → `stroke_width is not None`.

## Coordinates & units — `fabconvert.alignment`

### `Units` (enum)

```python
class Units(Enum):
    MM        = "mm"
    INCH      = "inch"
    MIL       = "mil"
    CM        = "cm"
    M         = "m"
    UNITLESS  = "unitless"
```

- `.value` → the lowercase string (use it for logging / CLI).
- `.mm_per_unit` → mm per source unit (`MM=1.0`, `INCH=25.4`, `MIL=0.0254`,
  `CM=10.0`, `M=1000.0`, `UNITLESS=1.0`).

Import: `from fabconvert.alignment import Units`.

### `Alignment`

The single coordinate transform passed through every reader and writer. It
carries the unit scale, Y-flip, origin, the **detected unit**, and a
human-readable **detection note**.

```python
@dataclass
class Alignment:
    unit_scale: float = 1.0      # mm per source-format unit
    y_flip: bool = False         # True for math-Y-up formats (DXF, Gerber)
    flip_axis: float = 0.0       # mirror about this Y (bbox midline)
    origin: Tuple[float, float] = (0.0, 0.0)
    detected_unit: Optional[Units] = None
    detection_note: str = ""     # "" only when units were explicit, no guess
```

How it's used:
- **Reader**: `intermediate = (source - origin) * unit_scale` in X; in Y,
  `intermediate_y = (flip_axis - source_y) * unit_scale` when `y_flip`.
- **Writer**: the inverse — the Y-flip is its own inverse, but `unit_scale`
  is divided back out so writers emit in their *target* units.

Detection by format (`alignment/detect.py`):
- **SVG** — from `viewBox`/`width`/`height`; `mm` by default, `inch` when the
  `width` attribute ends in `in`/`inch`.
- **DXF** — `$INSUNITS` if non-zero; else a heuristic from the drawing extent;
  else `mm` (no geometry to detect). `unit_override` always wins and sets
  `detection_note = "unit overridden to <unit>"`.
- **Gerber** — `%MOIN*%` → inches; `%MOMM*%` (or absent) → mm; override wins.

Convenience constructors:
- `Alignment.mm(y_flip=False, flip_axis=0.0, origin=(0,0))` — alignment
  already in mm (handy for SVG).
- `Alignment.for_units(units, y_flip=False, flip_axis=0.0, origin=(0,0))`.
