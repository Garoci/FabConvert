# CLI reference

The `fabconvert` command is built with [click](https://click.palletsprojects.com/)
for parsing and [rich](https://rich.readthedocs.io/) for colored tables,
panels, and progress bars. It lives in `fabconvert/cli.py` and is exposed as
the `fabconvert` console script.

```text
fabconvert convert <input> <output> [--unit mm|inch|mil|cm|m]
fabconvert info    <file>             [--unit mm|inch|mil|cm|m]
fabconvert batch   <input_dir> <output_dir> --to dxf|svg|gbr
                    [--pattern "*.svg"] [--unit mm|inch|mil|cm|m]
fabconvert gui
fabconvert --version
```

All output and errors go to **stderr** (`Console(stderr=True)`), so you can
pipe converted-file lists on stdout independently if you build your own
wrapper — though fabconvert doesn't itself print file lists to stdout.

## Global

| Flag          | Effect                                              |
|---------------|-----------------------------------------------------|
| `--version`   | Print `fabconvert, version X.Y.Z` and exit 0.      |
| `--help` / `-h` on any subcommand | Print that subcommand's help.        |

Running `fabconvert` with no subcommand prints the group help.

## `convert`

Convert one file. Dispatch is purely on the extensions of `<input>` and
`<output>`, so all six directions are available through this one subcommand.

```bash
fabconvert convert board.svg board.dxf
fabconvert convert board.dxf board.svg --unit inch
fabconvert convert board.gtl board.gbr
```

**Arguments**

| Argument  | Required | Description                                   |
|-----------|----------|-----------------------------------------------|
| `INPUT`   | yes      | Input file. Must exist; extension must be `.svg`, `.dxf`, or one of the Gerber extensions. |
| `OUTPUT`  | yes      | Output file path. Extension selects the writer. |

**Options**

| Option                | Default | Description |
|-----------------------|---------|-------------|
| `--unit {mm,inch,mil,cm,m}` | *none* | Force a unit on unitless/ambiguous DXF or Gerber input. Ignored for SVG. |

**Output** — source→target formats, detected unit, `detection_note` (when
non-empty), bounding box in mm, an entity-count table
(lines/arcs/circles/polygons/paths), and elapsed time. Captured library stdout
(DXF detection/auditor notes, Gerber polygon-loss NOTE) is printed indented
under `library notes:`.

**Exit codes**

| Code | Meaning |
|------|---------|
| 0    | Conversion succeeded and the output file was written. |
| 1    | A library-level error: unrecognized extension, `UnsupportedGerberConstruct`, corrupt/unreadable file, or missing `ezdxf`. A red `convert failed` panel names the problem. |
| 2    | A click validation error (e.g. `INPUT` file does not exist, unknown `--unit` value). |

## `info`

Read one file with `fabconvert.read()` and print a summary — no conversion,
no output file written. Useful for sanity-checking a file's detected unit and
entity counts before converting.

```bash
fabconvert info board.gbr
fabconvert info weird.dxf --unit mil
```

Prints a table: `file`, `format` (from suffix), `detected unit`,
`detection note`, `bounding box` (W × H mm + corners), and `entities` per
type, followed by the same entity-count table. Exit codes mirror `convert`
(0 / 1 / 2).

## `batch`

Convert every matching file in `INPUT_DIR` to the target format in
`OUTPUT_DIR`. A single bad file does **not** abort the batch.

```bash
fabconvert batch ./in ./out --to dxf --pattern "*.svg"
fabconvert batch ./gerbers ./out --to svg --unit mm
```

**Arguments**

| Argument    | Required | Description |
|-------------|----------|-------------|
| `INPUT_DIR`  | yes | Directory to scan. Must exist. |
| `OUTPUT_DIR` | yes | Destination directory. Created if missing. |

**Options**

| Option                          | Default | Description |
|---------------------------------|---------|-------------|
| `--to {dxf,svg,gbr}`            | *required* | Target format extension. (`gbr` is used as the representative Gerber suffix for outputs, regardless of the input's Gerber variant.) |
| `--pattern PATTERN`             | `*`     | Glob for input filenames. Files with unsupported extensions are skipped (not errors). |
| `--unit {mm,inch,mil,cm,m}`     | *none*  | Forced unit, forwarded as `unit_override` to every read. |

**Behavior** — for each match, the output path is `OUTPUT_DIR/<stem>.<to>`.
A rich progress bar shows completion. At the end, a `batch summary` table
lists each file (`<input>`, status ✓/✗, reason). The final line reports
`N succeeded, M failed`.

**Exit codes**

| Code | Meaning |
|------|---------|
| 0    | All matched files converted (or none matched — a yellow "no convertible files" line is printed). |
| 1    | At least one file failed; the table shows the per-file reason. |
| 2    | A click validation error (bad dir, missing `--to`, unknown `--unit`). |

## `gui`

Open the desktop GUI. Requires the `[gui]` extra:

```bash
fabconvert gui
```

If PySide6 isn't installed, the command prints a yellow panel with the
exact install hint:

```
pip install "fabconvert[gui]"
```

and exits 1 — never a raw traceback. See the [GUI guide](gui.md).

**Exit codes**: 0 on normal window close; 1 if the GUI extra is missing or a
GUI launch error occurs.

## Exit-code semantics (summary)

- `0` — success.
- `1` — a fabconvert-level failure surfaced as a friendly panel/dialog.
- `2` — a click-level argument-validation failure (bad path / unknown choice).
