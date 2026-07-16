"""Command-line entry point for fabconvert.

Provides the subcommands:

    fabconvert convert  <input> <output> [--unit mm|inch|mil|cm|m]
    fabconvert info     <file>
    fabconvert batch    <input_dir> <output_dir> --to dxf|svg|gbr
                      [--pattern "*.svg"] [--unit mm|inch|mil|cm|m]
    fabconvert gui
    fabconvert --version

Built with ``click`` (option parsing) and ``rich`` (colored tables, panels,
progress bars).  The library import path is the public one
(``fabconvert.api``, ``fabconvert.alignment.Units``); no internal readers/writers
are touched.

Backward compatible with the previous argparse CLI in spirit:
``fabconvert convert in.svg out.dxf --unit mm`` parses and behaves exactly as
before (the only change is richer, more informative output).

API notes that shape this file:
  * ``fabconvert.api.convert`` discards the ``Alignment`` (``geom, _ = read``),
    so ``convert``/``info`` here call ``fabconvert.api.read`` directly to surface
    the detected unit + ``detection_note``.
  * ``fabconvert.formats.gerber_io.UnsupportedGerberConstruct`` is caught once
    and its message relayed to the user (covers AM macros, G36/G37 regions,
    step-and-repeat, missing %FS, non-circular apertures, empty files).
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .alignment import Units
from .api import read, write
from .formats.gerber_io import UnsupportedGerberConstruct

# Accepted file extensions (lowercase, dot-prefixed).  Identical for read()
# and write() in api.py; kept here so the CLI's error messages and batch
# filtering share one source of truth.
SVG_EXTS = (".svg",)
DXF_EXTS = (".dxf",)
GERBER_EXTS = (".gbr", ".gtl", ".gbo", ".gbs", ".gbl", ".gto", ".gts",
               ".gko", ".gm1")
ALL_EXTS = SVG_EXTS + DXF_EXTS + GERBER_EXTS

# Map a --unit string to a Units enum member.
UNIT_CHOICES = ("mm", "inch", "mil", "cm", "m")
_UNIT_MAP = {
    "mm": Units.MM,
    "inch": Units.INCH,
    "mil": Units.MIL,
    "cm": Units.CM,
    "m": Units.M,
}

console = Console(stderr=True)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _format_label(ext: str) -> str:
    """A human format name for an extension."""
    if ext in SVG_EXTS:
        return "SVG"
    if ext in DXF_EXTS:
        return "DXF"
    if ext in GERBER_EXTS:
        return "Gerber"
    return ext.upper().lstrip(".")


def _entity_counts(geom) -> dict:
    return {
        "lines": len(geom.lines),
        "arcs": len(geom.arcs),
        "circles": len(geom.circles),
        "polygons": len(geom.polygons),
        "paths": len(geom.paths),
    }


def _fmt_bounds(b) -> str:
    if b is None:
        return "— (empty)"
    xmin, ymin, xmax, ymax = b
    return (f"{(xmax - xmin):.3f} × {(ymax - ymin):.3f} mm "
            f"(xmin={xmin:.3f}, ymin={ymin:.3f}, xmax={xmax:.3f}, "
            f"ymax={ymax:.3f})")


def _counts_table(counts: dict, title: str = "Entities") -> Table:
    t = Table(title=title, show_header=True, header_style="bold cyan",
              expand=False)
    t.add_column("lines", justify="right")
    t.add_column("arcs", justify="right")
    t.add_column("circles", justify="right")
    t.add_column("polygons", justify="right")
    t.add_column("paths", justify="right")
    t.add_row(*(str(counts[k]) for k in
                ("lines", "arcs", "circles", "polygons", "paths")))
    return t


@contextmanager
def _capture_stdout():
    """Capture library stdout (detection notes / silent-loss warnings)."""
    buf = StringIO()
    with redirect_stdout(buf):
        yield buf


def _friendly_error(e: BaseException) -> str:
    """Translate a library exception into a short user-facing message."""
    if isinstance(e, UnsupportedGerberConstruct):
        return (f"Unsupported Gerber construct: {e}. Macro apertures, "
                f"G36/G37 region fills, and step-and-repeat blocks are not "
                f"supported.")
    if isinstance(e, FileNotFoundError):
        return f"File not found: {e.filename or e}"
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, ModuleNotFoundError) and "ezdxf" in str(e).lower():
        return "DXF support requires the 'ezdxf' package: pip install fabconvert"
    return f"{type(e).__name__}: {e}"


def _resolve_unit(unit: Optional[str]) -> Optional[Units]:
    if not unit:
        return None
    return _UNIT_MAP[unit]


def _emit_notes(buf: StringIO) -> None:
    """Print captured library stdout as indented informational lines."""
    text = buf.getvalue().strip()
    if text:
        console.print("[dim]library notes:[/dim]")
        for line in text.splitlines():
            console.print(f"[dim]    {line}[/dim]")


# ----------------------------------------------------------------------------
# Click group
# ----------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="fabconvert")
@click.pass_context
def fabconvert(ctx: click.Context) -> None:
    """Convert PCB fabrication files between SVG ⇄ DXF ⇄ Gerber."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---- convert ---------------------------------------------------------------

