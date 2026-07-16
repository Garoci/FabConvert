# Changelog

All notable changes to fabconvert are documented here. The package version is
`fabconvert.__version__` (currently `0.1.0`), which `pyproject.toml` reads
dynamically — so the version here, `--version`, and the installed wheel always
agree.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and
this project adheres to [Semantic Versioning](https://semver.org/) (pre-1.0,
so minor bumps may include breaking changes).

## [0.1.0] — initial public release

The SVG ⇄ DXF ⇄ Gerber conversion library, plus packaging, a modern CLI, and
an optional PySide6 GUI.

### Added
- **Library** (`fabconvert.api`): `read`, `write`, `convert`, and the named
  `svg_to_dxf` / `svg_to_gerber` / `dxf_to_svg` / `gerber_to_svg`
  convenience functions, all re-exported at the package root.
- **Intermediate geometry model** (`fabconvert.core.geometry`): `GeometrySet`
  with `lines/arcs/circles/polygons/paths`, `bounds()`, `is_empty`, `__len__`;
  coordinates in mm, Y-down. Widths carried as `stroke_width` (mm), not baked
  into geometry.
- **Alignment** (`fabconvert.alignment`): `Units` enum (MM/INCH/MIL/CM/M/
  UNITLESS) and the `Alignment` transform that owns unit scale, Y-flip, origin,
  detected unit, and detection note. Centralised Y-flip about the bbox
  midline (golden rule zero).
- **Formats** (`fabconvert.formats`): `SvgReader/SvgWriter`, `DxfReader/DxfWriter`
  (via `ezdxf`, lazily imported), `GerberReader/GerberWriter`, plus the
  `UnsupportedGerberConstruct` exception raised for macro apertures, G36/G37
  region fills, step-and-repeat, missing `%FS`, non-circular apertures, and
  empty files.
- **Packaging** (`pyproject.toml`): setuptools backend; `dynamic = ["version"]`
  reading `fabconvert.__version__`; explicit `packages` list (no
  auto-discovery, so the unrelated C++ sibling is never packaged); core deps
  `ezdxf`, `click`, `rich`; extras `[gui]=PySide6`, `[dev]=pytest/ruff/build`;
  console scripts `fabconvert` (`fabconvert.cli:main`) and `fabconvert-gui`
  (`fabconvert.gui:main`).
- **CLI** (`fabconvert.cli`, click + rich): `convert`, `info`, `batch`,
  `gui`, `--version`. Rich tables/panels/progress; stdout-capture surfaces
  silent library notes; friendly error panels with non-zero exit codes;
  backward-compatible `convert in out --unit ...`.
- **GUI** (`fabconvert.gui`, PySide6, optional): drag & drop + picker for the
  full extension set; output-format/dir/unit-override controls; two
  side-by-side `GeometryCanvas` panels (input + re-read output); info strip
  (unit, note, bbox, counts); light/dark theme; batch results tab; clean
  error dialogs with a Details expander. PySide6 imported lazily inside both
  `launch()` and `main()`, so a CLI-only install and the standalone
  `fabconvert-gui` entry both print a friendly `pip install "fabconvert[gui]"`
  message when the extra is missing.
- **Docs**: README plus this `docs/` tree (quick start, CLI reference, library
  reference, GUI guide, format coverage, troubleshooting, contributing,
  changelog).

### Notes
- Round trips SVG→DXF→SVG and SVG→GBR→SVG are exercised by the bundled test
  suite at 1 µm tolerance against real KiCad fixtures.
- Known limitation: converting filled polygons **into** Gerber drops them
  (the writer emits centrelines + aperture widths and prints a NOTE; no
  exception). The CLI surfaces the NOTE; the GUI pre-counts and warns.
