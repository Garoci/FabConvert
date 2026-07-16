# Troubleshooting & errors

## "unrecognised input/output extension: '.txt'"

You passed a file whose suffix isn't one of `.svg`, `.dxf`, or the Gerber set
(`.gbr/.gtl/.gbo/.gbs/.gbl/.gto/.gts/.gko/.gm1`). Both `read()` and `write()`
raise `ValueError` for this; the CLI shows a red panel and exits 1, the GUI
shows a dialog.

- **Fix:** rename/copy the file to a supported extension, or confirm the file
  is actually one fabconvert handles. `.svgz`/`.dwg` are **not** supported.

## "Unsupported Gerber construct: …"

`UnsupportedGerberConstruct` is raised on read for features fabconvert
deliberately doesn't model as centreline + width:

- AM macro apertures (`%AM`)
- region fills (`G36`/`G37`)
- step-and-repeat blocks (`%SR`)
- missing `%FS` coordinate format
- non-circular apertures (e.g. rectangle `R`)
- files with no draws and no flashes

```python
from fabconvert.formats.gerber_io import UnsupportedGerberConstruct

try:
    geom, _ = fabconvert.read("board.gbr")
except UnsupportedGerberConstruct as e:
    print("unsupported:", e)
```

- **Fix:** re-export the board from your CAD tool using simple round (`C`)
  apertures and no region fills / step-and-repeat / macros. Many KiCad/Altium
  outputs already satisfy this.

## The Gerber output is missing my filled copper pour

This is **expected**: the Gerber writer represents traces as centrelines +
aperture widths and can't represent a filled region. When this happens the
writer prints a NOTE to stdout. The CLI surfaces it under `library notes:`;
the GUI's info strip flags the count and warns before writing.

- **Fix:** if you need filled copper on a Gerber path, keep it as Gerber
  (don't round-trip through SVG/DXF), or represent the pour as a stroked
  outline + a hatch. fabconvert intentionally doesn't silently fake it.

## My DXF came out as a filled outline, not a thin line

This is **by design**: DXF has no stroke-width property, so the writer emits a
filled outline reconstructed from the centreline + width. It's documented
behaviour, not a silent workaround.

## The board looks mirrored / upside down

Likely a unit/Y-flip issue. Confirm with `fabconvert info` — check `detected
unit` and `detection_note`:

- If a unitless DXF reported `mm` but it's really inches, force it:
  `fabconvert convert in.dxf out.svg --unit inch`.
- SVG input that looks mirrored: SVG is Y-down by spec; the DXF/Gerber writers
  flip about the **bounding-box midline** to land the geometry at the same
  absolute extents. If you integrated fabconvert with hand-rolled alignment
  *around* it, don't apply a second flip — `Alignment` already owns the flip
  (golden rule zero).

## "DXF support requires the 'ezdxf' package"

You tried a DXF read or write but `ezdxf` isn't importable. `ezdxf` is a core
dependency (declared in `pyproject.toml`), so a normal `pip install .` brings
it in; this message means the install is broken or you're running outside the
installed environment. Fix: `pip install fabconvert` (or reinstall).

## GUI: "GUI not available" / `ModuleNotFoundError: PySide6`

The GUI is an optional extra. Install it:

```bash
pip install "fabconvert[gui]"
```

This is the exact line both `fabconvert gui` and the standalone `fabconvert-gui`
print when PySide6 is missing.

## Batch exit code is 1 even though most files converted

`batch` exits non-zero if **any** file failed — even one out of a hundred.
Inspect the `batch summary` table; the `reason` column names the per-file
failure. Successes are unaffected (their output files are still written).

## Which `fabconvert` am I importing?

If a sibling project puts a different copy on `sys.path`, confirm:

```bash
python -c "import fabconvert; print(fabconvert.__file__)"
```

`pip install .` installs the root `fabconvert/` package, so `__file__` should
point there. If it points elsewhere, uninstall/reinstall or clean up the
conflicting `sys.path` entry.

## Running the CLI and getting exit code 2 instead of 1

Exit `2` is a click argument-validation error (e.g. `INPUT` doesn't exist,
unknown `--unit` value) — it's caught before fabconvert runs. Exit `1` is a
fabconvert-level failure (unrecognized extension, unsupported construct,
corrupt file, missing deps). See [CLI reference → Exit codes](cli.md).
