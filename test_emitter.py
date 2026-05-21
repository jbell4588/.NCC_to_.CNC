"""
Tests for the G-code emitter.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from ncc_converter.loader import load, RawLine
from ncc_converter.lexer import lex
from ncc_converter.parser import parse
from ncc_converter.emitter import Emitter, EmitterConfig, emit
from ncc_converter.operations import (
    ArcCut, EndProgram, LinearCut, RapidMove, ToolDown, ToolUp,
)

SAMPLE = Path(__file__).parent / "samples" / "good" / "4HDNOAA.NCC"
_D = RawLine(file_line=0, text="")


def _rapid(sx, sy, ex, ey) -> RapidMove:
    return RapidMove(source=_D, start_x=sx, start_y=sy, end_x=ex, end_y=ey)

def _linear(sx, sy, ex, ey, f=None) -> LinearCut:
    return LinearCut(source=_D, start_x=sx, start_y=sy,
                     end_x=ex, end_y=ey, feedrate=f)

def _arc(direction, sx, sy, ex, ey, ci, cj, f=None) -> ArcCut:
    return ArcCut(source=_D, direction=direction,
                  start_x=sx, start_y=sy, end_x=ex, end_y=ey,
                  center_i=ci, center_j=cj, feedrate=f)

def sample_ops():
    return parse(lex(load(SAMPLE))).operations


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

class TestEmitterStructure:
    def test_starts_with_percent(self):
        assert emit([EndProgram(source=_D)]).startswith("%\n")

    def test_ends_with_percent(self):
        assert emit([EndProgram(source=_D)]).strip().endswith("%")

    def test_header_comment_included(self):
        cfg = EmitterConfig(header_comment="test run")
        assert "(test run)" in emit([EndProgram(source=_D)], cfg)

    def test_g90_in_preamble(self):
        assert "G90" in emit([EndProgram(source=_D)])

    def test_g21_by_default(self):
        assert "G21" in emit([EndProgram(source=_D)])

    def test_g20_when_configured(self):
        cfg = EmitterConfig(units_code="G20")
        assert "G20" in emit([EndProgram(source=_D)], cfg)

    def test_m30_present(self):
        assert "M30" in emit([EndProgram(source=_D)])


# ---------------------------------------------------------------------------
# Rapids
# ---------------------------------------------------------------------------

class TestEmitterRapid:
    def test_g0_by_default(self):
        assert "G0" in emit([_rapid(0, 0, 1.5, -2.0)])

    def test_absolute_coordinates(self):
        out = emit([_rapid(0, 0, 1.5, -2.25)])
        assert "X1.5000" in out
        assert "Y-2.2500" in out

    def test_rapid_as_g1_when_configured(self):
        cfg = EmitterConfig(rapid_as_g0=False, rapid_feedrate=5000.0)
        out = emit([_rapid(0, 0, 1.0, 0.0)], cfg)
        assert "G1" in out
        assert "G0" not in out.split("G90")[1]
        assert "F5000" in out


# ---------------------------------------------------------------------------
# Linear cuts
# ---------------------------------------------------------------------------

class TestEmitterLinear:
    def test_emits_g1(self):
        assert "G1" in emit([_linear(0, 0, 1.0, 2.0, f=300.0)])

    def test_feedrate_emitted(self):
        assert "F300.0000" in emit([_linear(0, 0, 1.0, 2.0, f=300.0)])

    def test_no_feedrate_when_none(self):
        out = emit([_linear(0, 0, 1.0, 2.0, f=None)])
        body = out.split("G90")[1].split("M30")[0]
        assert "F" not in body


# ---------------------------------------------------------------------------
# Arc cuts
# ---------------------------------------------------------------------------

class TestEmitterArc:
    def test_g2_for_cw(self):
        assert "G2" in emit([_arc(2, 0, 1, 1, 0, 0.0, -1.0)])

    def test_g3_for_ccw(self):
        assert "G3" in emit([_arc(3, 1, 0, 0, 1, -1.0, 0.0)])

    def test_ij_emitted(self):
        out = emit([_arc(3, 1, 0, 0, 1, -1.0, 0.5)])
        assert "I-1.0000" in out
        assert "J0.5000" in out

    def test_zero_i_suppressed_when_j_nonzero(self):
        out = emit([_arc(3, 0, 0, 1, 1, 0.0, 1.0)])
        body = out.split("G90")[1]
        assert "I" not in body

    def test_zero_j_suppressed_when_i_nonzero(self):
        out = emit([_arc(3, 0, 0, 1, 1, 1.0, 0.0)])
        body = out.split("G90")[1]
        assert "J" not in body

    def test_both_zero_ij_explicit(self):
        # Full circle / degenerate — must emit I and J
        out = emit([_arc(3, 0, 0, 0, 0, 0.0, 0.0)])
        assert "I0.0000" in out
        assert "J0.0000" in out


# ---------------------------------------------------------------------------
# Tool codes
# ---------------------------------------------------------------------------

class TestEmitterToolCodes:
    def test_tool_down_default(self):
        assert "M03" in emit([ToolDown(source=_D)])

    def test_tool_up_default(self):
        assert "M05" in emit([ToolUp(source=_D)])

    def test_tool_codes_configurable(self):
        cfg = EmitterConfig(tool_down_code="M07", tool_up_code="M09")
        out = emit([ToolDown(source=_D), ToolUp(source=_D)], cfg)
        assert "M07" in out
        assert "M09" in out


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------

class TestEmitterLineNumbers:
    def test_line_numbers_present_by_default(self):
        out = emit([_rapid(0, 0, 1, 1)])
        body = out.split("G90")[1].split("M30")[0]
        assert any(l.startswith("N") for l in body.splitlines())

    def test_line_numbers_suppressed(self):
        cfg = EmitterConfig(emit_line_numbers=False)
        out = emit([_rapid(0, 0, 1, 1)], cfg)
        body = out.split("G90")[1].split("M30")[0]
        assert not any(l.startswith("N") for l in body.splitlines() if l.strip())

    def test_line_number_format(self):
        # Default start=10, step=10 → first body line after preamble is N0020
        out = emit([_rapid(0, 0, 1, 1)])
        assert "N0010" in out or "N0020" in out   # preamble or first body

    def test_custom_step(self):
        cfg = EmitterConfig(line_number_start=5, line_number_step=5)
        out = emit([_rapid(0, 0, 1, 1), _linear(1, 1, 2, 2)], cfg)
        n_lines = [l for l in out.splitlines() if l.startswith("N")]
        # Should have at least preamble + 2 body + M30
        assert len(n_lines) >= 4


# ---------------------------------------------------------------------------
# Precision
# ---------------------------------------------------------------------------

class TestEmitterPrecision:
    def test_default_4dp(self):
        assert "X1.1235" in emit([_rapid(0, 0, 1.123456, 0.0)])

    def test_custom_precision(self):
        cfg = EmitterConfig(precision=2)
        assert "X1.12" in emit([_rapid(0, 0, 1.123456, 0.0)], cfg)


# ---------------------------------------------------------------------------
# Full file integration
# ---------------------------------------------------------------------------

class TestEmitterFullFile:
    def setup_method(self):
        self.ops = sample_ops()
        self.out = emit(self.ops)
        self.lines = self.out.splitlines()

    def test_output_nonempty(self):
        assert len(self.lines) > 5

    def test_delimiters(self):
        assert self.lines[0] == "%"
        assert self.lines[-1] == "%"

    def test_contains_g90(self):
        assert any("G90" in l for l in self.lines)

    def test_contains_m30(self):
        assert any("M30" in l for l in self.lines)

    def test_no_burny_rapid_flag(self):
        # No line should end with bare R (that's the Burny format)
        assert not any(l.rstrip().endswith("R") for l in self.lines)

    def test_no_incremental_mode(self):
        assert not any("G91" in l for l in self.lines)

    def test_contains_arcs(self):
        assert any("G2" in l or "G3" in l for l in self.lines)

    def test_all_body_lines_have_valid_start(self):
        body = [l for l in self.lines
                if l and l != "%" and not l.startswith("(")]
        for line in body:
            assert line[0] in "NGM", f"Unexpected: {line!r}"

    def test_line_count_reasonable(self):
        # 111 source lines → roughly similar number of output lines
        assert 100 < len(self.lines) < 300
