"""Unit-conversion constants for the fabconvert intermediate model.

The intermediate (:mod:`fabconvert.core.geometry`) model always stores
coordinates in **millimetres**.  Every reader converts source-format units
into mm on the way in (via an :class:`~fabconvert.alignment.Alignment`), and
every writer converts mm back out into the destination format's units.  No
format module performs a unit conversion on its own — the scale factor lives
on the :class:`~fabconvert.alignment.Alignment` object and is applied here.

The constants below are the single source of truth for "how many mm in one X".
They mirror the ``$INSUNITS`` integer codes defined by the DXF specification,
so a DXF reader can index this table directly.  Gerber only distinguishes mm
vs. inches (``%MOMM*%`` / ``%MOIN*%``), but the same table covers it.
"""

from __future__ import annotations

# Mapping of DXF ``$INSUNITS`` integer codes → mm-per-drawing-unit.
# Verified against ezdxf 1.4.4 and the real KiCad fixtures in tests/fixtures.
#   0 = Unitless (handled by alignment.detect, defaults to 1.0 here)
#   1 = Inches, 4 = Millimeters, 9 = Mils are the practically common ones.
INSUNITS_TO_MM: dict[int, float] = {
    0: 1.0,          # Unitless — assume drawing unit == 1 mm unless detect() overrules
    1: 25.4,         # Inches → mm
    2: 304.8,        # Feet → mm
    3: 1_609_344.0,  # Miles → mm
    4: 1.0,          # Millimeters
    5: 10.0,         # Centimeters → mm
    6: 1000.0,       # Meters → mm
    7: 1_000_000.0,  # Kilometers → mm
    8: 2.54e-5,      # Microinches → mm
    9: 0.0254,       # Mils → mm
    10: 914.4,       # Yards → mm
    11: 1e-7,        # Angstroms → mm
}

# Human-readable names for the codes that actually occur in PCB work.
INSUNITS_NAMES: dict[int, str] = {
    0: "Unitless",
    1: "Inches",
    2: "Feet",
    4: "Millimeters",
    5: "Centimeters",
    6: "Meters",
    9: "Mils",
}

# The intermediate model's working unit.  Everything downstream assumes mm.
INTERMEDIATE_UNIT_MM: float = 1.0
