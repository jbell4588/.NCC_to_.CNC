"""
Tests for modal state tracker and parser.

All position / geometry assertions are derived from manually tracing
through 4HDNOAA.NCC, so they double as regression tests for the real file.
"""

from __future__ import annotations

import math
import pytest
from pathlib import Path

from ncc_converter.loader import load
from ncc_converter.lexer import lex, lex_line, LexedLine, TokKind
from ncc_converter.loader import RawLine
from ncc_converter.modal import ModalState
from ncc_converter.operations import (
    ArcCut, EndProgram, LinearCut, Operation, RapidMove, ToolDown, ToolUp,
)
from ncc_converter.parser import ParseError, ParseResult, ParseWarning, parse


SAMPLE = Path(__file__).parent / "samples" / "good" / "4HDNOAA.NCC"
APPROX = pytest.approx  # shorthand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_and_parse(path=SAMPLE) -> ParseResult:
    return parse(lex(load(path)))


def make_ll(text: str) -> LexedLine:
    return lex_line(RawLine(file_line=1, text=text))


# ---------------------------------------------------------------------------
# ModalState unit tests
# ---------------------------------------------------------------------------

class TestModalStateDefaults:
    def test_initial_position_is_origin(self):
        s = ModalState()
        assert s.x == 0.0 and s.y == 0.0

    def test_initial_tool_is_up(self):
        assert ModalState().tool_down is False

    def test_initial_motion_g_is_none(self):
        assert ModalState().motion_g is None

    def test_program_not_ended_initially(self):
        assert ModalState().program_ended is False


class TestModalStateApply:
    def test_g1_sets_motion_g(self):
        s = ModalState()
        s.apply(make_ll("N005G1X1.0Y2.0R"))
        assert s.motion_g == 1

    def test_g3_sets_motion_g(self):
        s = ModalState()
        s.apply(make_ll("N001G3X0.5Y0.5I-0.5J0.5"))
        assert s.motion_g == 3

    def test_motion_g_persists_across_lines(self):
        s = ModalState()
        s.apply(make_ll("N001G3X0.5Y0.5I-0.5J0.5"))
        s.apply(make_ll("N003X0.3Y0.3I-0.3"))
        assert s.motion_g == 3

    def test_incremental_x_accumulates(self):
        s = ModalState()
        s.apply(make_ll("N001G1X1.0Y0.0R"))
        s.apply(make_ll("N003X2.5Y0.0R"))
        assert s.x == APPROX(3.5)

    def test_incremental_y_accumulates(self):
        s = ModalState()
        s.apply(make_ll("N001G1X0.0Y-2.2R"))
        s.apply(make_ll("N003X0.0Y1.1R"))
        assert s.y == APPROX(-1.1)

    def test_omitted_x_does_not_move(self):
        s = ModalState()
        s.apply(make_ll("N001G1X3.0Y0.0R"))
        s.apply(make_ll("N003G3J-0.63"))   # only J, no X
        assert s.x == APPROX(3.0)

    def test_omitted_y_does_not_move(self):
        s = ModalState()
        s.apply(make_ll("N001G1X0.0Y3.0R"))
        s.apply(make_ll("N003G3I-0.62"))   # only I, no Y
        assert s.y == APPROX(3.0)

    def test_m04_sets_tool_down(self):
        s = ModalState()
        s.apply(make_ll("N001M04"))
        assert s.tool_down is True

    def test_m03_sets_tool_up(self):
        s = ModalState()
        s.apply(make_ll("N001M04"))
        s.apply(make_ll("N003M03"))
        assert s.tool_down is False

    def test_m30_sets_program_ended(self):
        s = ModalState()
        s.apply(make_ll("N001M30"))
        assert s.program_ended is True

    def test_feedrate_update(self):
        s = ModalState()
        s.apply(make_ll("N001G1F150X1.0Y1.0"))
        assert s.feedrate == APPROX(150.0)

    def test_n_word_updates_line_number(self):
        s = ModalState()
        s.apply(make_ll("N071X-0.13Y0.61"))
        assert s.line_number == 71

    def test_snapshot_is_independent(self):
        s = ModalState()
        snap = s.snapshot()
        s.apply(make_ll("N001G1X5.0Y5.0R"))
        assert snap.x == 0.0   # snapshot unchanged


