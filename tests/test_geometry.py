"""
Tests for the geometry model (build) and validator (validate).

Position arithmetic is hand-traced from 4HDNOAA.NCC.
"""

from __future__ import annotations

import math
import pytest
from pathlib import Path

from ncc_converter.loader import load, RawLine
from ncc_converter.lexer import lex
from ncc_converter.parser import parse
from ncc_converter.operations import ArcCut, LinearCut, RapidMove, ToolDown, ToolUp
from ncc_converter.geometry import (
    ARC_RADIUS_TOL, CLOSURE_TOL,
    ArcDirectionWarning, ArcRadiusError,
    Contour, ContiguityError, ContourClosureWarning,
    GeometryModel, ResolvedArc, ValidationResult,
    build, validate,
)

SAMPLE = Path(__file__).parent / "samples" / "good" / "4HDNOAA.NCC"
APPROX = pytest.approx
_D = RawLine(file_line=0, text="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_model() -> GeometryModel:
    return build(parse(lex(load(SAMPLE))).operations)


def _arc(direction, sx, sy, ex, ey, ci, cj) -> ArcCut:
    return ArcCut(source=_D, direction=direction,
                  start_x=sx, start_y=sy, end_x=ex, end_y=ey,
                  center_i=ci, center_j=cj, feedrate=None)


def _linear(sx, sy, ex, ey) -> LinearCut:
    return LinearCut(source=_D, start_x=sx, start_y=sy,
                     end_x=ex, end_y=ey, feedrate=None)


def _rapid(sx, sy, ex, ey) -> RapidMove:
    return RapidMove(source=_D, start_x=sx, start_y=sy, end_x=ex, end_y=ey)


# ---------------------------------------------------------------------------
# ResolvedArc
# ---------------------------------------------------------------------------

class TestResolvedArc:
    def test_centre_is_start_plus_ij(self):
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 2.0, 1.5, 2.5, -0.5, 0.5))
        assert ra.center_x == APPROX(0.5)
        assert ra.center_y == APPROX(2.5)

    def test_perfect_arc_zero_radius_error(self):
        # Quarter-circle: start=(1,0), end=(0,1), centre=(0,0)
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0))
        assert ra.radius_error == APPROX(0.0, abs=1e-9)

    def test_radius_is_distance_to_centre(self):
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0))
        assert ra.radius == APPROX(1.0, abs=1e-9)

    def test_corrupt_arc_has_nonzero_error(self):
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 0.0, 0.0, 1.5, -1.0, 0.0))
        assert ra.radius_error > 0.1

    def test_start_angle_at_positive_x(self):
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0))
        assert ra.start_angle == APPROX(0.0, abs=1e-9)

    def test_end_angle_at_positive_y(self):
        ra = ResolvedArc.from_arc(_arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0))
        assert ra.end_angle == APPROX(math.pi / 2, abs=1e-6)


# ---------------------------------------------------------------------------
# Contour model — sample file
# ---------------------------------------------------------------------------

class TestContourModel:
    def setup_method(self):
        self.model = load_model()

    def test_contour_count(self):
        assert len(self.model.contours) == 9

    def test_all_contours_have_pierce(self):
        assert all(isinstance(c.pierce_op, ToolDown) for c in self.model.contours)

    def test_all_contours_have_lift(self):
        assert all(isinstance(c.lift, ToolUp) for c in self.model.contours)

    def test_all_contours_have_moves(self):
        assert all(len(c.moves) > 0 for c in self.model.contours)

    def test_first_contour_pierce_position(self):
        c = self.model.contours[0]
        assert c.pierce_x == APPROX(4.37)
        assert c.pierce_y == APPROX(-2.2)

    def test_first_contour_structure(self):
        # linear lead-in, then 5 arcs
        moves = self.model.contours[0].moves
        assert isinstance(moves[0], LinearCut)
        assert all(isinstance(m, ArcCut) for m in moves[1:])

    def test_first_contour_move_count(self):
        assert len(self.model.contours[0].moves) == 6

    def test_outer_profile_has_many_moves(self):
        # Last contour is the outer profile — many more moves
        assert len(self.model.contours[-1].moves) == 24

    def test_no_rapid_moves_inside_contours(self):
        for c in self.model.contours:
            assert not any(isinstance(m, RapidMove) for m in c.moves)


# ---------------------------------------------------------------------------
# Contour closure
# ---------------------------------------------------------------------------