@fabconvert.command()
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--unit", "unit", type=click.Choice(UNIT_CHOICES),
              default=None,
              help="force a unit on unitless/ambiguous DXF/Gerber input")
def convert(input: str, output: str, unit: Optional[str]) -> None:
    """Convert a single file from one format to another."""
    override = _resolve_unit(unit)
    src = Path(input)
    dst = Path(output)
    src_ext = src.suffix.lower()
    dst_ext = dst.suffix.lower()

    t0 = time.perf_counter()
    try:
        with _capture_stdout() as buf:
            geom, alignment = read(src, unit_override=override)
            write(geom, dst)
    except (ValueError, UnsupportedGerberConstruct, FileNotFoundError,
            ModuleNotFoundError) as e:
        console.print(Panel(_friendly_error(e),
                            title="[bold red]convert failed[/bold red]",
                            border_style="red"))
        _emit_notes(buf)
        _exit_with_error()
        return
    except Exception as e:  # noqa: BLE001 — last-resort, never crash
        console.print(Panel(_friendly_error(e),
                            title="[bold red]convert failed[/bold red]",
                            border_style="red"))
        _emit_notes(buf)
        _exit_with_error()
        return
    elapsed = time.perf_counter() - t0

    detected = (alignment.detected_unit.value
                if alignment.detected_unit is not None else "—")
    note = alignment.detection_note or ""

    console.print(f"[bold green]✓[/bold green] "
                  f"[cyan]{_format_label(src_ext)}[/cyan] "
                  f"[bold]{src}[/bold] → "
                  f"[cyan]{_format_label(dst_ext)}[/cyan] "
                  f"[bold]{dst}[/bold]")
    console.print(f"    detected unit: [yellow]{detected}[/yellow]")
    if note:
        console.print(f"    note: [yellow]{note}[/yellow]")
    console.print(f"    bounds: {_fmt_bounds(geom.bounds())}")
    console.print(_counts_table(_entity_counts(geom)))
    console.print(f"    elapsed: {elapsed * 1000:.1f} ms")
    _emit_notes(buf)


# ---- info ------------------------------------------------------------------

@fabconvert.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--unit", "unit", type=click.Choice(UNIT_CHOICES),
              default=None,
              help="force a unit on unitless/ambiguous DXF/Gerber input")
def info(file: str, unit: Optional[str]) -> None:
    """Read a file and print a summary — no conversion."""
    override = _resolve_unit(unit)
    p = Path(file)
    ext = p.suffix.lower()

    buf = StringIO()
    try:
        with _capture_stdout() as cap:
            geom, alignment = read(p, unit_override=override)
            buf = cap
    except (ValueError, UnsupportedGerberConstruct, FileNotFoundError,
            ModuleNotFoundError) as e:
        console.print(Panel(_friendly_error(e),
                            title="[bold red]info failed[/bold red]",
                            border_style="red"))
        _emit_notes(cap)
        _exit_with_error()
        return
    except Exception as e:  # noqa: BLE001
        console.print(Panel(_friendly_error(e),
                            title="[bold red]info failed[/bold red]",
                            border_style="red"))
        _emit_notes(cap)
        _exit_with_error()
        return

    detected = (alignment.detected_unit.value
                if alignment.detected_unit is not None else "—")
    note = alignment.detection_note or "—"

    t = Table(title=f"fabconvert — {p.name}", show_header=True,
              header_style="bold cyan", expand=False)
    t.add_column("field", style="bold")
    t.add_column("value")
    t.add_row("file", str(p))
    t.add_row("format", _format_label(ext))
    t.add_row("detected unit", detected)
    t.add_row("detection note", note)
    t.add_row("bounding box", _fmt_bounds(geom.bounds()))
    counts = _entity_counts(geom)
    t.add_row("entities",
              ", ".join(f"{k}={v}" for k, v in counts.items() if v) or
              "(none)")
    console.print(t)
    if any(counts.values()):
        console.print(_counts_table(counts))
    _emit_notes(buf)


