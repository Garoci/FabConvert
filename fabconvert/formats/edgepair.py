"""Recover trace centreline + width from KiCad DXF parallel edge-pairs.

The DXF format carries **no width property** (golden rule #2).  KiCad's DXF
plotter works around this by emitting each trace as two roughly-parallel,
roughly-equal-length LINE edges (the copper trace walls) plus short end-cap
segments around the boundary (the round-end stylisation, whose length clusters
at the aperture radius).  The perpendicular offset between a matched edge-pair
*is* the trace width, and the midpoint polyline *is* the print centreline.

This module ports the validated ``DxfConverter._pair_parallel_edges`` /
``_trace_line_polygons`` out of ``Python/svg/format_converter.py`` verbatim in
spirit and in thresholds, and golfed into reusable functions:

  * :func:`pair_parallel_edges` — greedy matching of long LINE edges into
    ``(cx0, cy0, cx1, cy1, width_mm)`` centreline+width pairs.
  * :func:`trace_line_polygons` — union-find-over-endpoints chaining of
    connected LINEs into closed polygons, with the step-budget guard and the
    pathological-drop + report that the original carried (golden rule #3&4).

Validated against ``tests/fixtures/test6-F_Cu.dxf`` ↔ ``.gbr``:
the 20 long edges pair into 10 pairs with 0.0000mm perpendicular spread and
recovered widths exactly 0.2mm (×8) / 0.8mm (×2), pair midpoints within ≤0.14mm
of the Gerber centrelines.  ``tests/dxf_edgepair_probe.py`` is the original
diagnostic that established this; ``tests/test_roundtrip_svg_dxf.py`` is the
permanent automated version.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

# Segment shorter than this (mm) = end-cap / polygon-close stub, not a trace
# wall → excluded from edge-pairing candidates.
PAIR_MIN_LEN_MM = 0.5
# Edge-pair acceptance thresholds.  Tuned empirically against test6; all are
# heuristic (this is recovered-geometry logic, not a fixed formula).
PAIR_ANG_TOL = 8.0       # deg — parallel tolerance (mod pi, direction-independent)
PAIR_LEN_RATIO = 0.85   # the two edges' lengths must agree to within 15 %
PAIR_SPREAD_MM = 0.05    # perpendicular projections must agree to within 50 µm


def _edge_unit(ax0: float, ay0: float, ax1: float, ay1: float) -> Tuple[float, float]:
    """Unit direction of segment A0→A1, or (0,0) if degenerate."""
    dx, dy = ax1 - ax0, ay1 - ay0
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return 0.0, 0.0
    return dx / L, dy / L


def _ang_diff_pi(a: float, b: float) -> float:
    """Smallest angle between two directions, modulo pi (parallel)."""
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def pair_parallel_edges(
    lines: Sequence[Dict[str, float]],
    mm_per_unit: float,
) -> Tuple[List[Tuple[float, float, float, float, float]], int,
           List[Dict[str, float]]]:
    """Recover trace centreline + width from parallel LINE edge-pairs.

    ``lines`` is the list of dicts ``{x1,y1,x2,y2}`` in **drawing units** (the
    same space ``DxfReader`` builds).  Lengths convert to mm via
    ``mm_per_unit`` for the threshold checks and the recovered width.

    Returns ``(pairs, unpaired_long_count, leftover_lines)`` where ``pairs``
    is a list of ``(cx0, cy0, cx1, cy1, width_mm)`` in **drawing units** for
    the centreline endpoints (the caller scales by mm_per_unit at emission)
    and ``width_mm`` is the perpendicular offset in mm.
    ``unpaired_long_count`` is the number of long edges (≥
    ``PAIR_MIN_LEN_MM``) that found no parallel partner — non-zero means the
    geometry did *not* decompose cleanly and the caller should fall back to
    filled-outline emission rather than emit a partial / bad centreline guess
    (golden rule #4: never force a leftover segment).

    ``leftover_lines`` is the list of input line dicts that were *not*
    consumed by any emitted pair — short end-cap stubs *and* any long edges
    that failed to pair, preserved verbatim so the caller can chain them into
    closed outline polygons via :func:`trace_line_polygons` instead of
    silently dropping them (golden rule #4 applied to the short-segment case:
    previously all sub-``PAIR_MIN_LEN_MM`` segments were excluded from pairing
    *and* excluded from ``unpaired_long_count``, so they vanished — now the
    caller always sees everything left over).
    """
    n = len(lines)

    def seg_mm(idx: int) -> float:
        ln = lines[idx]
        return math.hypot(ln["x2"] - ln["x1"], ln["y2"] - ln["y1"]) * mm_per_unit

    def angle(idx: int) -> float:
        ln = lines[idx]
        return math.atan2(ln["y2"] - ln["y1"], ln["x2"] - ln["x1"])

    lengths_mm = [seg_mm(i) for i in range(n)]
    units = [_edge_unit(lines[i]["x1"], lines[i]["y1"],
                       lines[i]["x2"], lines[i]["y2"]) for i in range(n)]
    angs = [angle(i) for i in range(n)]

    def perp_proj(idx: int, ux: float, uy: float,
                  px: float, py: float) -> Tuple[float, float]:
        ln = lines[idx]
        d1 = (ln["x1"] - px) * (-uy) + (ln["y1"] - py) * ux
        d2 = (ln["x2"] - px) * (-uy) + (ln["y2"] - py) * ux
        return d1, d2

    # --- Global greedy assignment by ascending recovered width --------------
    # Enumerate EVERY candidate (i, j) pair that passes the existing filters
    # (angle tolerance, length-ratio, spread tolerance, the width < min(la, lb)
    # plausibility guard — all unchanged and still necessary), record its
    # ``(width_mm, spread, i, j)``, sort ascending by ``width_mm`` (``spread``
    # only as a tie-breaker for equal widths), then walk the sorted list and
    # greedily accept a candidate when neither edge has been used yet.
    #
    # Why global-by-ascending-width and not the old per-anchor greedy (a real
    # bug — see tests/test_edgepair.py's adjacent-traces fixture):
    #
    # The old loop scored each ``(i, j)`` as ``spread + abs(la - lb)`` and, for
    # each anchor ``i`` in index order, picked the unused ``j`` with the lowest
    # score.  Crucially, **the score had no term for the recovered width
    # (the perpendicular offset) itself** — width only entered as a *rejection*
    # (the plausibility guard) and never as a *preference*.  When two adjacent,
    # close, genuinely-separate traces' walls are all mutually parallel and
    # near-equal in length, ``spread`` is ~0 for every combination and
    # ``abs(la - lb)`` alone decides the winner.  On the 4-line fixture a real
    # partner's walls differ slightly in length while the *wrong* trace's two
    # outer walls of an equal-length adjacent trace coincide exactly in length
    # — so the old scoring picked the cross-trace (0,2) pair (a phantom ~1.3 mm
    # width) over the true (0,1) pair (the real ~0.2 mm width) purely on a
    # coincidental length match, even though the true pair was sitting right
    # there with a much smaller, correct recovered width.
    #
    # The general principle a genuine trace's own two walls are always the
    # *closest* parallel edges to each other is recovered here as: width_mm is
    # the strongest "these two edges belong to the same trace" signal, so let
    # the globally-smallest-available width claim its edges first.  This is a
    # *relative* assignment (smallest-available-width-wins), NOT a fixed absolute
    # width threshold — board designs vary, so no magic "width must be under
    # Xmm" guard is introduced; the change is purely how a winner is picked
    # *among* candidates that already pass the filters.
    candidates: List[Tuple[float, float, int, int]] = []  # (width_mm, spread, i, j)
    for i in range(n):
        if lengths_mm[i] < PAIR_MIN_LEN_MM:
            continue
        ux, uy = units[i]
        for j in range(i + 1, n):
            if lengths_mm[j] < PAIR_MIN_LEN_MM:
                continue
            if _ang_diff_pi(angs[i], angs[j]) > math.radians(PAIR_ANG_TOL):
                continue
            la, lb = lengths_mm[i], lengths_mm[j]
            if la <= 0 or lb <= 0:
                continue
            if min(la, lb) / max(la, lb) < PAIR_LEN_RATIO:
                continue
            d1, d2 = perp_proj(j, ux, uy, lines[i]["x1"], lines[i]["y1"])
            spread = abs(abs(d1) - abs(d2)) * mm_per_unit
            if spread > PAIR_SPREAD_MM:
                continue
            width_mm = (abs(d1) + abs(d2)) * 0.5 * mm_per_unit
            # Plausibility guard (bug: parallel chords of a finely-subdivided
            # *curved* outline, not a straight trace, can satisfy the angle +
            # length-ratio + spread tests above — the two opposite chords of a
            # round pad/pour boundary look "parallel" to this heuristic and pair
            # as a phantom trace whose perpendicular offset (the recovered
            # "width") spans across the curve, not across a real trace.  On
            # test2-F_Cu.dxf that yields widths of ~10 mm / ~3.5 mm against edge
            # lengths of ~3 mm / ~2 mm — i.e. width *larger* than the edge itself
            # — which ``dxf_io`` then emits as a giant stroke (the wrong filled
            # blob).  A genuine trace's two walls are always much closer
            # together than the trace is long, so reject when the recovered
            # width is not clearly shorter than both walls (>= the shorter edge).
            # Conservative ratio: every legitimate pair tested sits at width /
            # min(la, lb) <= ~0.67 (test6: 0.8 mm / >=1.2 mm; test2: 1.7 mm /
            # 4.5 mm), every phantom pair sits at ratio >= 1.7 — a wide gulf.
            if width_mm >= min(la, lb):
                continue
            candidates.append((width_mm, spread, i, j))

    # Smallest recovered width wins first — the strongest same-trace signal.
    # ``spread`` is the tie-breaker for equal widths (a more-parallel pair is
    # the marginally better claim on the rare identical-width tie).
    candidates.sort(key=lambda c: (c[0], c[1]))

    used = [False] * n
    pairs: List[Tuple[float, float, float, float, float]] = []
    for (width_mm, _spread, i, j) in candidates:
        if used[i] or used[j]:
            continue
        li, lj = lines[i], lines[j]
        # Align endpoint-to-endpoint by the SHORTEST cross-pair chord so a
        # pair whose two edges run in opposite drawing directions still
        # yields a straight midpoint centreline, not an X.
        opt1 = (math.hypot(li["x1"] - lj["x1"], li["y1"] - lj["y1"]) +
                math.hypot(li["x2"] - lj["x2"], li["y2"] - lj["y2"]))
        opt2 = (math.hypot(li["x1"] - lj["x2"], li["y1"] - lj["y2"]) +
                math.hypot(li["x2"] - lj["x1"], li["y2"] - lj["y1"]))
        if opt1 <= opt2:
            cx0 = (li["x1"] + lj["x1"]) * 0.5; cy0 = (li["y1"] + lj["y1"]) * 0.5
            cx1 = (li["x2"] + lj["x2"]) * 0.5; cy1 = (li["y2"] + lj["y2"]) * 0.5
        else:
            cx0 = (li["x1"] + lj["x2"]) * 0.5; cy0 = (li["y1"] + lj["y2"]) * 0.5
            cx1 = (li["x2"] + lj["x1"]) * 0.5; cy1 = (li["y2"] + lj["y1"]) * 0.5
        pairs.append((cx0, cy0, cx1, cy1, width_mm))
        used[i] = used[j] = True

    unpaired_long = sum(
        1 for k in range(n)
        if not used[k] and lengths_mm[k] >= PAIR_MIN_LEN_MM)
    # Every input segment a pair did NOT consume — preserved so the caller can
    # chain them into outline polygons instead of dropping them (golden rule
    # #4 for the short-segment case; see docstring).
    leftover_lines = [lines[k] for k in range(n) if not used[k]]
    return pairs, unpaired_long, leftover_lines


def _trace_chains(
        lines: Sequence[Dict[str, float]],
        eps: float = 1e-4
        ) -> Tuple[List[Tuple[List[Tuple[float, float]], List[int]]], int]:
    """Walk the endpoint-adjacency graph into vertex chains.

    Single source of truth for the chain walk shared by
    :func:`trace_line_polygons` (geometry) and
    :func:`lines_form_closed_boundary` (segment-id membership).  Returns
    ``(closed_chains, truncated_count)`` where each ``closed_chains`` entry is
    ``(vertex_list, segment_id_list)`` for a chain that closed back on its
    start vertex, and ``truncated_count`` is the number that hit the step
    budget without closing (surfaced as a WARNING by the callers — golden
    rule #4: never silently emit a truncated oversized polygon).

    Geometry-driven walk via union-find-over-endpoints adjacency (golden rule
    #3): a chain-returning graph of degree-2 / few-higher-degree nodes is a
    genuine connected copper network, never a bug.
    """
    if not lines:
        return [], 0

    def _pt_key(x: float, y: float) -> Tuple[float, float]:
        return (round(x / eps) * eps, round(y / eps) * eps)

    adj: Dict[Tuple[float, float], List[Tuple[Tuple[float, float], int]]] = \
        defaultdict(list)
    for idx, ln in enumerate(lines):
        p1 = _pt_key(ln["x1"], ln["y1"])
        p2 = _pt_key(ln["x2"], ln["y2"])
        adj[p1].append((p2, idx))
        adj[p2].append((p1, idx))

    step_budget = len(lines) + 1  # generous upper bound; reports truncation
    visited_segments: set = set()
    closed_chains: List[Tuple[List[Tuple[float, float]], List[int]]] = []
    truncated = 0

    for start_pt in list(adj.keys()):
        unvisited = [(nbr, idx) for nbr, idx in adj[start_pt]
                     if idx not in visited_segments]
        if not unvisited:
            continue
        first_idx = unvisited[0][1]
        seg_ids: List[int] = [first_idx]
        chain = [start_pt]
        visited_segments.add(first_idx)
        current = unvisited[0][0]
        chain.append(current)

        steps = 0
        while current != start_pt and steps < step_budget:
            steps += 1
            found_next = False
            for nbr, idx in adj[current]:
                if idx in visited_segments:
                    continue
                visited_segments.add(idx)
                seg_ids.append(idx)
                chain.append(nbr)
                current = nbr
                found_next = True
                break
            if not found_next:
                break

        if current != start_pt and steps >= step_budget:
            truncated += 1
            continue

        is_closed = (len(chain) > 2 and
                     abs(chain[-1][0] - chain[0][0]) < eps * 2 and
                     abs(chain[-1][1] - chain[0][1]) < eps * 2)
        if is_closed and len(chain) > 3:
            closed_chains.append((chain, seg_ids))
    return closed_chains, truncated


def lines_form_closed_boundary(
        lines: Sequence[Dict[str, float]],
        mm_per_unit: float,
        eps: float = 1e-4) -> Tuple[List[List[Tuple[float, float]]], bool]:
    """Decide whether the LINE set is a *filled boundary* (copper pour) vs a
    set of free-floating trace walls.

    KiCad's two copper-plot styles are structurally distinct once you look at
    whether the long (≥ ``PAIR_MIN_LEN_MM``) edges close into polygon chains:

    * **Genuine routed traces** — each trace is two isolated parallel walls
      with round end-caps around the boundary; the walls *never* close into a
      polygon by themselves (the end-caps, discretised short, break the
      closure).  Edge-pairing recovers their centreline + width and is the
      right interpretation.
    * **Filled copper pour / outline shape** — the boundary is one closed
      polygon; the long straight walls are simply edges of that polygon and
      the short chords are the curved / rounded corners between them.  The
      paired-around walls of *neighbouring* pours look "parallel" to the
      edge-pair heuristic and produce phantom centrelines (bug: the
      SVG→DXF→… "cloud" of overlapping big strokes on a pour-only file).

    Returns ``(polygons, is_boundary)`` where ``polygons`` is the list of
    closed outlines traced from the full set (point lists in drawing units —
    safe to emit directly via :class:`ClosedPolygon`) and ``is_boundary`` is
    True when a strong majority of the long edges are consumed by those closed
    chains, i.e. this geometry should be emitted as filled outlines and
    edge-pairing skipped (golden rule #4: never force a phantom centreline).
    """
    n = len(lines)
    if n == 0:
        return [], False

    lengths_mm = [
        math.hypot(ln["x2"] - ln["x1"], ln["y2"] - ln["y1"]) * mm_per_unit
        for ln in lines
    ]
    total_long = sum(1 for L in lengths_mm if L >= PAIR_MIN_LEN_MM)
    closed_chains, _truncated = _trace_chains(lines, eps=eps)
    polys = [chain for chain, _ids in closed_chains]
    if not polys or total_long == 0:
        return polys, False

    # Union the segment-id membership of every closed chain, then count how
    # many of the LONG edges fall inside it.  A genuine trace-wall file has 0
    # of its long edges in closed chains (the walls don't close).  A pour
    # boundary file has ~all of them.
    closed_ids: set = set()
    for _chain, seg_ids in closed_chains:
        closed_ids.update(seg_ids)
    long_in_chains = sum(
        1 for i, L in enumerate(lengths_mm)
        if L >= PAIR_MIN_LEN_MM and i in closed_ids)

    # Require a clear majority — robust against a file with a pour plus a
    # stray trace or two — so we only override edge-pairing when the walls
    # really are a boundary, and fall through to the validated pairing path
    # otherwise (test6: 0/N long edges in closed chains → is_boundary False,
    # unchanged).
    is_boundary = long_in_chains * 2 > total_long
    return polys, is_boundary


def trace_line_polygons(lines: Sequence[Dict[str, float]],
                        eps: float = 1e-4) -> List[List[Tuple[float, float]]]:
    """Trace connected LINE segments into closed polygon chains.

    Uses union-find-over-endpoints adjacency (golden rule #3): a connected
    graph of degree-2 nodes with one or two higher-degree nodes is a genuine
    connected copper network, never a bug.  The defensive step budget
    (golden rule #4: ``len(lines)+1``) terminates every chain; a chain that
    hits the budget without closing is *dropped and reported*, never emitted
    as a silently-truncated oversized polygon.

    Returns a list of polygons (each a list of (x, y) in drawing units).
    Closed polygons end where they started (first == last).

    Note — *open* chains (a connected run that does NOT close back on its
    start vertex) are intentionally NOT returned by this function: a polygon is
    defined to be closed.  Open leftover chains carry real corner/connecting
    geometry; surface them via :func:`trace_open_chains` instead.
    """
    closed_chains, truncated = _trace_chains(lines, eps=eps)
    if truncated:
        # Surface it — never silently emit a truncated oversized polygon.
        print(f"[edgepair] WARNING: {truncated} chain(s) hit the step budget "
              f"without closing — dropped rather than emit a truncated "
              f"oversized polygon")
    return [chain for chain, _ids in closed_chains]


def trace_open_chains(
        lines: Sequence[Dict[str, float]],
        eps: float = 1e-4
        ) -> List[List[Tuple[float, float]]]:
    """Trace the *open* chains in a LINE set — connected runs that do NOT
    close back on themselves.

    The companion of :func:`trace_line_polygons` for the corner-gap case
    (a real bug — see ``dxf_io.py``'s ``clean_pairing`` branch and the
    test2-F_Cu.dxf corner at drawing-unit point ~(2.135, -1.556)):
    KiCad's round-end-cap stylization at a trace corner emits short cap
    chords that geometrically bridge one trace wall-pair's end to a
    *different* wall-pair's start.  Those cap chords are too short to be
    pairing candidates so they land in :func:`pair_parallel_edges`'s
    ``leftover`` list — correct.  But ``trace_line_polygons(leftover)`` only
    returns *closed* chains; an open cap chain that runs wall-to-wall (not
    back to itself) is therefore **silently dropped**, contradicting the
    module's own stated intent that leftover segments are "preserved
    verbatim instead of silently dropping them."  The visible concomitant is
    a trace corner that renders with a ~0.5 mm gap.

    This function surfaces those open chains verbatim — the literal DXF cap
    geometry between two trace walls — so the caller can emit each as a thin
    connecting centreline ``Path`` between its two open ends (geometrically
    honest: the actual DXF data, not a snapped/invented connection).

    Returns a list of open chains (each an ordered list of (x, y) drawing
    units; the chain's two ends are the degree-1 endpoints, NOT coincident).

    Approach (distinct from :func:`_trace_chains`'s mid-chain start, which
    fragments open runs): build the endpoint adjacency, then start each
    chain only at a **degree-1** vertex (a true chain end) and walk the whole
    run, taking the single unvisited continuation at each interior degree-2
    node until the other degree-1 end is reached.  A component with no
    degree-1 vertex is a closed loop (handled by ``trace_line_polygons``),
    not open, so it yields nothing here — open and closed surfacing are
    disjoint and idempotent on the same ``leftover`` list.

    A defensive step budget (``len(lines)+1``) terminates every walk; a
    chain that hits it without reaching a degree-1 end is reported and
    dropped (golden rule #4: never silently emit a truncated chain).
    """
    if not lines:
        return []

    def _pt_key(x: float, y: float) -> Tuple[float, float]:
        return (round(x / eps) * eps, round(y / eps) * eps)

    # point -> list of (neighbour_point, segment_idx)
    adj: Dict[Tuple[float, float],
              List[Tuple[Tuple[float, float], int]]] = defaultdict(list)
    for idx, ln in enumerate(lines):
        p1 = _pt_key(ln["x1"], ln["y1"])
        p2 = _pt_key(ln["x2"], ln["y2"])
        adj[p1].append((p2, idx))
        adj[p2].append((p1, idx))

    step_budget = len(lines) + 1
    visited_segments: set = set()
    open_chains: List[List[Tuple[float, float]]] = []
    truncated = 0

    # Seed every walk at a TRUE chain end (degree-1 vertex).  Each open
    # component has exactly two degree-1 ends; seeding at one and marking
    # segments visited as we walk means the component's other end is no
    # longer a fresh seed (its edges are already consumed), so each open
    # component yields exactly one chain.
    end_pts = [pt for pt, nbrs in adj.items() if len(nbrs) == 1]
    for start in end_pts:
        # Pick the component's first unvisited segment out of this end.
        nxt = next((nb for nb, idx in adj[start] if idx not in visited_segments),
                   None)
        if nxt is None:
            continue
        seg_ids: List[int] = [
            idx for nb, idx in adj[start] if nb == nxt
            and idx not in visited_segments]
        first_idx = seg_ids[0]
        visited_segments.add(first_idx)
        chain: List[Tuple[float, float]] = [start, nxt]
        current = nxt
        steps = 0
        # Walk interior degree-2 nodes: exactly one unvisited neighbour each.
        while current != start and steps < step_budget:
            steps += 1
            found_next = False
            for nbr, idx in adj[current]:
                if idx in visited_segments:
                    continue
                visited_segments.add(idx)
                chain.append(nbr)
                current = nbr
                found_next = True
                break
            if not found_next:
                break  # degree-1 terminus reached: open chain complete
        if steps >= step_budget and current != start and current != chain[-1]:
            truncated += 1
            continue
        if len(chain) >= 2 and chain[0] != chain[-1]:
            open_chains.append(chain)

    if truncated:
        # Surface it — never silently emit a truncated open chain.
        print(f"[edgepair] WARNING: {truncated} open chain(s) hit the step "
              f"budget without reaching an end — dropped rather than emit a "
              f"truncated chain")
    return open_chains

