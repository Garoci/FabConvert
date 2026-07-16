"""Recover trace centreline + width from a closed ribbon polygon's medial axis.

The DXF format carries **no width property** (golden rule #2), and KiCad's
plotter emits each routed trace as a closed outline (the two parallel walls + the
round end-caps concatenated into one "stadium" loop).  This module recovers the
centreline + width of such a closed ribbon polygon in one geometric pass via
its **Voronoi medial axis** ("chordal axis transform"), replacing
:mod:`fabconvert.formats.edgepair`'s piecewise wall-pairing + the corner/end-cap
patch stack (``trace_open_chains``, ``_snap_corner_endpoints``, the
connectivity gate) on the closed-loop case.  Corners come out of the medial
axis as bends in one continuous polyline — there are no branch/junction nodes
to merge, so the whole corner-patch whack-a-mole is structurally impossible.

Algorithm (the steps the prototype ``proto_noshapely.py`` validated against
``tests/fixtures/test2-F_Cu.dxf`` — exactly 19 runs, widths
0.1977–0.2012mm, post-prune degree ``{2: ~9800, 1: 2, ≥3: 0}``):

 1. **Densify** the polygon boundary at ``STEP_MM = 0.02`` so the Voronoi diagram
    resolves the medial axis even where the boundary is a coarse polyline.
 2. ``scipy.spatial.Voronoi`` of the densified boundary points.
 3. Keep finite ridges whose **both** endpoints lie strictly inside the polygon
    (ray-casting point-in-polygon — pure Python, no shapely).  Adjacency is
    keyed on the **Voronoi vertex index** (NOT a rounded coordinate — rounding
    a key fragments shared endpoints; diagnosed in the prototype).
 4. **Length-threshold spur pruning.**  A "spur" is a short tentacle off the
    spine: a degree-1 leaf reachable through a run of ORIGINAL-degree-2 nodes
    that terminates at an ORIGINAL-degree-≥3 branch.  Walk each such tentacle,
    summing chord length; prune it iff it terminates at a branch AND the total
    chord is under ``SPUR_MAX = 0.5``mm.  A tentacle whose terminus is an
    endpoint (ORIGINAL degree 1) is a real trace arm — its chord is the trace
    length, well over ``SPUR_MAX``, so it is kept.  ``SPUR_MAX`` sits cleanly
    between cap-spray length (~0.1mm) and a real trace arm (≥1mm), so the
    recovery is stable across a wide threshold (verified: SPUR_MAX 0.3–0.8mm
    all give the identical 19-run / zero-branch result).
 5. **Branch-respecting segment extraction.**  After pruning, emit each maximal
    run of surviving-degree-2 nodes that sits between two anchors (an anchor is
    a surviving-degree ≠ 2 node — a free end or a retained branch).  Walk from
    every anchor through each unvisited degree-2 neighbour to the next anchor.
 6. **Split each run at bends** by **chord deviation**: a vertex whose
    perpendicular distance from the run's start->end chord exceeds
    ``BEND_DEV_FRAC * median_half_width`` (floored at ``BEND_DEV_MIN_MM``) is a
    corner.  (NOT a per-vertex turn angle — the medial axis rounds a chamfered
    corner *smoothly* over many densified vertices, so no single per-vertex turn
    is large enough to fire an angle threshold; measured on test2 the 45 deg
    chamfered corner turned 9.21 deg max per step.  Chord deviation catches the
    corner where the straight chord departs the curved spine.)  Corners become
    the join between two runs sharing an endpoint — continuous, no merge step.
    Drop fragments shorter than ``MIN_RUN_VERTS = 6`` (residual cap debris).
 7. **Per-run width** = the median of its vertices' clearances to the polygon
    boundary × 2 (nearest-segment distance — pure Python, no shapely).
 8. Collapse each straight run to its two end vertices and return
    ``(x0, y0, x1, y1, width_mm)`` in **drawing units** for the coords and mm for
    the width — the same contract :func:`edgepair.pair_parallel_edges` returns,
    so the caller's emission loop is unchanged.

The only added dependency is :mod:`scipy` (``scipy.spatial.Voronoi`` does the
real work).  Shapely was a convenience (``polygon.contains`` + nearest-boundary
distance) and is replaced by the ~12-line ray-cast ``in_poly`` and the
nearest-segment ``ring_dist`` here — no shapely dependency.
"""