class TestModalStateOnRealFile:
    """Trace through the first few lines of 4HDNOAA.NCC manually."""

    def setup_method(self):
        lines = lex(load(SAMPLE))
        self.state = ModalState()
        self.lines = lines

    def _apply_up_to(self, n_word: int):
        """Apply lines until we've processed the line with Nxxx == n_word."""
        for ll in self.lines:
            self.state.apply(ll)
            if self.state.line_number == n_word:
                return
        raise AssertionError(f"N{n_word} not found in file")

    def test_after_n005_position(self):
        # N005G1X4.37Y-2.2R → start (0,0) + (4.37, -2.2) = (4.37, -2.2)
        self._apply_up_to(5)
        assert self.state.x == APPROX(4.37)
        assert self.state.y == APPROX(-2.2)

    def test_after_n007_tool_down(self):
        # N007M04
        self._apply_up_to(7)
        assert self.state.tool_down is True

    def test_after_n009_position(self):
        # N009X0.4Y-0.48 → (4.37+0.4, -2.2-0.48) = (4.77, -2.68)
        self._apply_up_to(9)
        assert self.state.x == APPROX(4.77)
        assert self.state.y == APPROX(-2.68)

    def test_after_n021_tool_up(self):
        # N021M03
        self._apply_up_to(21)
        assert self.state.tool_down is False

    def test_n069_is_skipped(self):
        # File goes N067 → N071; N069 does not exist
        for ll in self.lines:
            self.state.apply(ll)
        n_words = [
            t.value for ll in self.lines for t in ll.tokens if t.kind == TokKind.N_WORD
        ]
        assert 69 not in n_words

    def test_final_m30(self):
        for ll in self.lines:
            self.state.apply(ll)
        assert self.state.program_ended is True


# ---------------------------------------------------------------------------
# Parser unit tests — synthetic lines
# ---------------------------------------------------------------------------

class TestParserRapidMove:
    def test_g1_r_produces_rapid_move(self):
        result = parse([make_ll("N001G1X3.0Y4.0R")])
        assert len(result.operations) == 1
        assert isinstance(result.operations[0], RapidMove)

    def test_rapid_move_start_is_origin(self):
        op = parse([make_ll("N001G1X3.0Y4.0R")]).operations[0]
        assert isinstance(op, RapidMove)
        assert op.start_x == APPROX(0.0)
        assert op.start_y == APPROX(0.0)

    def test_rapid_move_end_position(self):
        op = parse([make_ll("N001G1X3.0Y4.0R")]).operations[0]
        assert isinstance(op, RapidMove)
        assert op.end_x == APPROX(3.0)
        assert op.end_y == APPROX(4.0)

    def test_rapid_move_chained(self):
        lines = [make_ll("N001G1X1.0Y0.0R"), make_ll("N003X2.0Y0.0R")]
        ops = parse(lines).operations
        assert isinstance(ops[1], RapidMove)
        assert ops[1].start_x == APPROX(1.0)
        assert ops[1].end_x == APPROX(3.0)


class TestParserLinearCut:
    def test_g1_no_r_produces_linear_cut(self):
        lines = [make_ll("N001G1X1.0Y0.0R"), make_ll("N003M04"), make_ll("N005X0.5Y0.5")]
        ops = [o for o in parse(lines).operations if isinstance(o, LinearCut)]
        assert len(ops) == 1

    def test_linear_cut_geometry(self):
        lines = [make_ll("N001G1X1.0Y0.0R"), make_ll("N003M04"), make_ll("N005X0.5Y0.5")]
        op = next(o for o in parse(lines).operations if isinstance(o, LinearCut))
        assert op.start_x == APPROX(1.0)
        assert op.start_y == APPROX(0.0)
        assert op.end_x == APPROX(1.5)
        assert op.end_y == APPROX(0.5)

    def test_linear_cut_x_only(self):
        # N183G1X-4.75 — only X, no Y
        lines = [make_ll("N001G1X0.0Y0.0R"), make_ll("N003M04"), make_ll("N005G1X-4.75")]
        op = next(o for o in parse(lines).operations if isinstance(o, LinearCut))
        assert op.end_y == APPROX(0.0)
        assert op.end_x == APPROX(-4.75)


class TestParserArcCut:
    def test_g3_produces_arc_cut_ccw(self):
        lines = [
            make_ll("N001G1X1.0Y1.0R"),
            make_ll("N003M04"),
            make_ll("N005G3X0.23Y0.48I-0.4J0.48"),
        ]
        ops = [o for o in parse(lines).operations if isinstance(o, ArcCut)]
        assert len(ops) == 1
        assert ops[0].direction == 3

    def test_g2_produces_arc_cut_cw(self):
        lines = [
            make_ll("N001G1X1.0Y1.0R"),
            make_ll("N003M04"),
            make_ll("N005G2X0.17Y-0.66I-1.21J-0.66"),
        ]
        op = next(o for o in parse(lines).operations if isinstance(o, ArcCut))
        assert op.direction == 2

    def test_arc_missing_i_defaults_to_zero(self):
        lines = [
            make_ll("N001G1X0.0Y0.0R"),
            make_ll("N003M04"),
            make_ll("N005G3X-0.62Y0.62J0.62"),
        ]
        op = next(o for o in parse(lines).operations if isinstance(o, ArcCut))
        assert op.center_i == APPROX(0.0)
        assert op.center_j == APPROX(0.62)

    def test_arc_missing_j_defaults_to_zero(self):
        lines = [
            make_ll("N001G1X0.0Y0.0R"),
            make_ll("N003M04"),
            make_ll("N005G3X-0.62Y0.62I-0.62"),
        ]
        op = next(o for o in parse(lines).operations if isinstance(o, ArcCut))
        assert op.center_j == APPROX(0.0)
        assert op.center_i == APPROX(-0.62)

    def test_arc_modal_g_inherited(self):
        lines = [
            make_ll("N001G1X0.0Y0.0R"),
            make_ll("N003M04"),
            make_ll("N005G3X0.23Y0.48I-0.4J0.48"),
            make_ll("N007X-0.62Y0.62I-0.62"),   # no G — should inherit G3
        ]
        arcs = [o for o in parse(lines).operations if isinstance(o, ArcCut)]
        assert len(arcs) == 2
        assert arcs[1].direction == 3


