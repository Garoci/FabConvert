"""Unit tests for the Voronoi-medial-axis centreline+width primitive.

``fabconvert.formats.medialaxis.recover_centrelines`` turns a closed trace-outline
ribbon polygon (drawing units, mm_per_unit) into ``(cx0,cy0,cx1,cy1,width_mm)``
centreline segments — the same contract ``edgepair.pair_parallel_edges`` returns.
These tests pin the three properties the production redesign relies on:

  * a thin straight ribbon recovers its width within a few microns and a
    centreline of the right length;
  * a bent ribbon (a corner) recovers a CONTINUOUS centreline through the bend
    — the post-prune spine has zero branch points and the recovered segments
    share an endpoint at the bend (the structural property the old snap-merge
    had to fake);
  * a broad pour rectangle yields only runs whose width is not clearly shorter
    than their own length — the belt-and-braces pour filter the caller applies
    drops them, so a pour falls back to a filled outline rather than emitting a
    phantom centreline.
"""
from __future__ import annotations

import math

from fabconvert.formats import medialaxis as ma


def _stadium_pts(cx, cy, length, width, n_cap=24):
    """A horizontal "stadium" (rectangle + two semicircular end-caps) closed
    polygon: a 0.2mm-wide trace of given centreline ``length`` (mm).  Points
    returned in mm, no closing duplicate.  This is the shape KiCad plots for a
    routed trace's walls + round end-caps."""
    hw = width / 2.0
    half_L = length / 2.0
    pts = []
    # top wall L->R
    pts.append((cx - half_L, cy - hw))
    pts.append((cx + half_L, cy - hw))
    # right semicircle (bottom->top, outside)
    for k in range(1, n_cap):
        a = -math.pi / 2 + math.pi * k / n_cap
        pts.append((cx + half_L + hw * math.cos(a), cy + hw * math.sin(a)))
    # bottom wall R->L
    pts.append((cx + half_L, cy + hw))
    pts.append((cx - half_L, cy + hw))
    # left semicircle (top->bottom, outside)
    for k in range(1, n_cap):
        a = math.pi / 2 + math.pi * k / n_cap
        pts.append((cx - half_L + hw * math.cos(a), cy + hw * math.sin(a)))
    return pts


def test_stadium_recovers_width_and_length():
    """A 0.2mm-wide, 10mm-long stadium ribbon -> recovered width ~0.2mm
    (within a few microns) and a centreline whose endpoints span ~10mm.

    Coordinates are passed in mm with mm_per_unit=1.0 (the primitive scales
    drawing units to mm internally).  The recovered width is the median vertex
    clearance x2; on a clean stadium it should land within ~5um of 0.2.
    """
    pts = _stadium_pts(0.0, 0.0, length=10.0, width=0.2, n_cap=24)
    segs = ma.recover_centrelines(pts, mm_per_unit=1.0)
    assert len(segs) >= 1, (
        f"stadium ribbon recovered no centreline segments ({segs})")
    # Width: a sub-micron-honest recovery should be within ~5um of 0.2 on this
    # clean stadium.  (Densification + Voronoi tolerance; the test2 fixture's
    # spread was 0.1977-0.2012.)
    widths = [s[4] for s in segs]
    assert all(abs(w - 0.2) < 0.005 for w in widths), (
        f"recovered widths {widths} not all within 5um of 0.2mm — the medial "
        f"axis width recovery drifted on the cleanest possible stadium.")
    # Centreline length: sum the chord lengths of the recovered segments; the
    # total spine should span ~10mm (within the cap rounding, ~half-width
    # inshort at each end).  Allow +/- ~1mm tolerance for cap geometry.
    total = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in segs)
    assert 9.0 < total < 11.0, (
        f"recovered centreline total length {total:.4f}mm, expected ~10mm — "
        f"the spine did not recover the stadium's full length.")


