"""Regression test: routed traces whose DXF walls close into a loop.

This is the companion to ``fabconvert/formats/dxf_io.py``'s precedence fix.
Bug class: a KiCad ``F_Cu`` DXF plot where each routed trace's two parallel
walls + round end-caps are emitted as one fully-closed "stadium/racetrack"
loop (a closed chain).  The closed-boundary heuristic
(:func:`fabconvert.formats.edgepair.lines_form_closed_boundary`) reads such a
loop as a copper pour / filled-outline region and — under the old precedence —
pre-empted :func:`pair_parallel_edges` entirely, so every routed trace was
emitted as a filled polygon instead of a recovered centreline, and
``GerberWriter`` then *dropped* those polygons (no G36/G37 support), leaving
a ``.gbr`` containing only the pad flashes (D03) and zero draw operations
(D01).  Converting the same file to ``.svg`` looked fine, because the SVG
writer renders filled polygons directly — masking the regression.

WHY THIS FIXTURE MATTERS (the test2-vs-test6 distinction)
---------------------------------------------------------
``tests/fixtures/test2-F_Cu.dxf`` is *the* file where a trace's walls +
end-caps close into a single loop.  That distinguishes it from a (real-world)
``test6-F_Cu.dxf`` plot, where the end-caps *don't* close the loop by
themselves and the closed-boundary heuristic therefore does NOT fire.  Before
the precedence fix:

  * ``test6``: open walls  -> ``lines_form_closed_boundary`` is_boundary=False
    -> pairing runs -> centrelines emitted.  Worked.
  * ``test2``: closed loop -> ``lines_form_closed_boundary`` is_boundary=True
    -> pairing NEVER ran -> filled polygons emitted -> GerberWriter dropped
    them -> ``.gbr`` had only pad flashes.  BROKEN.

Both files are genuine routed traces; "the walls close" is a side effect of
*how KiCad discretised the end-caps for that particular plot*, not a property
of whether the geometry is traces vs. a pour.  The one reliable signal that the
geometry *is* routed-trace wall-pairs is that ``pair_parallel_edges`` already
decomposed the long edges into parallel pairs with **zero unpaired long
edges** — so the fix makes a clean edge-pairing take precedence over the
closed-boundary heuristic (run pairing first; only fall through to the
heuristic when pairing is *not* clean).

On ``test2-F_Cu.dxf`` (units: the heuristic detects inch from the ~1.06
drawing-unit extent, mm_per_unit=25.4) ``pair_parallel_edges`` returns 19
wall-pairs with 0 unpaired long edges, recovering width 0.200 mm on the
genuine trace walls — the clean decomposition that should, and now does,
select centreline emission.
"""
from __future__ import annotations

import pathlib

import pytest

