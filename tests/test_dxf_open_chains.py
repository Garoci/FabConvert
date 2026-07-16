"""Regression test: open leftover chains (trace-corner caps) must surface.

Companion to the ``trace_open_chains`` addition in
``fabconvert/formats/edgepair.py`` and the ``clean_pairing`` emission change in
``fabconvert/formats/dxf_io.py``.  Bug class: KiCad's round-end-cap stylization
at a trace corner is short cap chords that bridge one wall-pair's end to a
*different* wall-pair's start.  The cap chords are correctly NOT pairing
candidates (too short) so they sit in :func:`pair_parallel_edges`'s
``leftover`` — but :func:`trace_line_polygons` only returns *closed* chains,
so an open cap chain that runs wall-to-wall was **silently dropped**, and the
corner rendered with a visible ~0.5 mm gap.

On ``tests/fixtures/test2-F_Cu.dxf`` the corner at drawing-unit point
~(2.135, -1.556) — a diagonal trace turning into a short vertical stub down to
a via — had its diagonal-pair end (~ (2.1254, -1.5545)) sit **0.5187 mm** from
the vertical-stub-pair start (~ (2.1457, -1.5571)), while every other corner in
the fixture connects to within ≤0.09 mm.  The bridging cap chords sat unused in
``leftover``; ``trace_open_chains`` now surfaces them and ``dxf_io`` emits each
as a thin connecting centreline ``Path``, closing the gap.
"""
from __future__ import annotations

import math
import pathlib

import ezdxf

