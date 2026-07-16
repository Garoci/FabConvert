# GUI guide

The fabconvert desktop app is a **PySide6** window with a light/dark theme,
drag & drop input, a settings row, two side-by-side live geometry canvases,
an info strip, and clean error dialogs. It installs only with the optional
`[gui]` extra — the core library and CLI stay Qt-free.

## Install

```bash
pip install "fabconvert[gui]"
```

That adds `PySide6`. The base `pip install .` does **not** pull PySide6.

## Launch

Three equivalent entry points:

```bash
fabconvert gui          # from the CLI
fabconvert-gui          # standalone console script
python -m fabconvert.gui
```

If PySide6 is missing, all three print a yellow panel with the install hint
(`pip install "fabconvert[gui]"`) and exit 1 — never a raw traceback. The
PySide6 import is deferred inside `launch()`/`main()` (shared `_import_qt()`
helper), so merely importing `fabconvert.gui` can't raise.

## Window layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  fabconvert                       SVG ⇄ DXF ⇄ Gerber · drag, convert… [🌙 Dark] │
├──────────────────────────────────────────────────────────────────────┤
│  Input files                                                          │
│  ┌────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │ Drop .svg/.dxf/.gbr (+ .gtl │  │ board.gtl            ▢ board.gbr │ │
│  │ .gbo .gbs .gbl .gto ...)    │  │ board.dxf                        │ │
│  └────────────────────────────┘  │ [Browse…] [Clear]                │ │
│                                   └──────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  Output format: Gerber (.gbr) ▾   Output dir: …⟂ [Directory…]          │
│  Unit override: Auto ▾                                  [ Convert ]   │
├──────────────────────────────────────────────────────────────────────┤
│  INPUT                          │  OUTPUT (after)                       │
│  ┌────────────────────────────┐ │ ┌──────────────────────────────┐    │
│  │  ╭───────────╮             │ │ │             ╭───────────╮      │    │
│  │  │ geometry  │             │ │ │             │ geometry  │      │    │
│  │  ╰───────────╯             │ │ │             ╰───────────╯      │    │
│  └────────────────────────────┘ │ └──────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────────┤
│  Info: board.gtl  format: GTL  unit: mm  note: %MOMM*%  …            │
└──────────────────────────────────────────────────────────────────────┘
```

- **Input files** — drag files onto the drop area (filter: `.svg`, `.dxf`, and
  `.gbr/.gtl/.gbo/.gbs/.gbl/.gto/.gts/.gko/.gm1`), or **Browse…** to pick
  (multi-select). Listed files are kept; **Clear** empties the list. Selecting
  a file immediately loads it into the INPUT canvas.
- **Settings**
  - *Output format* — SVG / DXF / Gerber (.gbr).
  - *Output dir* — destination directory (picked or typed; created if missing).
  - *Unit override* — Auto / mm / inch / mil / cm / m. Forwarded as
    `unit_override` to `read()`.
  - *Convert* — primary button; writes the output and re-reads it into the
    OUTPUT canvas.
- **Preview** — two `GeometryCanvas` panels. The INPUT panel renders the read
  geometry on selection; the OUTPUT panel renders the **re-read converted
  file** after Convert, so you can visually confirm orientation / Y-flip /
  scale across the pair (exactly what `Alignment` guarantees). Each canvas
  draws a faint grid, a dashed bounding box for orientation, and a scale
  readout (`mm × mm · 1 px ≈ N mm`).
- **Info strip** — detected unit, `detection_note` (warning-coloured when a
  guess was made), bounding box, and per-type entity counts. Captured
  library stdout is shown here.
- **Tabs** — appear after a batch run with a per-file ✓/✗ results table.

## Workflows

### Single file

1. Drag a file (or **Browse…**). The INPUT canvas renders immediately; the
   info strip shows its detected unit + note + bbox + counts.
2. Pick the output format and directory.
3. (Optional) pick a unit override if the file is ambiguous.
4. **Convert**. The OUTPUT canvas shows the re-read result. If you converted
   to Gerber and the source had filled polygons, the info strip flags how
   many were dropped (see [Format coverage](formats.md)).

### Batch

1. Add multiple files (drag a folder's worth or browse multi-select).
2. Pick one output format + one output directory.
3. **Convert**. Each file is written `<stem>.<format>` into the directory; a
   **Results** tab lists each file's status and the reason for any failure.
   One bad file doesn't stop the rest.

### Theme

**🌙 Dark / ☀ Light** (top-right) toggles an embedded flat QSS stylesheet;
both use rounded corners, an accent colour, and the system font. The choice
applies immediately and lasts for the session.

## Error handling

All library errors are shown as dialogs — never raw tracebacks:

| Cause | Message |
|-------|---------|
| Unrecognized extension | "Unrecognized extension / cannot read X" (a `ValueError`). |
| Unsupported Gerber construct | "Unsupported Gerber construct: <e>. Macro apertures, G36/G37 region fills, and step-and-repeat blocks aren't supported." (`UnsupportedGerberConstruct`.) |
| Missing `ezdxf` | "DXF support requires the 'ezdxf' package: pip install fabconvert". |
| Corrupt / unparseable file | A short summary. |

Every error dialog has a **Details…** expander containing the full traceback,
so you can still debug when something is wrong. The underlying logic lives in
`fabconvert.gui.convertworker` (pure Python, no Qt) which calls the public API
and runs each operation under `redirect_stdout` so library notes don't leak.