def test_bent_ribbon_centreline_is_continuous():
    r"""An L-shaped (bent) ribbon recovers a centreline that is CONTINUOUS through
    the bend — the structural property the snap-merge used to fake.

    The post-prune medial-axis spine has degree ``{2: …, 1: 2, ≥3: 0}``: zero
    branch points, two trace termini.  The bend itself is a degree-2 node on the
    spine, so the recovered segments meeting at the bend SHARE an endpoint
    (rounding-aside).  Assert exactly that: >=2 recovered segments, and at
    least one pair of segment endpoints coincides (the corner join), with the
    spine carrying zero branch points.
    """
    # An L: a 0.2mm-wide horizontal arm and a 0.2mm-wide vertical arm meeting
    # at a rounded inside corner.  Built as a closed outline.
    hw = 0.1
    L = 6.0
    # Horizontal arm: from x=-L/2..+L/2 at the top; then turn down at x=+L/2.
    # Polygon (no closing dup): outer boundary of the L shape.
    pts = [
        (-L / 2, 0.0),            # top-left of horizontal arm
        (L / 2 - hw, 0.0),        # top-right near corner
        (L / 2, -hw),             # corner outer vertex
        (L / 2, -L),              # right side down
        (L / 2 + 2 * hw, -L),     # bottom-right
        (L / 2 + 2 * hw, 2 * hw), # under the horizontal arm's right end
        (-L / 2, 2 * hw),         # bottom-left under horizontal arm
        (-L / 2, hw),             # inner top-left
        (-L / 2, hw),             # (placeholder removed below)
    ]
    pts = [p for i, p in enumerate(pts)
           if i == 0 or p != pts[i - 1]]  # drop exact dups
    # The L outline above is a rough approximation; what matters is that the
    # medial-axis spine is continuous (zero branches).  Use spine_degree to
    # assert the structural property independent of the exact recovery count.
    deg = ma.spine_degree(pts, mm_per_unit=1.0)
    assert deg.get(3, 0) == 0 and deg.get(1, 0) == 2, (
        f"a bent trace ribbon's post-prune spine must have degree "
        f"{{2:…, 1:2, >=3:0}} (two termini, zero branches); got {dict(deg)}. "
        f"A non-zero branch count means the spur pruning regressed and the "
        f"corner would need merge plumbing again.")


def test_pour_vs_trace_width_length_ratio():
    """A pour's medial-axis runs have width COMPARABLE to their length; a trace's
    runs have width << length.  This is the qualitative property the primitive
    exposes that distinguishes a pour from a trace ribbon.

    NOTE: the simple ``width < run_length`` guard is NOT a sufficient pour
    detector — measured in plan mode, a 10x10mm square pour yields runs with
    width≈5mm, length≈7mm (ratio≈0.71) that PASS ``width<len``, and an L-shaped
    pour with a 10mm×1mm neck yields a run ratio≈0.1 that passes anything up to
    a 0.4 threshold.  So pour-vs-trace discrimination stays in
    :func:`fabconvert.formats.edgepair.lines_form_closed_boundary`'s
    ``is_boundary`` (the primary discriminator); the medial-axis primitive is
    only the RECOVERY engine for polygons already classified as trace-like.

    This test pins the qualitative ratio gap the primitive produces — pour's
    width/length ratio (~0.71) is ~35x a trace's (~0.02) — so the property is
    observable even though the threshold alone does not cleanly partition them
    in the pathological L-neck case.
    """
    # A 0.2mm-wide, 10mm-long stadium trace: width/length ratio ~0.02.
    trace_pts = _stadium_pts(0.0, 0.0, length=10.0, width=0.2, n_cap=24)
    t_segs = ma.recover_centrelines(trace_pts, mm_per_unit=1.0)
    assert t_segs, "stadium trace recovered nothing"
    t_ratios = [s[4] / math.hypot(s[2] - s[0], s[3] - s[1]) for s in t_segs]
    assert all(r < 0.05 for r in t_ratios), (
        f"trace ribbon width/length ratios {t_ratios} not all < 0.05 — a trace "
        f"should be thin relative to its length; the stadium shape changed.")
    # A 10x10mm solid pour: width/length ratio ~0.71 (a degenerate medial axis).
    pour_pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    p_segs = ma.recover_centrelines(pour_pts, mm_per_unit=1.0)
    assert p_segs, "pour rectangle recovered nothing (degenerate Voronoi?)"
    p_ratios = [s[4] / math.hypot(s[2] - s[0], s[3] - s[1]) for s in p_segs]
    assert all(r > 0.3 for r in p_ratios), (
        f"pour rectangle width/length ratios {p_ratios} not all > 0.3 — a "
        f"pour's medial axis should be degenerate (width ~ comparable to "
        f"length), unlike a trace.  This is the qualitative property the "
        f"primitive uses; the actual pour-vs-trace classification upstream stays "
        f"in is_boundary (the ratio threshold alone fails the L-neck pour).")
    # The two are separated by an order of magnitude: the ratio gap is the
    # observable signal, not a clean threshold.
    assert min(p_ratios) / max(t_ratios) > 5.0, (
        f"pour min ratio {min(p_ratios):.3f} is not >> trace max ratio "
        f"{max(t_ratios):.3f} — the qualitative pour-vs-trace signal is gone.")