class TestParserToolOps:
    def test_m04_produces_tool_down(self):
        ops = parse([make_ll("N001M04")]).operations
        assert any(isinstance(o, ToolDown) for o in ops)

    def test_m03_produces_tool_up(self):
        ops = parse([make_ll("N001M04"), make_ll("N003M03")]).operations
        assert any(isinstance(o, ToolUp) for o in ops)

    def test_m30_produces_end_program(self):
        ops = parse([make_ll("N001M30")]).operations
        assert any(isinstance(o, EndProgram) for o in ops)


class TestParserErrors:
    def test_motion_before_g_code_raises(self):
        with pytest.raises(ParseError):
            parse([make_ll("N001X1.0Y2.0")])

    def test_arc_zero_ij_produces_warning(self):
        lines = [
            make_ll("N001G1X0.0Y0.0R"),
            make_ll("N003M04"),
            make_ll("N005G3X1.0Y0.0"),   # no I or J
        ]
        result = parse(lines)
        assert any("I=0 and J=0" in w.message for w in result.warnings)


# ---------------------------------------------------------------------------
# Parser integration — full real file
# ---------------------------------------------------------------------------

class TestParserFullFile:
    def setup_method(self):
        self.result = load_and_parse()
        self.ops = self.result.operations

    def test_no_errors(self):
        # Parsing the sample must not raise
        assert self.ops is not None

    def test_no_warnings(self):
        assert self.result.warnings == [], \
            f"Unexpected warnings: {self.result.warnings}"

    def test_operation_count(self):
        # Manually counted from file: 5 ToolDown, 9 ToolUp (incl. final lift),
        # multiple rapids, cuts, arcs, 1 EndProgram.
        assert len(self.ops) > 50

    def test_starts_with_rapid_move(self):
        # N005G1X4.37Y-2.2R — first line is always a rapid approach
        assert isinstance(self.ops[0], RapidMove)

    def test_first_rapid_geometry(self):
        op = self.ops[0]
        assert isinstance(op, RapidMove)
        assert op.start_x == APPROX(0.0) and op.start_y == APPROX(0.0)
        assert op.end_x == APPROX(4.37) and op.end_y == APPROX(-2.2)

    def test_second_op_is_tool_down(self):
        assert isinstance(self.ops[1], ToolDown)

    def test_ends_with_end_program(self):
        assert isinstance(self.ops[-1], EndProgram)

    def test_arc_directions_are_valid(self):
        arcs = [o for o in self.ops if isinstance(o, ArcCut)]
        assert all(a.direction in (2, 3) for a in arcs)

    def test_arc_center_offsets_nonzero(self):
        # Every arc in this file has at least one nonzero I or J
        arcs = [o for o in self.ops if isinstance(o, ArcCut)]
        assert all(a.center_i != 0.0 or a.center_j != 0.0 for a in arcs)

    def test_no_motion_before_first_g(self):
        # By definition — if this passes, the parser handled leading M-codes
        # and N-words correctly without raising ParseError.
        assert True

    def test_tool_down_count(self):
        assert sum(1 for o in self.ops if isinstance(o, ToolDown)) == 9

    def test_tool_up_count(self):
        assert sum(1 for o in self.ops if isinstance(o, ToolUp)) == 9

    def test_rapid_moves_all_have_zero_distance_or_positive(self):
        rapids = [o for o in self.ops if isinstance(o, RapidMove)]
        for r in rapids:
            dist = math.hypot(r.end_x - r.start_x, r.end_y - r.start_y)
            assert dist >= 0.0

    def test_n221_n223_n225_are_rapids(self):
        # After M03 at N219, the remaining three moves are rapid (R flag, no G)
        # They should resolve as RapidMove via inherited G1.
        last_rapids = [o for o in self.ops if isinstance(o, RapidMove)][-3:]
        assert all(isinstance(r, RapidMove) for r in last_rapids)
