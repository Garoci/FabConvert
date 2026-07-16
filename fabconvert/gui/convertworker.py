"""Pure (no Qt) conversion worker for the GUI.

Keeps all public-API calls and exception triage in one module that has **no
PySide6 dependency**, so it can be imported and unit-tested headlessly without
a display.  The Qt layer (:mod:`fabconvert.gui.app`) calls into these and then
renders the returned ``GeometrySet``s.

Design notes:
  * Uses ``fabconvert.api.read`` / ``fabconvert.api.write`` (public surface
    only).  Nothing internal to core/alignment/formats is imported.
  * All operations run under ``contextlib.redirect_stdout`` so that messages
    the library prints (DXF detection/auditor notes, GerberWriter polygon-loss
    NOTE) are captured rather than leaking to the GUI's owning console.
  * ``UnsupportedGerberConstruct`` is caught once and surfaced as a clean
    ``ConvertError`` with a friendly message.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import List, Optional, Tuple

from ..alignment import Alignment, Units
from ..api import read, write
from ..core.geometry import GeometrySet
from ..formats.gerber_io import UnsupportedGerberConstruct


class ConvertError(Exception):
    """A user-facing error from a convert/read operation (friendly message)."""


def _friendly(e: BaseException) -> str:
    if isinstance(e, UnsupportedGerberConstruct):
        return (f"Unsupported Gerber construct: {e}. Macro apertures, "
                f"G36/G37 region fills, and step-and-repeat blocks aren't "
                f"supported by fabconvert.")
    if isinstance(e, FileNotFoundError):
        return f"File not found: {e.filename or e}"
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, ModuleNotFoundError) and "ezdxf" in str(e).lower():
        return "DXF support requires the 'ezdxf' package: pip install fabconvert"
    return f"{type(e).__name__}: {e}"


@dataclass
class LoadResult:
    """Result of reading one file with :func:`fabconvert.api.read`."""
    geom: Optional[GeometrySet] = None
    alignment: Optional[Alignment] = None
    notes: str = ""
    error: Optional[ConvertError] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ConvertResult:
    """Result of converting one file: input load + write + output re-read."""
    src: Path
    dst: Path
    geom_in: Optional[GeometrySet] = None
    alignment_in: Optional[Alignment] = None
    geom_out: Optional[GeometrySet] = None
    alignment_out: Optional[Alignment] = None
    notes_in: str = ""
    notes_out: str = ""
    # When writing Gerber, filled polygons are silently dropped by the
    # writer (it prints a NOTE).  We flag it here so the GUI can warn.
    polygons_dropped: int = 0
    error: Optional[ConvertError] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def load_file(path: Path, unit_override: Optional[Units] = None) -> LoadResult:
    """Read a file through the public API; capture notes & errors."""
    buf = StringIO()
    try:
        with redirect_stdout(buf):
            geom, alignment = read(path, unit_override=unit_override)
    except (ValueError, UnsupportedGerberConstruct, FileNotFoundError,
            ModuleNotFoundError) as e:
        return LoadResult(notes=buf.getvalue(), error=ConvertError(_friendly(e)))
    except Exception as e:  # noqa: BLE001 — never crash the GUI
        return LoadResult(notes=buf.getvalue(), error=ConvertError(_friendly(e)))
    return LoadResult(geom=geom, alignment=alignment, notes=buf.getvalue())


def convert_one(src: Path, dst: Path,
                unit_override: Optional[Units] = None) -> ConvertResult:
    """Convert ``src`` → ``dst``; re-read ``dst`` for the live after-preview.

    Reads input (capturing notes), writes output; for Gerber output pre-checks
    ``geom.polygons`` so the caller can warn about silent polygon loss; then
    re-reads the written file so the OUTPUT canvas shows the honest result
    (which surfaces the polygon-drop visually).  Any step failing returns a
    ``ConvertResult`` with ``error`` set rather than raising.
    """
    res = ConvertResult(src=src, dst=dst)
    in_buf = StringIO()

    # Read input.
    try:
        with redirect_stdout(in_buf):
            geom, alignment = read(src, unit_override=unit_override)
    except (ValueError, UnsupportedGerberConstruct, FileNotFoundError,
            ModuleNotFoundError) as e:
        res.notes_in = in_buf.getvalue()
        res.error = ConvertError(_friendly(e))
        return res
    except Exception as e:  # noqa: BLE001
        res.notes_in = in_buf.getvalue()
        res.error = ConvertError(_friendly(e))
        return res

    res.geom_in = geom
    res.alignment_in = alignment
    res.notes_in = in_buf.getvalue()

    # Pre-warn: GerberWriter drops filled ClosedPolygons (prints a NOTE).
    if dst.suffix.lower() in (".gbr", ".gtl", ".gbo", ".gbs", ".gbl", ".gto",
                              ".gts", ".gko", ".gm1"):
        res.polygons_dropped = len(geom.polygons)

    # Write output.
    out_buf = StringIO()
    try:
        with redirect_stdout(out_buf):
            write(geom, dst)
    except (ValueError, UnsupportedGerberConstruct, FileNotFoundError,
            ModuleNotFoundError) as e:
        res.notes_out = out_buf.getvalue()
        res.error = ConvertError(_friendly(e))
        return res
    except Exception as e:  # noqa: BLE001
        res.notes_out = out_buf.getvalue()
        res.error = ConvertError(_friendly(e))
        return res
    res.notes_out = out_buf.getvalue()

    # Re-read the output for the after-view.
    reread = load_file(dst, unit_override=unit_override)
    res.geom_out = reread.geom
    res.alignment_out = reread.alignment
    # Append any reread notes to the output notes.
    if reread.notes:
        res.notes_out = (res.notes_out + reread.notes) if res.notes_out else reread.notes
    if reread.error is not None:
        # Writing succeeded but re-reading failed — unusual, but surface it.
        res.error = reread.error
    return res


def convert_batch(files: List[Tuple[Path, Path]],
                   unit_override: Optional[Units] = None
                   ) -> List[ConvertResult]:
    """Convert a list of (src, dst) pairs; per-file errors don't abort."""
    return [convert_one(src, dst, unit_override=unit_override)
            for src, dst in files]