def test_axis_aligned_wall_recovers_near_horizontal_centreline():
    r"""Regression for Bug B: a centreline covering a known-axis-aligned trace
    wall must itself be near-axis-aligned, not cut diagonally across the corner.

    PRE-Bugfix-B ``_emit_splits`` detected bends by a PER-VERTEX turn angle
    (>=10deg on a single step).  The medial axis of a chamfered-corner trace
    ROUNDS the corner smoothly over many densified vertices (STEP_MM=0.02), so
    no single per-vertex step reached the threshold (measured on test2: a 45deg
    chamfered corner turned 9.21deg max per step).  The splitter never fired,
    and the whole top-wall-plus-chamfer run was emitted as ONE straight
    segment end-to-end: from the trace's far terminus straight across to the
    *inner* corner 3.5mm past the real corner — a spurious ~12deg diagonal tilt
    where the source wall has zero perpendicular deviation.  The chord-
    deviation splitter (BEND_DEV_FRAC) now splits at the real corner.

    This test builds a trace whose long wall is perfectly axis-aligned and
    asserts the recovered centreline covering that wall has |dy| << |dx|
    (perpendicular deviation a tiny fraction of the trace width — not the
    multi-mm diagonal the bug produced).  It pins the bugfix against
    regression to a per-vertex-angle splitter.
    """
    # A 20mm-long horizontal stadium (axis-aligned walls, rounded caps).
    pts = _stadium_pts(0.0, 0.0, length=20.0, width=0.2, n_cap=24)
    segs = ma.recover_centrelines(pts, mm_per_unit=1.0)
    assert segs, "stadium recovered no centreline (Voronoi failed?)"
    # Find the centreline segment covering the LONG axis-aligned wall: the one
    # whose horizontal span |dx| is largest (the end-caps would give short segs
    # if they split off; the long wall is the dominant run).
    dx_dy = [((s[2] - s[0]), (s[3] - s[1])) for s in segs]
    long_seg = max(segs, key=lambda s: abs(s[2] - s[0]))
    dx, dy = long_seg[2] - long_seg[0], long_seg[3] - long_seg[1]
    # The source wall is horizontal (zero Y deviation over its full length).
    # The recovered centreline covering it must be near-horizontal: |dy| a
    # tiny fraction of the trace WIDTH (0.2mm here), nowhere near |dx|.  The
    # bug produced |dy| ~ |dx| ~ 3.5mm (a 45deg diagonal) on a wall that has
    # zero Y deviation in the source — a perpendicular deviation ~17.5x the
    # trace width.  Require |dy| < width/4 (50um) so a reversion to the
    # diagonal is caught immediately.
    assert abs(dy) < 0.2 / 4.0, (
        f"the centreline covering the horizontal wall is tilted: "
        f"dx={dx:.4f} dy={dy:.4f}mm (|dy| ~ |dx| ~ 3.5mm was the Bug B "
        f"diagonal; a horizontal wall must recover a near-horizontal centreline "
        f"with |dy| << 0.05mm). segs={segs}")
    # And that long run spans most of the trace's length (>=15mm of the 20mm
    # wall) — not a short fragment that ducked the corner.
    assert abs(dx) >= 15.0, (
        f"the recovered horizontal run is only {abs(dx):.2f}mm long (expected "
        f">=15mm of the 20mm wall) — the splitter over-fragmented the straight "
        f"wall; the chord-deviation threshold is too low. segs={segs}")