class TestContourClosure:
    def setup_method(self):
        self.model = load_model()

    def test_hole_contours_are_closed(self):
        # First 8 contours are circular holes — should close within CLOSURE_TOL
        # (measured from end of lead-in / start of arc sequence)
        for c in self.model.contours[:8]:
            assert c.is_closed, (
                f"Hole contour at ({c.pierce_x}, {c.pierce_y}) not closed: "
                f"gap={c.closure_gap:.4f}"
            )

    def test_outer_profile_is_open(self):
        # The 9th contour is the outer profile — it does not close
        assert not self.model.contours[-1].is_closed

    def test_hole_closure_gaps_are_small(self):
        for c in self.model.contours[:8]:
            assert c.closure_gap < CLOSURE_TOL


# ---------------------------------------------------------------------------
# Resolved arcs — sample file
# ---------------------------------------------------------------------------

class TestModelArcs:
    def setup_method(self):
        self.model = load_model()

    def test_resolved_arc_count(self):
        ops = parse(lex(load(SAMPLE))).operations
        n_arcs = sum(1 for o in ops if isinstance(o, ArcCut))
        assert len(self.model.resolved_arcs) == n_arcs

    def test_all_radii_positive(self):
        assert all(ra.radius > 0 for ra in self.model.resolved_arcs)

    def test_all_radius_errors_within_tolerance(self):
        # Burny's 2 d.p. coordinate precision causes inherent errors ≤ 0.01 mm.
        # Our tolerance is 0.015 — all arcs in a well-formed file must pass.
        bad = [ra for ra in self.model.resolved_arcs if ra.radius_error > ARC_RADIUS_TOL]
        assert bad == [], (
            f"{len(bad)} arcs exceed radius tolerance:\n" +
            "\n".join(f"  line {ra.op.source.file_line}: err={ra.radius_error:.6f}"
                      for ra in bad)
        )


# ---------------------------------------------------------------------------
# Validation — sample file (clean)
# ---------------------------------------------------------------------------

class TestValidationCleanFile:
    def setup_method(self):
        self.model = load_model()
        self.result = validate(self.model)

    def test_no_errors(self):
        assert self.result.errors == [], str(self.result)

    def test_ok_property(self):
        assert self.result.ok

    def test_only_one_closure_warning(self):
        # The outer profile is the only open contour
        closure_warns = [w for w in self.result.warnings
                         if isinstance(w, ContourClosureWarning)]
        assert len(closure_warns) == 1

    def test_no_direction_warnings(self):
        dir_warns = [w for w in self.result.warnings
                     if isinstance(w, ArcDirectionWarning)]
        assert dir_warns == []


# ---------------------------------------------------------------------------
# Validation — injected defects
# ---------------------------------------------------------------------------

class TestValidationArcRadius:
    def test_bad_radius_detected(self):
        arc = _arc(3, 1.0, 0.0, 0.0, 1.5, -1.0, 0.0)  # wrong end
        ops = [_rapid(0,0,1,0), ToolDown(source=_D), arc, ToolUp(source=_D)]
        result = validate(build(ops))
        assert any(isinstance(e, ArcRadiusError) for e in result.errors)

    def test_good_radius_passes(self):
        arc = _arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0)  # perfect quarter-circle
        ops = [_rapid(0,0,1,0), ToolDown(source=_D), arc, ToolUp(source=_D)]
        result = validate(build(ops))
        assert not any(isinstance(e, ArcRadiusError) for e in result.errors)


class TestValidationArcDirection:
    def test_wrong_direction_warns(self):
        # G2 (CW) declared but geometry is CCW
        arc = _arc(2, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0)
        ops = [_rapid(0,0,1,0), ToolDown(source=_D), arc, ToolUp(source=_D)]
        result = validate(build(ops))
        warns = [w for w in result.warnings if isinstance(w, ArcDirectionWarning)]
        assert len(warns) == 1
        assert warns[0].declared_direction == 2
        assert warns[0].implied_direction == 3

    def test_correct_direction_no_warn(self):
        arc = _arc(3, 1.0, 0.0, 0.0, 1.0, -1.0, 0.0)
        ops = [_rapid(0,0,1,0), ToolDown(source=_D), arc, ToolUp(source=_D)]
        result = validate(build(ops))
        warns = [w for w in result.warnings if isinstance(w, ArcDirectionWarning)]
        assert warns == []


class TestValidationContourClosure:
    def test_open_contour_warns(self):
        ops = [_rapid(0,0,0,0), ToolDown(source=_D),
               _linear(0,0,5,0), ToolUp(source=_D)]
        result = validate(build(ops))
        assert any(isinstance(w, ContourClosureWarning) for w in result.warnings)

    def test_closed_contour_no_warn(self):
        ops = [_rapid(0,0,0,0), ToolDown(source=_D),
               _linear(0,0,1,0), _linear(1,0,0,0),
               ToolUp(source=_D)]
        result = validate(build(ops))
        assert not any(isinstance(w, ContourClosureWarning) for w in result.warnings)