# ---- batch -----------------------------------------------------------------

@fabconvert.command()
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False))
@click.argument("output_dir", type=click.Path(file_okay=False))
@click.option("--to", "to_fmt", required=True,
              type=click.Choice(["dxf", "svg", "gbr"]),
              help="target format extension")
@click.option("--pattern", "pattern", default="*",
              show_default=True,
              help="glob pattern for input files (e.g. '*.svg')")
@click.option("--unit", "unit", type=click.Choice(UNIT_CHOICES),
              default=None,
              help="force a unit on unitless/ambiguous DXF/Gerber input")
def batch(input_dir: str, output_dir: str, to_fmt: str, pattern: str,
          unit: Optional[str]) -> None:
    """Convert every matching file in INPUT_DIR to OUTPUT_DIR.

    A single bad file does not abort the batch.  Output files are named
    <stem>.<to> in OUTPUT_DIR.
    """
    override = _resolve_unit(unit)
    in_dir = Path(input_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    matches = sorted(
        p for p in in_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in ALL_EXTS
    )
    if not matches:
        console.print(f"[yellow]No convertible files matching "
                      f"{pattern!r} in {in_dir}[/yellow]")
        return

    results = []  # (path, status, reason)
    from rich.progress import (BarColumn, Progress, TextColumn,
                               TimeElapsedColumn)

    with Progress(TextColumn("[progress.description]{task.description}"),
                  BarColumn(), TextColumn("{task.completed}/{task.total}"),
                  TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("[cyan]converting…[/cyan]", total=len(matches))
        for src in matches:
            dst = out_dir / f"{src.stem}.{to_fmt}"
            try:
                with _capture_stdout():
                    geom, _ = read(src, unit_override=override)
                    write(geom, dst)
                results.append((src, True, ""))
            except (ValueError, UnsupportedGerberConstruct,
                    FileNotFoundError, ModuleNotFoundError) as e:
                results.append((src, False, _friendly_error(e)))
            except Exception as e:  # noqa: BLE001
                results.append((src, False, _friendly_error(e)))
            prog.advance(task)

    n_ok = sum(1 for _, ok, _ in results if ok)
    n_bad = len(results) - n_ok

    t = Table(title="batch summary", show_header=True,
              header_style="bold cyan", expand=True)
    t.add_column("file", overflow="fold")
    t.add_column("status", justify="center")
    t.add_column("reason", overflow="fold")
    for src, ok, reason in results:
        rel = src.relative_to(in_dir) if src.is_relative_to(in_dir) else src
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        t.add_row(str(rel), status, reason)
    console.print(t)
    console.print(f"[bold]{n_ok}[/bold] succeeded, "
                  f"[bold red]{n_bad}[/bold red] failed.")
    if n_bad:
        _exit_with_error()


# ---- gui -------------------------------------------------------------------

@fabconvert.command()
def gui() -> None:
    """Open the graphical interface (requires the [gui] extra)."""
    try:
        from .gui import launch
    except ModuleNotFoundError as e:
        from rich.text import Text
        console.print(Panel(
            Text(f"The GUI requires PySide6, which isn't installed.\n\n"
                 f"    pip install \"fabconvert[gui]\"\n\n"
                 f"(original import error: {e})"),
            title="[bold yellow]GUI not available[/bold yellow]",
            border_style="yellow"))
        _exit_with_error()
        return
    rc = launch()
    if rc:
        # propagate a non-zero GUI launch result (e.g. missing deps) as a
        # process exit code.
        raise SystemExit(int(rc))


def _exit_with_error() -> None:
    # NOTE: deliberately SystemExit — not click.exceptions.Exit. click swallows
    # its own Exit (silently returns None) under standalone_mode=False; SystemExit
    # is a BaseException so it propagates past click, and main() converts it to a
    # return code. The per-command `except Exception` can't catch it (BaseException).
    raise SystemExit(1)


def main(argv=None) -> int:
    """Console entry point. Returns a process exit code."""
    try:
        fabconvert.main(args=argv, standalone_mode=False)
    except click.exceptions.Exit as e:
        return int(e.exit_code)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