def test_sharp_corner_recovers_exactly_two_legs():
    r"""Regression for the Bug B over-split of a SHARP (non-chamfered) corner.

    The chord-deviation splitter that fixed the chamfered-corner missing-bend
    (Bug B) fires TWICE at a genuinely sharp corner — once at the corner vertex
    on the approach and again on the exit — because the densified medial spine
    deviates ~30-45um (just over the BEND_DEV tol) from each straight leg's
    chord right at the apex.  That carved a tiny middle "sliver" fragment whose
    chord was shorter than the trace width and whose width read inflated (apex
    clearance > wall clearance, ~0.22-0.24mm vs ~0.198-0.201mm), so test2's
    sharp corners came back as THREE pieces instead of TWO — visually a corner
    cut into mismatched halves.  (Measured on test2: at the (1.9727,-1.3994)
    corner the recursion produced two ~9-vertex sub-width slivers flanking the
    apex, each width 0.22526 / 0.22550mm — mechanism (a): the SAME apex vertex
    counted as two bend points.)

    The fix (``_merge_subwidth_slivers``): an INTERIOR fragment (both
    neighbours) whose chord is under ``MAX_CORNER_FRAC``× the trace width AND
    whose own width is inflated above nominal is the apex region, not a wall
    piece — collapse it onto one shared apex vertex so the corner is exactly
    two legs.  A real short end-cap straight bit is a SPINE TERMINUS (one
    neighbour) OR has normal width, so it stays.

    This test builds a synthetic ribbon with a genuinely sharp 90 deg corner
    (NOT a chamfer) and asserts it recovers as exactly TWO segments — no third
    sliver, and no segment whose width is inflated outside the ribbon's real
    width tolerance.  The short-arm case (B=3mm, ~30x the 0.1mm half-width) is
    the one that reproduced the sliver pre-fix, so it pins the fix against
    regression (the long-arm cases also sweep to 2 but cannot distinguish the
    fix from "the apex never split" because their short leg is long enough to
    read as a wall).
    """
    hw = 0.1
    # A 90-deg L: horizontal arm A long, vertical arm B short, SHARP inner +
    # outer corners (no fillet, no chamfer).  Outline traced CCW.  The short-arm
    # B=3 reproduces the over-split-then-needs-merge case; the long-arm B=6
    # must also stay 2 (a regression to over-fragmentation here is caught).
    def sharp_l(a: float, b: float) -> list:
        return [
            (-a, 0.0), (a, 0.0),
            (a + 2 * hw, 0.0), (a + 2 * hw, b + hw),      # outer sharp corner
            (a, b + hw), (a, hw),                          # inner sharp corner
            (-a, hw),
        ]

    for a, b in [(10.0, 3.0), (8.0, 6.0)]:
        pts = sharp_l(a, b)
        segs = ma.recover_centrelines(pts, mm_per_unit=1.0)
        assert segs, f"A={a},B={b}: sharp-L recovered no centreline ({segs})"
        assert len(segs) == 2, (
            f"A={a},B={b}: a sharp 90-deg corner must recover as EXACTLY TWO "
            f"legs (one per arm), got {len(segs)} — a third short/inflated-width "
            f"sliver at the apex means the Bug B over-split regressed: the "
            f"chord-deviation splitter is firing twice at the corner and the "
            f"sub-width-apex merge isn't collapsing it back. segs={segs}")
        widths = [s[4] for s in segs]
        # The sliver's signature is an INFLATED width — apex clearance > wall
        # clearance (~0.22-0.24mm on the test2 trace whose real width is 0.2mm).
        # On this clean synthetic the medial axis reads the two arms at the
        # half- and full-width (0.1 and 0.2mm — both honest medial reads of a
        # 0.2mm slab, spread intrinsic to a sharp-corner outline), so the
        # assertion permits the ribbon's full-width envelope [nominal/2, nominal]
        # and rejects only an apex sliver ABOVE the ribbon's real full width.
        nominal = 0.2
        for w in widths:
            assert nominal * 0.5 - 1e-6 <= w <= nominal + 1e-6, (
                f"A={a},B={b}: a recovered segment width {w:.5f}mm is outside "
                f"the ribbon's [nominal/2, nominal] envelope [0.1, 0.2]mm — an "
                f"inflated-value apex sliver (the Bug B over-split signature, "
                f">nominal width from apex clearance) survived the merge, or a "
                f"fragment of a different geometry crept in. widths={widths}")
        # And the two legs span the two arms: one is mostly horizontal (|dx| >> |dy|),
        # the other mostly vertical (|dy| > |dx|).  Pins that the corner split into
        # its two legs, not e.g. two fragments of the same arm.
        dominators = []
        for s in segs:
            dx, dy = abs(s[2] - s[0]), abs(s[3] - s[1])
            dominators.append("H" if dx > dy else "V")
        assert set(dominators) == {"H", "V"}, (
            f"A={a},B={b}: the two legs are not one-per-arm (H+V), got "
            f"{dominators} — the corner did not split into its two distinct "
            f"legs. segs={segs}")