import fabconvert
from fabconvert.formats.edgepair import (
    lines_form_closed_boundary, pair_parallel_edges,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TEST2 = FIXTURES / "test2-F_Cu.dxf"


def _count_ops(gbr_text: str) -> dict[str, int]:
    """Count Gerber D01 draws, D02 light-off moves, D03 flashes."""
    counts: dict[str, int] = {"D01": 0, "D02": 0, "D03": 0}
    for ln in gbr_text.splitlines():
        for code in ("D01", "D02", "D03"):
            if ln.endswith(code + "*"):
                counts[code] += 1
                break
    return counts


# ---------------------------------------------------------------------------
# test2-F_Cu.dxf: closed trace loops — the regression itself.
# ---------------------------------------------------------------------------

def test_test2_fixture_exists():
    """Guard: the permanent fixture is committed where the test expects it."""
    assert TEST2.is_file(), f"missing fixture: {TEST2}"


def test_test2_clean_pairing_pre_empts_closed_boundary():
    """The structural justification for the precedence fix, asserted directly.

    On test2-F_Cu.dxf BOTH signals fire, but the clean pairing (the strong
    signal) must win over the closed loop (the weak, necessary-only signal).
    If this ever stops holding, the precedence fix has been undone.
    """
    geom, _ = fabconvert.read(str(TEST2))
    # Re-derive the two classification signals in isolation (drawing units ->
    # the reader's alignment scale) to assert their relationship without tying
    # the test to reader internals that may change.
    import ezdxf
    doc = ezdxf.readfile(str(TEST2))
    msp = doc.modelspace()
    line_dicts = [
        {"x1": e.dxf.start.x, "y1": e.dxf.start.y,
         "x2": e.dxf.end.x, "y2": e.dxf.end.y}
        for e in msp if e.dxftype() == "LINE"
    ]
    sc = fabconvert.read(str(TEST2))[1].unit_scale
    pairs, unpaired_long, _leftover = pair_parallel_edges(line_dicts, sc)
    _polys, is_boundary = lines_form_closed_boundary(line_dicts, sc)
    # The clean decomposition: every long wall found a parallel partner.
    assert len(pairs) > 0 and unpaired_long == 0, (
        "test2 no longer produces a clean edge-pairing — the precedence fix's "
        "premise (clean pairing pre-empts the closed-boundary heuristic) no "
        "longer applies; revisit fabconvert/formats/dxf_io.py.")
    # And the closed-boundary heuristic *also* fires (the whole reason the old
    # order was wrong): the trace walls + end-caps close into loops.
    assert is_boundary is True, (
        "test2's trace walls no longer close into loops — the fixture may have "
        "changed; the test2 vs test6 distinction this regression guards is "
        "that BOTH signals fire on test2.")
    # Net: the intermediate model chose centrelines (Paths) over filled
    # polygons, despite the loop.  This is the precedence decision itself.
    assert len(geom.paths) > 0, "no centreline paths recovered for test2 — regression"
    assert len(geom.polygons) == 0, (
        "test2 was classified as a pour/filled-outline — the closed-boundary "
        "heuristic pre-empted a clean pairing; the precedence fix is broken.")
    assert len(geom.circles) > 0, "test2 should still carry its pad flashes (CIRCLEs)"


def test_test2_gerber_has_draws_not_just_flashes(tmp_path):
    """The headline regression assertion: the .gbr contains routed traces.

    Before the fix the .gbr held ONLY pad flashes (D03) — every routed trace
    was missing.  Assert there is a realistic number of D01 draw operations
    (recovered centrelines), not merely that the file is non-empty.
    """
    out = tmp_path / "test2-F_Cu.gbr"
    fabconvert.convert(str(TEST2), str(out))
    text = out.read_text(encoding="utf-8")
    ops = _count_ops(text)
    assert ops["D01"] > 0, (
        f"no D01 draws in test2 .gbr — routed traces are missing again "
        f"(ops={ops}); the precedence regression is BACK.")
    # test2 pairs into 19 wall-pairs; allow a generous floor so the test does
    # not break on incidental pair-count drift, while still proving a
    # *realistic* count rather than a single token draw.
    assert ops["D01"] >= 10, (
        f"unrealistic D01 draw count {ops['D01']} for test2 (expected ~19); "
        f"the centreline recovery is suspect.")
    assert ops["D03"] > 0, "test2 should still have pad flashes (D03)."


def test_test2_svg_renders_traces(tmp_path):
    """Converting to SVG still works (it always did — SVG renders polygons).

    After the fix test2 renders as stroked centreline <path>s instead of
    filled polygon <path>s; both are valid, so assert the traces are present
    as stroked paths carrying the recovered trace width, not the old filled
    polygons that masked the Gerber regression.
    """
    out = tmp_path / "test2-F_Cu.svg"
    fabconvert.convert(str(TEST2), str(out))
    svg = out.read_text(encoding="utf-8")
    # SvgWriter uses a namespace prefix (``<ns0:svg ...>``) so the literal
    # ``<svg`` substring is not present; use the closing tag + viewBox instead.
    assert "</ns0:svg>" in svg or "</svg>" in svg, "SVG not well-formed"
    assert "viewBox=" in svg, "SVG missing viewBox"
    # The recovered trace width is 0.200 mm; the SVG strokes carry stroke-width.
    assert 'stroke-width="0.2' in svg, (
        "test2 SVG does not render a stroked 0.2 mm trace centreline — trace "
        "recovery did not reach the SVG writer.")
    # A routed-trace file yields stroked centreline <path>s; a mis-classified
    # pour would emit filled <path>s with no recovered-trace stroke-width.
    # Strokes set fill="none"; assert a realistic number of stroked traces.
    assert svg.count("stroke=") >= 10, "test2 SVG should show many stroked traces"
    assert svg.count('fill="none"') >= 10, "test2 strokes should be fill=\"none\""


# ---------------------------------------------------------------------------
# test6-F_Cu.dxf: OPEN trace walls — the invariant the fix must NOT regress.
# ---------------------------------------------------------------------------
# A real ``test6-F_Cu.dxf`` fixture is not committed to this checkout, so we
# exercise the test6 *invariant* — open walls (the closed-boundary heuristic
# does NOT fire) still select centrelines via a clean pairing — on a minimal
# open-walls DXF generated in tmp_path.  This is the structural property the
# prompt's "do not regress test6" requirement is about: a file whose trace
# walls do not close into loops must still produce centrelines (and identical
# output to before), because the new precedence only changes behaviour when
# BOTH a clean pairing AND a closed boundary are present — on open walls only
# the pairing signal fires, so the order change is a no-op there.

def _write_open_walls_dxf(path: pathlib.Path) -> None:
    """A minimal test6-like DXF: 2 open parallel-wall traces, no end-caps.

    Walls are 5 mm long (>> PAIR_MIN_LEN_MM 0.5 mm); widths 0.2 / 0.8 mm (<< the
    wall length, so the width-ratio guard accepts the pairs cleanly).  No
    end-caps => the chain is OPEN => lines_form_closed_boundary is_boundary
    False, exactly the test6 case.  $INSUNITS=4 (mm) keeps unit detection out
    of scope: the precedence fix is unit-independent.
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    for x0, y0, x1, w in [(0.0, 0.0, 5.0, 0.2), (0.0, 5.0, 4.0, 0.8)]:
        msp.add_line((x0, y0), (x1, y0))           # top wall
        msp.add_line((x0, y0 - w), (x1, y0 - w))   # bottom wall (open ends)
    doc.saveas(str(path))


def test_open_walls_still_emit_centrelines(tmp_path):
    """test6 invariant: open trace walls -> centrelines, unchanged by the fix.

    This must not regress: under the new precedence a clean pairing on OPEN
    walls still selects centreline emission (the closed-boundary heuristic is
    False here, so it was never going to pre-empt anything), producing the
    SAME output the old (pre-fix) code produced for test6-F_Cu.dxf.
    """
    dxf = tmp_path / "open_walls.dxf"
    _write_open_walls_dxf(dxf)
    geom, _ = fabconvert.read(str(dxf))
    # Two open wall-pairs -> two centreline paths with the recovered widths.
    assert len(geom.paths) == 2, (
        f"open-walls DXF should recover exactly 2 centreline traces, got "
        f"{len(geom.paths)}; the test6 path regressed.")
    widths = sorted(round(p.stroke_width, 3) for p in geom.paths)
    assert widths == [0.2, 0.8], f"unexpected recovered widths {widths}"
    # And NO filled polygons — open walls are not a pour.
    assert len(geom.polygons) == 0, (
        "open-walls DXF was classified as a filled pour — regression of the "
        "test6 open-walls path.")


def test_open_walls_gerber_has_draws(tmp_path):
    """test6 invariant in .gbr: open-wall traces render as D01 draws."""
    from fabconvert.formats.gerber_io import UnsupportedGerberConstruct
    dxf = tmp_path / "open_walls.dxf"
    _write_open_walls_dxf(dxf)
    gbr = tmp_path / "open_walls.gbr"
    fabconvert.convert(str(dxf), str(gbr))
    ops = _count_ops(gbr.read_text(encoding="utf-8"))
    assert ops["D01"] == 2, f"expected 2 D01 draws for 2 open-wall traces, got {ops}"
    assert ops["D03"] == 0, "open-walls DXF has no pads — no D03 flashes expected"


def test_test2_pad_flashes_land_on_source_pads(tmp_path):
    r"""Regression for Bug C: every D03 pad flash in the .gbr must sit on the
    *actual* pad it came from, in the same coordinate frame as the traces.

    PRE-Bugfix-C the 4 ``D03`` flashes in ``test2-F_Cu.gbr`` had correct X but a
    constant +999.808mm Y offset from their real pad positions.  Root cause:
    Bug A (``recover_centrelines`` returned mm, not drawing units) inflated every
    recovered trace's intermediate coordinates by ~unit_scale (25.4x); the
    intermediate Y range was therefore ~1000mm tall, so the GerberWriter's
    ``flip_axis_inter`` (the geom.bounds() Y midline, shared by traces AND
    pads) landed ~+1000mm from the pads' true ~16mm-tall intermediate range.
    The traces re-flipped cleanly (their own inflated bbox defined that axis),
    but the pads — which come through the *correct* drawing-unit->mm path —
    flipped about a midline ~1000mm off their real frame, displacing every pad
    by the same constant.  Bug C is a *downstream symptom of Bug A* through the
    shared flip axis, not an independent circle-path bug.

    This test pins the fix by round-tripping test2 (a fixture with both traces
    and pads) to .gbr and asserting each flashed D03 position equals its
    source ``CIRCLE``'s ``(cx, cy)`` converted to the Gerber output frame
    (mm, Y-up about the bbox midline — the same frame the traces plot in).
    """
    import re
    import ezdxf
    doc = ezdxf.readfile(str(TEST2))
    msp = doc.modelspace()
    src_circles = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius)
                   for e in msp if e.dxftype() == "CIRCLE"]
    assert src_circles, "test2 should carry pad CIRCLEs to flash"
    geom, al = fabconvert.read(str(TEST2))
    sc = al.unit_scale
    # Source pad centres in the Gerber (Y-up, mm) frame: the reader maps each
    # source (cx,cy) to the intermediate, and the writer re-flips Y about the
    # geometry midline.  Reconstruct the *exact* expected emitted coordinate:
    #   intermediate = af.to_intermediate_(cx|cy)
    #   gbr_y = flip_axis_inter - intermediate_y
    # which, for unit_scale*origin=(sc,0) and y_flip=True, simplifies to
    #   gbr_x = cx * sc
    #   gbr_y = (bbox_ymax + bbox_ymin) - (flip_axis_src - cy)*sc
    # but it is far less brittle to drive the *real* GerberWriter + parse.
    out = tmp_path / "test2-F_Cu.gbr"
    fabconvert.convert(str(TEST2), str(out))
    text = out.read_text(encoding="utf-8")
    # Parse every flashed coordinate (X#####Y#####D03*).  Gerber precision here
    # is 6 decimals (%FSLAX36Y36), so the emitted integer == mm * 1e6.
    flashes = []
    for ln in text.splitlines():
        m = re.match(r"^X(-?\d+)Y(-?\d+)D03\*$", ln.strip())
        if m:
            flashes.append((int(m.group(1)) / 1e6, int(m.group(2)) / 1e6))
    assert len(flashes) == len(src_circles), (
        f"expected {len(src_circles)} pad flashes, parsed {len(flashes)} — "
        f"the D03 count diverged from the source CIRCLE count.")
    # The expected emitted flash: re-run the reader's alignment transform and
    # the writer's re-flip on each source circle, in the same intermediate the
    # GerberWriter will see, so the assertion is independent of the writer's
    # internal helpers.  flip_axis_inter is the bbox midline of geom itself.
    bb = geom.bounds()
    flip_axis_inter = (bb[1] + bb[3]) if bb is not None else 0.0
    expected = []
    af = al
    for (cx, cy, r) in src_circles:
        ix = af.to_intermediate_x(cx)
        iy = af.to_intermediate_y(cy)
        expected.append((ix, flip_axis_inter - iy))
    # Match each parsed flash to an expected pad (sorted X then Y for order-
    # independence).  Tolerance: one Gerber coordinate step (1e-6 mm) plus a
    # small float fudge (1e-3 mm = 1um) for the double-rounding (emit /1e6
    # here vs the writer's own round).
    tol = 1e-3
    for (fx, fy), (ex, ey) in zip(sorted(flashes), sorted(expected)):
        assert abs(fx - ex) <= tol and abs(fy - ey) <= tol, (
            f"pad flash ({fx:.4f},{fy:.4f})mm does not land on its source pad "
            f"({ex:.4f},{ey:.4f})mm — Bug C (the constant +999.808mm Y pad "
            f"offset, a downstream consequence of Bug A inflating the trace "
            f"bbox and pulling the shared flip axis off the pad frame) is BACK. "
            f"flashes={sorted(flashes)} expected={sorted(expected)}")
    # And concretely: the emitted flash Ys must be in the tens-of-mm range of
    # the ~27x16mm board, NOT ~1000mm away (the bug's signature).
    assert all(abs(fy) < 100.0 for (_fx, fy) in flashes), (
        f"pad flash Y(s) {[(fx,fy) for (fx,fy) in flashes]} are far outside the "
        f"~16mm board range — a pad landed ~1000mm off its real pad (Bug C).")


def test_gerber_writer_round_trips_polygons(tmp_path):
    """Gerber polygon gap made loud: a pour reaching Gerber must NOT vanish.

    A ClosedPolygon in the intermediate has no centreline+width; before the
    GerberWriter fix such polygons were silently dropped with only a printed
    NOTE — a real pour disappeared from the .gbr with no error.  The fix makes
    that either round-trip as G36/G37 region fills OR fail loudly.  This test
    asserts the *contract*: emitting a non-empty polygon set into Gerber must
    either produce region-fill output OR raise — never silently succeed with
    missing copper.
    """
    from fabconvert.core.geometry import ClosedPolygon, GeometrySet
    from fabconvert.formats.gerber_io import GerberWriter
    geom = GeometrySet(polygons=[ClosedPolygon(
        points=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])])
    gbr = GerberWriter().to_string(geom)
    # A real pour reaching Gerber must NOT be silently dropped.  Acceptance: a
    # G36/G37 region fill is present (pour round-tripped) OR the writer raised
    # UnsupportedGerberConstruct.  Silent success with only M02 = the old bug.
    if "G36" in gbr and "G37" in gbr:
        return  # preferred path: pour emitted as a region fill
    pytest.fail(
        "Gerber writer silently dropped a non-empty polygon set without "
        "raising and without emitting a G36/G37 region fill — the polygon-loss "
        "regression returned (no fail-fast, copper missing).")
