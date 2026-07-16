"""High-level conversion API.

Each function is a thin orchestration: read a source format into the
intermediate model + Alignment, write the intermediate out to a destination
format with a freshly-built writer Alignment.  All real work lives in the
readers/writers + alignment.

Every conversion goes through ``alignment`` for the coordinate transform
(golden rule zero) — there is no format-to-format shortcut.  Round trips
(SVG→DXF→SVG, SVG→GBR→SVG) are exercised by ``tests/test_roundtrip_*.py`` at
1 µm tolerance against the real KiCad fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .alignment import Alignment, Units
from .core.geometry import GeometrySet
from .formats.svg_io import SvgReader, SvgWriter
from .formats.dxf_io import DxfReader, DxfWriter
from .formats.gerber_io import GerberReader, GerberWriter

PathLike = Union[str, Path]


def read(path: PathLike, *, unit_override: Optional[Units] = None
         ) -> tuple[GeometrySet, Alignment]:
    """Read any supported file into ``(GeometrySet, Alignment)``.

    Dispatches on extension.  ``unit_override`` (DXF/Gerber) forces a unit and
    always wins over the heuristic / metadata.
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".svg":
        r = SvgReader.from_file(p)
        return r.geometry, r.alignment
    if ext in (".dxf",):
        r = DxfReader.from_file(p, unit_override=unit_override)
        return r.geometry, r.alignment
    if ext in (".gbr", ".gtl", ".gbo", ".gbs", ".gbl", ".gto", ".gts",
               ".gko", ".gm1"):
        r = GerberReader.from_file(p, unit_override=unit_override)
        return r.geometry, r.alignment
    raise ValueError(f"unrecognised input extension: {ext!r} ({p})")


def write(geom: GeometrySet, path: PathLike) -> None:
    """Write the intermediate model to a destination file, dispatching on ext."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".svg":
        SvgWriter().to_file(geom, p)
    elif ext == ".dxf":
        DxfWriter().to_file(geom, p)
    elif ext in (".gbr", ".gtl", ".gbo", ".gbs", ".gbl", ".gto", ".gts",
                 ".gko", ".gm1"):
        GerberWriter().to_file(geom, p)
    else:
        raise ValueError(f"unrecognised output extension: {ext!r} ({p})")


def convert(src: PathLike, dst: PathLike, *,
            unit_override: Optional[Units] = None) -> None:
    """Convert a source file to a destination format through the intermediate."""
    geom, _ = read(src, unit_override=unit_override)
    write(geom, dst)


# ---- Named convenience entry points ----

def svg_to_dxf(src: PathLike, dst: PathLike) -> None:
    geom, _ = read(src); write(geom, dst)


def svg_to_gerber(src: PathLike, dst: PathLike) -> None:
    geom, _ = read(src); write(geom, dst)


def dxf_to_svg(src: PathLike, dst: PathLike, *,
               unit_override: Optional[Units] = None) -> None:
    geom, _ = read(src, unit_override=unit_override); write(geom, dst)


def gerber_to_svg(src: PathLike, dst: PathLike, *,
                  unit_override: Optional[Units] = None) -> None:
    geom, _ = read(src, unit_override=unit_override); write(geom, dst)