from __future__ import annotations

import collections
import math
from typing import List, Sequence, Tuple

import numpy as np
from scipy.spatial import Voronoi

Point = Tuple[float, float]
# (cx0, cy0, cx1, cy1, width_mm) — drawing units for coords, mm for width,
# matches edgepair.pair_parallel_edges's contract.
CentrelineSegment = Tuple[float, float, float, float, float]

# Densify the polygon boundary to <= this spacing before Voronoi (mm).  Fine
# enough that the medial axis is resolved on long-thin ribbon polygons.
STEP_MM = 0.02
# Spur (cap-spray) chord length under which a tentacle is pruned (mm).  Cap
# sprays are ~0.1mm; a real trace arm is >=1mm; 0.5 sits cleanly between.
SPUR_MAX = 0.5
# Bend detection: a run is split at the vertex whose perpendicular distance
# from the run's chord (start->end) exceeds this FRACTION of the run's median
# half-width, floored at BEND_DEV_MIN_MM.  This is *not* a per-vertex turn
# angle — on a chamfered-corner trace the medial axis rounds the 45 deg corner
# smoothly over hundreds of densified vertices (densified at STEP_MM=0.02),
# so no single per-vertex turn reaches an angle threshold (measured: max
# single-step turn 9.21 deg at the test2 corner).  Chord-deviation IS the
# geometrically meaningful "a corner" (the spine bends by an appreciable
# fraction of its own bit radius); it is also scale- and densification-
# independent (0.3 of a 0.1mm half-width = 30um, well above the ~5-10um
# smooth-medial noise, well below the ~100um a real 45 deg corner produces).
BEND_DEV_FRAC = 0.3
BEND_DEV_MIN_MM = 0.010
# Drop runs shorter than this many vertices (the residual 3-vertex cap debris
# the length-threshold prune mostly avoids but the bend split can still leave).
MIN_RUN_VERTS = 6
# Corner-apex sliver merge: a fragment whose chord is under this many times the
# trace's nominal width is the rounded corner apex, not a wall piece (a real
# wall is many widths long; the apex region is at most a few widths).  But a
# real short end-cap straight bit is a SPINE TERMINUS (a neighbour on one side
# only), kept; the apex sliver is INTERIOR (neighbours on both sides, the two
# straight legs) — so the merge keys on "interior AND chord < N×width", never
# touching a terminus fragment.  See ``_merge_subwidth_slivers``.
MAX_CORNER_FRAC = 3.0