import fabconvert
from fabconvert.formats.edgepair import (
    pair_parallel_edges, trace_open_chains, trace_line_polygons,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TEST2 = FIXTURES / "test2-F_Cu.dxf"


def _fixture_line_dicts(path: pathlib.Path) -> tuple[list[dict], float]:
    """Read the fixture's LINE dicts + detected mm_per_unit (drawing units)."""
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    line_dicts = [
        {"x1": e.dxf.start.x, "y1": e.dxf.start.y,
         "x2": e.dxf.end.x, "y2": e.dxf.end.y}
        for e in msp if e.dxftype() == "LINE"
    ]
    _geom, al = fabconvert.read(str(path))
    return line_dicts, al.unit_scale


def _max_pre_fix_gap_mm(line_dicts: list[dict], sc: float) -> float:
    """The largest nearest-neighbour endpoint gap using pairs ONLY (the model
    BEFORE the open-chain fix) — i.e. ``trace_open_chains``'s contribution is
    deliberately excluded.  Returns mm in drawing-unit-derived space."""
    pairs, _unpaired, _leftover = pair_parallel_edges(line_dicts, sc)
    ends = []
    for (cx0, cy0, cx1, cy1, _w) in pairs:
        ends.append((cx0 * sc, cy0 * sc))
        ends.append((cx1 * sc, cy1 * sc))
    worst = 0.0
    for i, a in enumerate(ends):
        nn = min(math.hypot(a[0] - b[0], a[1] - b[1])
                 for j, b in enumerate(ends) if i != j)
        worst = max(worst, nn)
    return worst


# ---------------------------------------------------------------------------
# trace_open_chains: direct unit-level behaviour.
# ---------------------------------------------------------------------------

def test_open_chain_surfaces_wall_to_wall_cap():
    """A wall-to-wall cap chain (open, degree-1 at both ends) must be returned
    by ``trace_open_chains`` and NOT by ``trace_line_polygons``.

    Minimal net: two parallel "wall" stubs bridged by one short "cap" segment
    running wall-to-wall.  The cap is an OPEN chain (its two ends sit on
    different walls, not back on itself), so ``trace_line_polygons`` must
    return nothing for it while ``trace_open_chains`` returns exactly it.
    """
    # Two parallel "walls" joined at ONE end by a cap — a "U" shape open at the
    # other end.  The degree-1 vertices are the two OPEN (uncapped) wall ends;
    # the cap is the bottom of the U.  This is an OPEN chain (both ends sit on
    # different wall ends, not back on itself), so it must surface via the open
    # walker, not the closed one.
    lines = [
        {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0},   # top wall
        {"x1": 0.0, "y1": 1.0, "x2": 10.0, "y2": 1.0},  # bottom wall
        {"x1": 10.0, "y1": 0.0, "x2": 10.0, "y2": 1.0},  # RIGHT cap (closes U)
    ]
    opens = trace_open_chains(lines)
    polys = trace_line_polygons(lines)
    # The U is open (degree-1 at (0,0) and (0,1)): the closed surfacer must
    # return nothing for it; the open surfacer returns exactly one chain whose
    # two ends are the uncapped wall ends.
    assert polys == [], (
        f"the open U must not register as a closed polygon ({polys})")
    assert len(opens) == 1, (
        f"expected one open U chain, got {len(opens)}: {opens}")
    u = opens[0]
    ends = {u[0], u[-1]}
    assert ends == {(0.0, 0.0), (0.0, 1.0)}, (
        f"open U must run between the two uncapped wall ends (0,0)/(0,1), "
        f"got chain {u} with ends {ends}")
    # And the cap vertex (10,0)/(10,1) is interior to the chain (not an end).
    assert (10.0, 0.0) in u and (10.0, 1.0) in u, (
        f"the RIGHT cap must be part of the U chain's interior, got {u}")


def test_closed_loop_not_returned_as_open():
    """A fully closed loop has no degree-1 vertex, so it is NOT an open chain
    (it belongs to ``trace_line_polygons``); ``trace_open_chains`` must yield
    nothing for it so the two surfacers are disjoint on the same input."""
    square = [
        {"x1": 0.0, "y1": 0.0, "x2": 4.0, "y2": 0.0},
        {"x1": 4.0, "y1": 0.0, "x2": 4.0, "y2": 4.0},
        {"x1": 4.0, "y1": 4.0, "x2": 0.0, "y2": 4.0},
        {"x1": 0.0, "y1": 4.0, "x2": 0.0, "y2": 0.0},
    ]
    assert trace_open_chains(square) == [], (
        "a closed loop must not surface as an open chain — open and closed "
        "surfacing must be disjoint")
    assert len(trace_line_polygons(square)) == 1


# ---------------------------------------------------------------------------
# End-to-end on tests/fixtures/test2-F_Cu.dxf: the corner-gap regression.
# ---------------------------------------------------------------------------

def test_test2_fixture_exists():
    """Guard: the fixture the corner-gap repro targets is present."""
    assert TEST2.is_file(), f"missing fixture: {TEST2}"


def test_test2_corner_gap_closed():
    r"""The headline assertion: after the redesign the recovered test2 centreline
    network has NO interior disconnect (the corner-gap regression is gone,
    structurally — no snap-merge plumbing).

    PRE-redesign (the edge-pair path): the wall-pair centrelines ended in open
    air at each corner.  The worst interior gap was ~0.5187mm (the diagonal vs
    vertical-stub corner at drawing-unit ~(2.135, -1.556)); ``_snap_corner_endpoints``
    snap-merged the pair ends to one shared vertex to paint over it.  Every
    corner was a potential blob-or-gap whack-a-mole site.

    POST-redesign (the medial-axis path): each closed loop's medial axis is one
    continuous spine (post-prune degree ``{2: …, 1: 2, ≥3: 0}`` — two real trace
    termini, zero branches).  Runs split at >=10deg bends SHARE their endpoint
    (a measured 0.0mm gap — the bend is one continuous polyline), so corners
    connect by construction with no merge step.  The only large endpoint gaps in
    the recovered network are the FOUR real trace termini (2 per polygon — the
    degree-1 nodes) whose nearest neighbour is the far end of their own trace,
    which is NOT a disconnect.  So the structural assertion is:
    ``find the 4 terminus endpoints (the 4 largest nearest-neighbour gaps) and
    assert EVERY remaining endpoint's nearest-neighbour gap is < 0.5mm`` — i.e.
    nowhere in the interior of the network is there the old ~0.52mm gap.
    """
    geom, _ = fabconvert.read(str(TEST2))
    ends: list[tuple[float, float]] = []
    for p in geom.paths:
        ends.append(p.segments[0]); ends.append(p.segments[-1])

    n_ends = len(ends)
    nn = []
    for i, a in enumerate(ends):
        best = min(math.hypot(a[0] - b[0], a[1] - b[1])
                   for j, b in enumerate(ends) if i != j)
        nn.append(best)
    nn_sorted = sorted(nn, reverse=True)

    # The 4 real trace termini (2 per polygon's medial-axis spine) are the
    # endpoints whose nearest neighbour is the far end of their own long trace —
    # huge gaps that are NOT disconnects.  Exclude them and assert the rest is a
    # connected network with no interior gap >=0.5mm.
    n_termini = 4  # {1: 2} per polygon × 2 polygons
    assert n_ends >= n_termini + 2, (
        f"too few recovered centreline endpoints ({n_ends}) to have 4 termini "
        f"plus interior connects — the recovery regressed.")
    interior_gaps = nn_sorted[n_termini:]
    worst_interior = max(interior_gaps) if interior_gaps else 0.0
    # The termini excluded, every interior endpoint must connect to a neighbour
    # within 0.5mm (the old snap-merge's 0.52mm gap floor).  Most are 0.0mm
    # (shared-bend vertices); a few round-cap-join pairs sit ~half-width
    # (~0.1-0.25mm) apart by construction of a rounded corner.
    assert worst_interior < 0.5, (
        f"after excluding the {n_termini} real trace termini, the worst "
        f"interior centreline endpoint gap is {worst_interior:.4f}mm (>=0.5) "
        f"— a real visible gap remains in test2's recovered centreline network; "
        f"the medial-axis redesign did not close the corner the snap-merge "
        f"used to. interior_gaps(top)={sorted(interior_gaps,reverse=True)[:6]}")
    # And concretely: the network's interior is dominated by exactly-shared
    # bend vertices (gap 0.0mm) — the structural signature of one continuous
    # spine split at bends, NOT a population of separate wall-pairs snapping.
    n_shared = sum(1 for g in interior_gaps if g < 1e-6)
    assert n_shared >= 8, (
        f"only {n_shared} interior centreline endpoints share an exact vertex "
        f"(gap <1e-6mm); the medial-axis spine should be continuous through "
        f"most corners (each bend a shared vertex). interior_gaps="
        f"{sorted(interior_gaps)}")
    # No multi-vertex cap-chain Path is emitted (the blob representation is
    # gone): every recovered trace is a 2-vertex straight centreline (the
    # medial axis already split at >=10deg bends).
    chains = [p for p in geom.paths if len(p.segments) > 2]
    assert chains == [], (
        f"test2 emitted {len(chains)} multi-vertex centreline Path(s) — the "
        f"medial axis should split at bends and emit 2-vertex straight "
        f"segments; a multi-vertex path would render the blob regression. "
        f"chains={chains}")
    print(f"\n[corner-gap] test2 centreline network: {n_ends} endpoints, "
          f"{n_termini} real termini excluded, {n_shared} interior "
          f"shared-vertex bends, worst interior gap {worst_interior:.4f}mm "
          f"(pre-redesign the comparable gap was 0.5187mm).")



def test_test2_open_chains_are_not_closed_polygons():
    """Idempotency guard: every chain ``trace_open_chains`` returns on test2's
    leftover must be genuinely open (first != last), so the open and closed
    surfacers stay disjoint and we are not double-emitting the same data."""
    line_dicts, sc = _fixture_line_dicts(TEST2)
    _pairs, _unpaired, leftover = pair_parallel_edges(line_dicts, sc)
    for ch in trace_open_chains(leftover):
        assert ch[0] != ch[-1], (
            f"an 'open' chain returned by trace_open_chains is actually closed "
            f"({ch[0]} == {ch[-1]}) — the open surfacer overlapped the closed "
            f"surfacers' domain; would double-emit.")


# ---------------------------------------------------------------------------
# Blob regression guard (redesigned): zero multi-vertex centreline Paths.
# ---------------------------------------------------------------------------
# PRE-redesign the edge-pair path surfaced ~22 open cap chains and a
# connectivity gate emitted a small hand-ful as 0.2mm-stroked multi-vertex
# Paths — stroking ~0.02-0.04mm cap chords at the full width renders a blob/dot
# per corner.  The gate guarded against emitting all 22.
#
# POST-redesign the medial-axis path does NOT surface cap chains at all — a
# corner is a bend in one continuous spine, recovered as two straight 2-vertex
# segments.  So the blob representation is STRUCTURALLY impossible (no cap
# chain is ever rendered), not merely gated.  These tests pin that.


def test_test2_emits_only_connective_open_chains():
    r"""The blob-regression guard, redesigned: test2 must emit ZERO multi-vertex
    centreline Paths (the blob representation).

    PRE-redesign: the edge-pair path surfaced ~22 open cap chains from
    ``trace_open_chains(leftover)`` and a connectivity gate emitted a small
    hand-ful (the <=4 halves of the cap arc bridging the one real ~0.5mm corner
    gap) as stroked multi-vertex Paths.  The regression the gate guarded against
    was emitting all 22 (a stroked cap-chain dot per corner = the blob bug).

    POST-redesign: the medial-axis path does NOT surface or emit open cap
    chains at all — a corner is a bend in the continuous medial-axis spine,
    recovered as two straight 2-vertex centreline segments meeting at that
    bend.  So ``geom.paths`` contains ONLY the 19 straight centrelines and zero
    multi-vertex paths: the blob representation is structurally impossible (no
    cap chain is ever rendered), not merely gated.  This replaces the
    connective-count gate with a direct "no multi-vertex path exists" assertion.
    """
    geom, _ = fabconvert.read(str(TEST2))
    multi = [p for p in geom.paths if len(p.segments) > 2]
    assert multi == [], (
        f"test2 emitted {len(multi)} multi-vertex centreline Path(s) — the "
        f"medial-axis redesign should emit only 2-vertex straight segments "
        f"(corners are bends in one spine, not separately-stroked cap chains); "
        f"a multi-vertex path here is the blob regression returned. multi={multi}")
    # And the population of straight centrelines is the expected 20 (no per-
    # corner dot overcounting the blob bug produced as ~22 chain Paths; the +1
    # over the pre-bugfix 19 is the chamfered corner now honestly split — Bug B).
    straight = [p for p in geom.paths if len(p.segments) == 2]
    assert len(straight) == 20, (
        f"test2 recovered {len(straight)} straight 2-vertex centrelines; "
        f"expected 20. straight count diverged — revisit the medial axis.")



def test_test2_corner_merged_not_drawn():
    r"""End-to-end at the intermediate-model level: the representation fix that
    the medial-axis redesign replaces.

    PRE-redesign: the bridge over the real test2 corner gap was MERGED, never
    DRAWN, by ``_snap_corner_endpoints`` — the bridge was two edge-pair
    centrelines snap-merged to one shared vertex at pair indices 16 (diagonal)
    and 5 (vertical stub).  Pre-fix-v1 (the blob bug) drew ~22 multi-vertex
    cap-chain Paths (a blob per corner); pre-fix-v2 (the gate, still drawing)
    produced 2 (one blob at the only bridged corner); fix-v3 snap-merged to
    zero — but it was plumbing around a fragile wall-pairing.

    POST-redesign: ``fabconvert.read``'s ``GeometrySet.paths`` contains ONLY
    the recovered 2-vertex centreline segments straight from the medial axis
    — no multi-vertex cap-chain Path is emitted anywhere (the medial axis
    splits at corners via chord-deviation; a corner is two straight segments
    meeting at the bend, NOT a separate opening).  The corner is connected by
    *construction* (the medial axis is one continuous spine through the bend),
    not by a snap-merge step.  This test pins the structural properties: 20
    centrelines, zero multi-vertex paths, and the corner connects.  The corner
    connectivity itself is asserted in detail by
    :func:`test_test2_corner_gap_closed`; here we pin the *contract* counts.
    """
    geom, _ = fabconvert.read(str(TEST2))
    centrelines = [p for p in geom.paths if len(p.segments) == 2]
    chains = [p for p in geom.paths if len(p.segments) > 2]
    # The medial axis recovers exactly 20 straight centreline segments on test2.
    # The count was 19 under the pre-bugfix bend splitter (a per-vertex >=10deg
    # angle test that the medial axis of a *chamfered* corner rounded smoothly
    # under (9.21deg max per step), so the real corner was emitted as ONE
    # straight-across segment that cut diagonally past it — Bug B).  The
    # chord-deviation splitter (BEND_DEV_FRAC) now honestly splits that corner
    # into its two legs, recovering the +1 segment.  20 is the geometrically
    # honest count (corners split, not merged).  Update only after re-checking
    # the recovered segments are all real trace pieces.
    assert len(centrelines) == 20, (
        f"test2 medial-axis recovery should yield 20 straight centreline "
        f"segments (2-vertex Paths), got {len(centrelines)}; the recovery "
        f"count diverged from the pinned contract — investigate before "
        f"updating this number.")
    # NO multi-vertex cap-chain Paths anywhere — the corner is a bend in the
    # continuous medial-axis spine, not a separately-stroked cap chain (the blob
    # representation is structurally impossible now).
    assert chains == [], (
        f"test2 emitted {len(chains)} multi-vertex centreline Path(s) — the "
        f"medial axis should split at >=10deg bends and emit 2-vertex straight "
        f"segments; a multi-vertex path would render the blob regression. "
        f"chains: {chains}")
    # Recovered widths sub-micron at the median — 18/19 within 2um of 0.2mm.
    widths = sorted(round(p.stroke_width, 5) for p in geom.paths if len(p.segments) == 2)
    assert widths, "no stroked centrelines recovered"
    assert abs(widths[0] - 0.2) < 0.005 and abs(widths[-1] - 0.2) < 0.005, (
        f"recovered centreline widths {widths} not all within 0.005mm of 0.2 — "
        f"the medial-axis width recovery diverged from the validated probe "
        f"(0.1977-0.2012mm).")
    n_near_0_2 = sum(1 for w in widths if abs(w - 0.2) < 0.002)
    assert n_near_0_2 >= 18, (
        f"only {n_near_0_2}/19 recovered widths within 2um of 0.2mm (widths "
        f"{widths}); the medial-axis width recovery regressed from the pinned "
        f"18/19.")
    print(f"[corner-redesign] 19 centreline segments, 0 multi-vertex paths; "
          f"widths {widths[0]}-{widths[-1]}mm ({n_near_0_2}/19 within 2um "
          f"of 0.2).")


def test_test2_no_inflated_corner_slivers():
    r"""Regression for the chord-deviation OVER-SPLIT of sharp corners.

    The chord-deviation splitter that fixed Bug B (the chamfered-corner missing
    bend) fires TWICE at a genuinely sharp (non-chamfered) corner — once on the
    approach, once on the exit — because the densified medial spine deviates
    ~30-45um (just over the BEND_DEV tol) from each straight leg's chord right
    at the apex.  That carved a tiny middle "sliver" fragment per corner: a
    short chord (shorter than the trace width) with an INFLATED width (apex
    clearance > wall clearance, ~0.22-0.24mm vs the trace's real ~0.198-0.201mm).
    On test2 this bloated the centreline count from 20 to 27 (7 slivers, two per
    sharp corner plus one); the prompt's repro at the (1.9727,-1.3994) corner
    showed ``w=0.22526/0.22550`` sub-0.006-du-chord fragments straddling the
    apex.  Mechanism (a): the SAME apex vertex counted as two bend points.

    The fix (``medialaxis._merge_subwidth_slivers``): an INTERIOR fragment
    (both neighbours, not a spine terminus) whose chord is under
    ``MAX_CORNER_FRAC``× the trace nominal width AND whose own width is inflated
    above nominal is the apex region, not a wall piece — collapse it onto one
    shared apex vertex so the corner is exactly two legs.  A real short end-cap
    straight bit is a terminus OR normal-width, so it stays.

    This test pins that against test2 directly: zero recovered centreline
    segments carry the sliver signature (inflated width on a sub-width chord),
    so no corner is cut into mismatched pieces.  Combined with
    ``test_test2_corner_merged_not_drawn``'s pinned count==20 (the over-split
    bloated it to 27), this catches both the count and the per-segment width
    anomaly the over-firing produces.
    """
    geom, _ = fabconvert.read(str(TEST2))
    centrelines = [p for p in geom.paths if len(p.segments) == 2]
    assert centrelines, "no straight centrelines recovered — separate regression"
    # The sliver signature: a recovered trace wall is ~0.198-0.201mm wide on a
    # chord that (for these routed traces) is >= ~0.005du long.  An apex sliver
    # has width >=1.05x nominal on a *short* chord -- the corner apex, not a
    # wall piece.  Match on width inflation (robust) AND short chord (the
    # collapse case), both in drawing units (coords) / mm (width).
    nominal = 0.2
    slivers = []
    for p in centrelines:
        (x0, y0), (x1, y1) = p.segments[0], p.segments[-1]
        chord = math.hypot(x1 - x0, y1 - y0)        # drawing units
        w = p.stroke_width                            # mm
        # ~0.2mm trace at sc=25.4 -> real wall chord >= ~0.005du (~0.13mm); an
        # apex sliver chord is sub-width, i.e. well under ~0.05du here.
        if w > nominal * 1.05 and chord < 0.05:
            slivers.append(((x0, y0), (x1, y1), w, chord))
    assert not slivers, (
        f"recovered {len(slivers)} inflated-width short-chord sliver(s) at "
        f"sharp corners — {slivers}.  The chord-deviation splitter is firing "
        f"twice per sharp corner (Bug B over-split, mechanism (a): same apex "
        f"vertex counted as two bend points) and the sub-width-apex merge in "
        f"medialaxis._merge_subwidth_slivers is no longer collapsing them.  "
        f"A sharp corner must be TWO legs sharing one apex, not three pieces "
        f"with a middle sliver of inflated width.")




def test_open_walls_emits_no_blobs(tmp_path):
    """test6 invariant (the prompt's "do not regress test6"): open trace walls
    have NO leftover cap chords (the walls don't close, the caps aren't plotted
    as discrete bridge segments), so ``trace_open_chains`` yields ZERO and the
    gate emits nothing — identical output to the old code, no new blobs.  The
    gate must be a strict no-op whenever ``leftover`` carries no open chains.
    """
    import ezdxf
    from fabconvert.formats.edgepair import pair_parallel_edges, trace_open_chains
    dxf = tmp_path / "open_walls_dxf_io.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    for x0, y0, x1, w in [(0.0, 0.0, 5.0, 0.2), (0.0, 5.0, 4.0, 0.8)]:
        msp.add_line((x0, y0), (x1, y0))
        msp.add_line((x0, y0 - w), (x1, y0 - w))
    doc.saveas(str(dxf))
    geom, _ = fabconvert.read(str(dxf))
    # Two open wall-pairs -> two 2-vertex centreline Paths, NO multi-vertex
    # open-chain Paths (leftover carries no open chains to gate).
    assert len(geom.paths) == 2, (
        f"open-walls DXF should recover exactly 2 centreline traces, got "
        f"{len(geom.paths)}; the open-walls path regressed.")
    assert all(len(p.segments) == 2 for p in geom.paths), (
        "open-walls DXF must not emit any multi-vertex open-chain Path — "
        "leftover has no open chains, so the connectivity gate is a no-op; "
        "a blob here means the gate invented connectivity on clean traces.")
    assert len(geom.polygons) == 0, (
        "open-walls DXF classified as a filled pour — regression of the test6 "
        "open-walls path.")
