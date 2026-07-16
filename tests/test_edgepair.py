"""Regression test: global same-trace wall matching in ``pair_parallel_edges``.

Companion to ``fabconvert/formats/edgepair.py``'s global-by-ascending-width
assignment fix.  Bug class: when two adjacent, close, genuinely-separate routed
traces have walls that are all mutually parallel and near-equal in length, the
*old* per-anchor greedy matcher scored each ``(i, j)`` as ``spread +
abs(la - lb)`` and therefore decided the winner on a length-mismatch signal alone
— with no preference for the recovered (perpendicular) width — so it could (and
did) pair a trace's two walls to the *wrong* neighbour trace's walls purely
because those happened to coincide in length more closely.  The recovered
"width" of such a phantom cross-trace pair is the inter-trace clearance
(typically ~1 mm+), not the genuine trace width (typically a few tenths of a
mm); the geometric concomitant is a fat, rounded blob in the converted
output instead of two thin parallel traces.

The fixture below is the minimal 4-line case extracted from
``tests/fixtures/test2-F_Cu.dxf`` (drawing units, ``mm_per_unit=25.4``).  Two
genuine ~0.2 mm-wide traces run parallel and close to each other; each
trace's two walls are indices (0,1) and (2,3).  The correct result is two pairs,
each ~0.2 mm wide.  Before the fix the matcher returned two pairs of ~1.3 mm
and ~1.7 mm — every wall matched to the *other* trace's wall.
"""
from __future__ import annotations

from fabconvert.formats.edgepair import pair_parallel_edges

# The minimal repro from the prompt: two genuine ~0.2 mm-wide traces, each
# given as its two parallel wall LINEs in drawing units (mm_per_unit=25.4).
# Edge 0 ↔ edge 1 are the walls of trace A; edge 2 ↔ edge 3 are the walls of
# trace B.  All four are perfectly parallel (spread=0); lengths cluster so that
# the cross-trace length mismatch (e.g. (0,2)) is *smaller* than the true-trace
# mismatch (e.g. (0,1)) — the trap that defeats a length-first scorer.
ADJACENT_TRACES = [
    {"x1": 2.1850393700787403, "y1": -1.125984251968504,
     "x2": 2.0118110236220472, "y2": -1.125984251968504},
    {"x1": 2.0078740157480315, "y1": -1.1181102362204725,
     "x2": 2.183408582677165,  "y2": -1.1181102362204725},
    {"x1": 2.326771653543307,  "y1": -1.1771653543307086,
     "x2": 2.5,                "y2": -1.1771653543307086},
    {"x1": 2.498369212598425,  "y1": -1.18503937007874,
     "x2": 2.322834645669291,  "y2": -1.18503937007874},
]


def test_adjacent_close_parallel_traces_pair_to_own_walls():
    """The permanent regression assertion for the global-match failure mode.

    Two genuine ~0.2 mm traces must pair into two narrow pairs (each wall to its
    own trace's wall), not into one/fat cross-trace pairs (each wall to a
    neighbour trace's wall).
    """
    pairs, unpaired_long, leftover = pair_parallel_edges(ADJACENT_TRACES, 25.4)

    # Decomposition must be clean: every long wall found a partner.
    assert len(pairs) == 2, (
        f"expected exactly 2 same-trace pairs from the 4-line fixture, got "
        f"{len(pairs)}; the global matcher dropped or merged traces.")
    assert unpaired_long == 0, (
        f"no long wall should be left unpaired, got {unpaired_long}; the "
        f"decomposition is no longer clean.")
    assert leftover == [], (
        f"every wall should be consumed by a pair, got {len(leftover)} "
        f"leftover segment(s); an edge vanished from the output.")

    widths = sorted(round(w[4], 4) for w in pairs)
    # The genuine trace width on this fixture is 0.2 mm; both recovered widths
    # must be within 10 µm of that.  This is the headline assertion: the matcher
    # recovered the *narrow* same-trace walls, not the wide inter-trace walls.
    for w in widths:
        assert abs(w - 0.2) <= 0.01, (
            f"recovered pair width {w} mm is not ~0.2 mm — the matcher paired a "
            f"wall to the wrong (neighbour) trace's wall, the global-match "
            f"regression returned (widths={widths}).")
    # Belt-and-braces: no recovered pair may be wider than 1.0 mm — that is the
    # signature of a cross-trace (clearance-spanning) phantom pair (the bug
    # produced ~1.3 mm / ~1.7 mm).  Guards the failure mode even if the exact
    # 0.2 mm target drifts on a future fixture edit.
    for w in widths:
        assert w < 1.0, (
            f"recovered pair width {w} mm exceeds 1.0 mm — a phantom cross-trace "
            f"pair survived (widths={widths}); the global-by-ascending-width "
            f"assignment is not taking precedence over the length signal.")


def test_pair_centrelines_align_to_each_trace():
    """The two recovered centrelines must straddle the right two traces.

    A centreline is the midpoint of its two walls, so its y must lie between the
    two walls' y of whichever trace it represents.  Asserting the two centrelines
    land at the two distinct trace midlines (~ -1.12205 and ~ -1.18110 drawing
    units) proves the walls were paired within a trace, not across traces: a
    cross-trace pair's centreline y would sit halfway *between* the traces
    (~ -1.15), distinct from both midlines.
    """
    pairs, _, _ = pair_parallel_edges(ADJACENT_TRACES, 25.4)
    # Trace A walls at y = -1.12598 and -1.11811 -> midline ≈ -1.12205.
    # Trace B walls at y = -1.17717 and -1.18504 -> midline ≈ -1.18110.
    # A cross-trace pair's centreline would sit halfway BETWEEN the traces
    # (~-1.15157), distinct from both midlines.  Assert each recovered
    # centreline is within 0.01 drawing units (≈0.25 mm) of ONE of the two
    # genuine midlines, and that BOTH midlines are claimed — order-independent
    # and robust to float rounding (no fragile Decimal-of-trailing-zero trap).
    midlines = [-1.12205, -1.18110]
    centreline_ys = [((w[1] + w[3]) * 0.5) for w in pairs]
    for cy in centreline_ys:
        assert min(abs(cy - m) for m in midlines) < 0.01, (
            f"centreline y={cy:.5f} is not near either trace midline {midlines} "
            f"— it sits between the traces, flagging a cross-trace pair.")
    # Both midlines must be claimed (one centreline each), not both centrelines
    # on the same midline.
    for m in midlines:
        assert any(abs(cy - m) < 0.01 for cy in centreline_ys), (
            f"trace midline {m} was not claimed by any recovered centreline "
            f"({centreline_ys}); one trace's walls were not paired to each other.")