def _in_poly(pt: Point, ring: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon — strictly-inside test.

    A Voronoi vertex ON the boundary is not the medial axis of an interior
    region, so the convention (boundary excluded) is what we want: rejects
    boundary vertices, keeps interior ones.  Pure Python; replaces
    ``shapely.Polygon.contains``.
    """
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) + 1e-30) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _ring_dist(pt: Point, ring: Sequence[Point]) -> float:
    """Nearest Euclidean distance from ``pt`` to any edge of the closed ring.

    Used for per-vertex clearance (width = clearance × 2).  Pure Python;
    replaces ``shapely.exterior.distance``.
    """
    best = float("inf")
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 < 1e-24:
            d = math.hypot(pt[0] - ax, pt[1] - ay)
        else:
            t = max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / L2))
            d = math.hypot(pt[0] - (ax + t * dx), pt[1] - (ay + t * dy))
        if d < best:
            best = d
    return best


def _densify(ring: Sequence[Point], step: float) -> np.ndarray:
    """Densify a closed ring (NO closing duplicate) to <= ``step``-spaced pts."""
    pts: List[Point] = []
    n = len(ring)
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / step)))
        for t in range(k):
            f = t / k
            pts.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return np.array(pts, dtype=float)


def _prune_spurs(adj: dict, verts: np.ndarray, orig_deg: dict,
                 spur_max: float) -> set:
    """Iteratively prune spur tentacles shorter than ``spur_max`` (chord length).

    A tentacle is: a currently-surviving degree-1 leaf, plus the
    ORIGINAL-degree-2 nodes reached by always continuing to the unique surviving
    non-pruned neighbour, stopping at the first node whose ORIGINAL degree ≠ 2
    (a branch or an endpoint).  The walked nodes (except that terminus) are
    pruned iff the terminus is a BRANCH (ORIGINAL degree ≥ 3) and the accumulated
    chord is under ``spur_max``.  A tentacle whose terminus is an endpoint
    (ORIGINAL degree 1) is a real trace arm walked inward — its chord is the
    trace length, large, kept.
    """
    pruned: set = set()

    def coord(i: int) -> Point:
        return (verts[i][0], verts[i][1])

    changed = True
    while changed:
        changed = False
        leaves = [n for n in adj
                  if n not in pruned and len(adj[n] - pruned) == 1]
        for leaf in leaves:
            if leaf in pruned:
                continue
            chain = [leaf]
            total = 0.0
            prev = None
            cur = leaf
            while True:
                if cur != leaf and orig_deg.get(cur, 0) != 2:
                    break  # reached a branch/endpoint terminus -> keep, stop
                nbs = [x for x in adj[cur] if x != prev and x not in pruned]
                if not nbs:
                    break
                nxt = nbs[0]
                total += math.hypot(coord(nxt)[0] - coord(cur)[0],
                                    coord(nxt)[1] - coord(cur)[1])
                prev = cur
                cur = nxt
                chain.append(cur)
                if len(chain) > 10000:
                    break
            term = chain[-1]
            if orig_deg.get(term, 0) >= 3 and total < spur_max:
                # Prune everything up to the branch terminus (the branch stays).
                for nd in chain[:-1]:
                    pruned.add(nd)
                changed = True
            # Else: too long, or terminus is an endpoint (a real trace end) —
            # keep the tentacle intact.
    return pruned


def _emit_splits(idx_path: List[int], verts: np.ndarray,
                 ring_mm: Sequence[Point]) -> List[CentrelineSegment]:
    """Split an ordered Voronoi-vertex path at bends, drop short fragments, and
    return one ``(cx0,cy0,cx1,cy1,width_mm)`` centreline per surviving fragment
    (collapsed to its endpoint pair).

    A bend is detected by **chord deviation**, not per-vertex turn angle: a
    vertex whose perpendicular distance from the fragment's start->end chord
    exceeds ``tol = max(BEND_DEV_MIN_MM, BEND_DEV_FRAC * median_half_width)``
    is a corner.  The medial axis of a chamfered-corner trace rounds the corner
    **smoothly** over many densified vertices (no single per-vertex turn is
    large) — a per-vertex angle threshold misses such corners entirely (Bug B:
    test2's top trace corner turned 45 deg cumulative but only 9.21 deg per
    single step, under any reasonable per-vertex threshold).  Chord-deviation
    catches the corner where the straight-across chord departs from the curved
    spine by an appreciable fraction of the trace's own width.
    """
    if len(idx_path) < MIN_RUN_VERTS:
        return []

    def coord(i: int) -> Point:
        return (verts[i][0], verts[i][1])

    # Per-vertex width (clearance x2); the MEDIAN over the whole run is the
    # bend threshold's width reference — a corner fragment carries this
    # trace's nominal width, so the threshold scales with it.
    widths = [2.0 * _ring_dist(coord(i), ring_mm) for i in idx_path]
    if not widths:
        return []
    w_med = sorted(widths)[len(widths) // 2]
    tol = max(BEND_DEV_MIN_MM, BEND_DEV_FRAC * (w_med / 2.0))

    # Recursive chord-deviation split (Ramer-Douglas-Peucker point): find the
    # vertex farthest off the fragment chord; split there iff it exceeds tol.
    fragments: List[List[int]] = [list(idx_path)]
    out_frags: List[List[int]] = []
    while fragments:
        frag = fragments.pop()
        if len(frag) < 3:
            out_frags.append(frag)
            continue
        P0 = coord(frag[0]); P1 = coord(frag[-1])
        vx, vy = P1[0] - P0[0], P1[1] - P0[1]
        L = math.hypot(vx, vy)
        worst_i = -1; worst_d = 0.0
        if L < 1e-12:
            # Degenerate chord — no bend to split on; keep as-is.
            out_frags.append(frag); continue
        for k in range(1, len(frag) - 1):
            Pk = coord(frag[k])
            # Perpendicular distance from P0->P1 (signed tells the side, abs
            # is the deviation we threshold on).
            d = abs((Pk[0] - P0[0]) * vy - (Pk[1] - P0[1]) * vx) / L
            if d > worst_d:
                worst_d = d; worst_i = k
        if worst_i >= 0 and worst_d > tol:
            fragments.append(frag[:worst_i + 1])
            fragments.append(frag[worst_i:])
        else:
            out_frags.append(frag)

    # Drop sub-MIN_RUN_VERTS debris (residual cap fragments), then MERGE the
    # corner-apex slivers a sharp (non-chamfered) corner leaves behind.
    #
    # At a genuinely sharp corner the medial axis rounds the apex over a few
    # densified vertices, then departs along the next wall; the recursion splits
    # at the corner (correct) but ALSO re-splits each straight leg at the apex-
    # adjacent vertex (the densified spine deviates ~30-45um from the leg's chord
    # right at the corner, just over `tol`).  The result is a sub-width middle
    # fragment whose own chord is SHORTER than the trace width — it has no real
    # centreline direction (it's the rounded corner apex, not a wall piece) and
    # its width reads inflated (apex clearance > wall clearance, ~0.22-0.24mm
    # vs ~0.198-0.201mm).  Such a fragment is not a legitimate centreline; merge
    # it into whichever neighbour shares its endpoint and has the closer heading,
    # so a sharp corner recovers as exactly TWO legs sharing one vertex (Bug B's
    # sliver over-split, mechanism (a): the same corner apex counted twice).
    merged = _merge_subwidth_slivers(out_frags, verts, ring_mm, w_med)

    out: List[CentrelineSegment] = []
    for frag in merged:
        if len(frag) < MIN_RUN_VERTS:
            continue
        seg_pts = [coord(i) for i in frag]
        fw = [2.0 * _ring_dist(pt, ring_mm) for pt in seg_pts]
        med = sorted(fw)[len(fw) // 2]
        out.append((seg_pts[0][0], seg_pts[0][1],
                    seg_pts[-1][0], seg_pts[-1][1], med))
    return out


def _merge_subwidth_slivers(raw_frags: List[List[int]], verts: np.ndarray,
                            ring_mm: Sequence[Point],
                            nominal_width_mm: float) -> List[List[int]]:
    """Merge corner-apex slivers a recursion over-split, back into a leg.

    ``raw_frags`` is the RDP recursion's output — contiguous index-runs of the
    spine, but in arbitrary pop order.  Returns the fragments in SPINE order
    (the order the original run walked them) with every fragment whose chord is
    shorter than the trace's nominal width absorbed into whichever neighbour
    shares an endpoint and has the closer heading.

    A sub-width fragment has no real centreline direction (its span is shorter
    than the trace it sits inside); it is the rounded apex a sharp corner leaves
    between the two straight legs, not a wall piece.  Its width reads inflated
    (apex clearance > wall clearance), so emitting it standalone adds a phantom
    sliver at the corner.  Merging it into the leg it continues keeps the corner
    a clean two-leg bend sharing one vertex.

    The spine is recovered from the fragments themselves: consecutive spine
    fragments share an endpoint vertex index, so chaining by shared endpoint
    reconstructs the original ordered walk (independent of pop order).
    """
    # Keep only >=MIN_RUN_VERTS pieces (drop tiny cap debris first).
    kept = [f for f in raw_frags if len(f) >= MIN_RUN_VERTS]
    if len(kept) <= 2:
        return kept
    apex_thresh = MAX_CORNER_FRAC * nominal_width_mm  # chord below N×width AND
    # width inflated above the trace nominal AND interior (both neighbours) =>
    # corner apex, not a wall piece.  A real short end-cap straight bit has a
    # short chord too, but its width stays ~nominal (it sits on a straight wall,
    # not over the apex where clearance widens) — so the width test separates
    # apex debris from a real cap-leg (kept).  test2 measures: apex slivers
    # 0.22526/0.22550/0.24171mm vs the real L=0.0199mm cap-leg 0.20056mm.

    def chord_len(f: List[int]) -> float:
        a, b = verts[f[0]], verts[f[-1]]
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def frag_width(f: List[int]) -> float:
        # The fragment's median clearance x2 (its own recovered width).
        fw = [2.0 * _ring_dist((verts[i][0], verts[i][1]), ring_mm)
              for i in f]
        return sorted(fw)[len(fw) // 2]

    # Reconstruct spine order.  Consecutive spine fragments share an endpoint
    # vertex index; build a chain by walking from one chain end to the other.
    endpts = collections.Counter()
    for f in kept:
        endpts[f[0]] += 1
        endpts[f[-1]] += 1
    once = [k for k, c in endpts.items() if c == 1]
    if len(once) != 2:
        # Closed spine or degenerate — leave fragments unmerged (no harm beyond
        # the dropped debris); the sliver case here is rare and won't compound.
        return kept
    chain: List[List[int]] = []
    used: set = set()
    cur_end = once[0]
    remaining = list(range(len(kept)))
    while remaining:
        pick = None
        for idx in remaining:
            f = kept[idx]
            if idx in used:
                continue
            if f[0] == cur_end:
                pick = (idx, f, False)
                break
            if f[-1] == cur_end:
                pick = (idx, f, True)
                break
        if pick is None:
            break
        idx, f, rev = pick
        used.add(idx)
        remaining.remove(idx)
        if rev:
            f = list(reversed(f))
        chain.append(f)
        cur_end = f[-1]
    if len(chain) != len(kept):
        return kept  # couldn't fully reconstruct; leave as-is

    # Collapse contiguous runs of INTERIOR apex fragments.  A sharp corner
    # leaves a little chain of slivers through the rounded apex (the densified
    # spine deviates ~just over tol from each straight leg's chord right at the
    # apex, so the recursion splits each leg again there — measured on test2:
    # two ~9-vertex sub-width fragments flanking the apex vertex at (1.9727,
    # -1.3994); on a synthetic sharp 90-deg L with a short arm, one ~0.1mm
    # chord fragment).  Such a fragment carries no real wall direction (its span
    # is a few times the trace width at most); the geometrically honest reading
    # is a SINGLE BEND at one apex vertex shared by the two straight legs.
    #
    # Crucially, a real short end-cap straight bit (e.g. test2's L=0.0199mm
    # cap-leg, width 0.20056 — normal) is a SPINE TERMINUS (a neighbour on one
    # side only); the apex sliver is INTERIOR (neighbours on both sides — the two
    # straight legs).  So merge keys on "interior AND chord < N×width": termini
    # are never touched.  Collapse each run to its median vertex (the apex) and
    # extend BOTH flanking legs to terminate there, so the corner is exactly two
    # legs sharing one vertex — continuous, no phantom third sliver, and neither
    # leg is pulled past the apex (which would re-open a gap).
    def is_apex(i: int) -> bool:
        # Interior fragment (both neighbours), short chord, AND inflated width.
        # The width inflation is the key discriminator vs a real short end-cap
        # straight bit: a cap-leg sits on a straight wall (clearance = nominal
        # half-width), an apex fragment sits over the rounded corner (clearance
        # widens past the walls).  test2: cap-leg L=0.0199mm width 0.20056
        # (kept) vs apex slivers width 0.22526/0.22550/0.24171 (merged).
        if i == 0 or i == len(chain) - 1:
            return False  # spine terminus — a real trace end / cap-leg, kept
        f = chain[i]
        if chord_len(f) >= apex_thresh:
            return False
        return frag_width(f) > nominal_width_mm * 1.05

    result: List[List[int]] = []
    i = 0
    while i < len(chain):
        if not is_apex(i):
            result.append(chain[i])
            i += 1
            continue
        # Collect the maximal contiguous run of interior apex fragments through
        # the corner; their combined vertex set IS the apex region.
        run = list(chain[i])
        while i + 1 < len(chain) and is_apex(i + 1):
            nxt = chain[i + 1]
            # Chain adjacency: nxt[0] == run[-1] (forward) or nxt[-1]==run[-1]
            # (reversed continuation).  Extend the apex region accordingly.
            if nxt[0] == run[-1]:
                run = run + nxt[1:]
            elif nxt[-1] == run[-1]:
                run = run + list(reversed(nxt))[1:]
            else:
                break
            i += 1
        apex = run[len(run) // 2]    # the apex vertex (median of the region)
        prev = result[-1] if result else None
        nxt_leg = chain[i + 1] if i + 1 < len(chain) else None
        # Extend the flanking legs to terminate AT the apex so they SHARE it.
        if prev is not None:
            # prev[-1] == run[0]; bridge prev -> apex through the apex region's
            # near half (keeps the curvature, lands the leg end exactly at apex).
            result[-1] = prev + run[:run.index(apex) + 1]
        if nxt_leg is not None:
            # nxt starts at run[-1]; prepend the far half (apex -> run[-1]) so
            # the leg departs FROM the apex vertex.
            far = run[run.index(apex):]   # apex .. run[-1]
            chain[i + 1] = far + nxt_leg[1:]
        i += 1
    return result


def recover_centrelines(
    polygon_du_points: Sequence[Point],
    mm_per_unit: float,
) -> List[CentrelineSegment]:
    """Recover centreline + width from one closed ribbon polygon.

    ``polygon_du_points`` is the polygon's boundary in **drawing units**, the
    same space ``DxfReader`` builds ``line_dicts`` in.  Returns a list of
    ``(cx0, cy0, cx1, cy1, width_mm)`` — centreline endpoints in drawing units,
    recovered width in mm — the same contract
    :func:`fabconvert.formats.edgepair.pair_parallel_edges` returns, so the
    caller scales coordinates by ``mm_per_unit`` at emission exactly as it does
    for edge-pairs.

    The returned segments are already split at ≥10° bends; consecutive segments
    that came from the same spine share an endpoint coordinate, so the emitted
    centreline is continuous through corners with no merge step (the structural
    win — the pruned medial axis has zero branch points on a trace ribbon).

    A pour (broad copper region rather than a thin ribbon) yields a degenerate
    medial axis whose runs are wide and short; the caller's existing width
    plausibility guard (``width < min(run_length)`` — applied in ``dxf_io``)
    filters those out so pours fall back to filled outlines.  The PRIMARY
    pour-vs-trace discrimination stays in
    :func:`edgepair.lines_form_closed_boundary`'s ``is_boundary``.
    """
    if len(polygon_du_points) < 3:
        return []
    sc = mm_per_unit
    # Drawing units -> mm.  Drop a closing duplicate vertex if present.
    ring_mm: List[Point] = [(p[0] * sc, p[1] * sc) for p in polygon_du_points]
    if (len(ring_mm) >= 2
            and math.hypot(ring_mm[0][0] - ring_mm[-1][0],
                           ring_mm[0][1] - ring_mm[-1][1]) < 1e-9):
        ring_mm = ring_mm[:-1]
    if len(ring_mm) < 3:
        return []

    bpts = _densify(ring_mm, STEP_MM)
    vor = Voronoi(bpts)
    verts = vor.vertices
    inside_idx = [_in_poly((v[0], v[1]), ring_mm) for v in verts]
    adj: dict = collections.defaultdict(set)
    for (p, q) in vor.ridge_vertices:
        if p < 0 or q < 0:
            continue
        if not inside_idx[p] or not inside_idx[q]:
            continue
        adj[p].add(q)
        adj[q].add(p)
    if not adj:
        return []
    orig_deg = {n: len(adj[n]) for n in adj}

    pruned = _prune_spurs(adj, verts, orig_deg, SPUR_MAX)
    core = {n: adj[n] - pruned for n in adj if n not in pruned}
    if not core:
        return []

    # Branch-respecting extraction: walk from each anchor (surviving-degree ≠ 2)
    # through unvisited degree-2 nodes to the next anchor.  Consecutive runs
    # share the anchor vertex index, so the centreline is continuous.
    anchors = [n for n in core if len(core[n]) != 2]
    visited_pairs: set = set()
    results: List[CentrelineSegment] = []

    for a in anchors:
        for nbr in core[a]:
            if (a, nbr) in visited_pairs:
                continue
            path = [a, nbr]
            prev = a
            cur = nbr
            while len(core[cur]) == 2:
                nxts = [x for x in core[cur] if x != prev]
                if not nxts:
                    break
                nxt = nxts[0]
                path.append(nxt)
                prev = cur
                cur = nxt
            for i in range(len(path) - 1):
                visited_pairs.add((path[i], path[i + 1]))
                visited_pairs.add((path[i + 1], path[i]))
            results.extend(_emit_splits(path, verts, ring_mm))
    # Coordinates so far are in mm (Voronoi runs on ring_mm).  The contract is
    # drawing units (so the caller's af.to_intermediate_x/y, which multiply by
    # sc, lands at the right mm) — divide coords back by sc.  Width (the 5th
    # element) stays in mm.  (Bug A: this conversion was missing, so every
    # recovered centreline was emitted ~sc× too large and the Gerber writer's
    # shared flip_axis — derived from geom.bounds() — was consequently pulled
    # off the trace/pad frame, displacing pads by ~sc× the bbox height.)
    out: List[CentrelineSegment] = []
    for (x0, y0, x1, y1, w) in results:
        out.append((x0 / sc, y0 / sc, x1 / sc, y1 / sc, w))
    return out


def spine_degree(
    polygon_du_points: Sequence[Point],
    mm_per_unit: float,
) -> collections.Counter:
    """Diagnostic: the post-prune degree distribution of the medial-axis spine.

    Used by the unit test and the regression report to confirm the structural
    property — a trace ribbon prunes to degree ``{2: …, 1: 2, ≥3: 0}`` (two
    real trace termini, zero branch points); a pour does not.  Exposed so the
    contract pin (``>=3: 0``) is testable without re-implementing the prune.
    """
    if len(polygon_du_points) < 3:
        return collections.Counter()
    sc = mm_per_unit
    ring_mm: List[Point] = [(p[0] * sc, p[1] * sc) for p in polygon_du_points]
    if (len(ring_mm) >= 2
            and math.hypot(ring_mm[0][0] - ring_mm[-1][0],
                           ring_mm[0][1] - ring_mm[-1][1]) < 1e-9):
        ring_mm = ring_mm[:-1]
    if len(ring_mm) < 3:
        return collections.Counter()
    bpts = _densify(ring_mm, STEP_MM)
    vor = Voronoi(bpts)
    verts = vor.vertices
    inside_idx = [_in_poly((v[0], v[1]), ring_mm) for v in verts]
    adj: dict = collections.defaultdict(set)
    for (p, q) in vor.ridge_vertices:
        if p < 0 or q < 0:
            continue
        if not inside_idx[p] or not inside_idx[q]:
            continue
        adj[p].add(q); adj[q].add(p)
    if not adj:
        return collections.Counter()
    orig_deg = {n: len(adj[n]) for n in adj}
    pruned = _prune_spurs(adj, verts, orig_deg, SPUR_MAX)
    core = {n: adj[n] - pruned for n in adj if n not in pruned}
    return collections.Counter(len(s) for s in core.values())
