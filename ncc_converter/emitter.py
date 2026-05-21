"""
ncc_converter.emitter
~~~~~~~~~~~~~~~~~~~~~
Converts a list of Operations into standard G-code text.

The emitter produces *absolute* (G90), explicit G-code — the opposite of
the compressed incremental Burny format.  Output is suitable for loading
in NC Viewer, CAMotics, or any standard G-code controller.

Key differences from Burny .NCC
--------------------------------
* G90  — absolute coordinates (Burny uses implicit G91 / incremental)
* G0   — explicit rapid traverse (Burny uses G1 + trailing R)
* M03  — tool / spindle on  (Burny M04 = pierce; conventions are inverted)
* M05  — tool / spindle off (Burny M03 = lift)
* All coordinates written to configurable decimal precision
* I/J arc offsets: zero component suppressed when the other is non-zero
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .operations import (
    ArcCut, EndProgram, LinearCut, Operation, RapidMove, ToolDown, ToolUp,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EmitterConfig:
    """All knobs for controlling emitter output."""

    # Preamble
    header_comment: str | None = None   # written as (comment) after %
    units_code: str = "G21"             # G21 = mm, G20 = inch

    # Rapids
    rapid_as_g0: bool = True            # False → emit G1 + rapid_feedrate
    rapid_feedrate: float | None = None # used only when rapid_as_g0=False

    # Tool codes  (standard convention, not Burny's inverted convention)
    tool_down_code: str = "M03"
    tool_up_code:   str = "M05"

    # Line numbers
    emit_line_numbers: bool = True
    line_number_start: int = 10
    line_number_step:  int = 10

    # Numeric precision
    precision: int = 4                  # decimal places for coordinates


# ---------------------------------------------------------------------------
# Emitter internals
# ---------------------------------------------------------------------------

class Emitter:
    """Stateful emitter; accumulates output lines."""

    def __init__(self, cfg: EmitterConfig | None = None) -> None:
        self.cfg = cfg or EmitterConfig()
        self._lines: list[str] = []
        self._n = self.cfg.line_number_start    # next line-number counter
        self._last_feedrate: float | None = None
        self._last_motion: str | None = None    # for modal suppression

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def emit_all(self, operations: Sequence[Operation]) -> str:
        self._emit_header()
        self._emit_preamble()
        for op in operations:
            self._emit_op(op)
        self._emit_footer()
        return "\n".join(self._lines)

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------

    def _emit_header(self) -> None:
        self._lines.append("%")
        if self.cfg.header_comment:
            self._lines.append(f"({self.cfg.header_comment})")

    def _emit_preamble(self) -> None:
        self._nl(f"G90{self.cfg.units_code}")

    def _emit_footer(self) -> None:
        self._nl("M30")
        self._lines.append("%")

    # ------------------------------------------------------------------
    # Operation dispatch
    # ------------------------------------------------------------------

    def _emit_op(self, op: Operation) -> None:
        if isinstance(op, RapidMove):
            self._emit_rapid(op)
        elif isinstance(op, LinearCut):
            self._emit_linear(op)
        elif isinstance(op, ArcCut):
            self._emit_arc(op)
        elif isinstance(op, ToolDown):
            self._nl(self.cfg.tool_down_code)
        elif isinstance(op, ToolUp):
            self._nl(self.cfg.tool_up_code)
        elif isinstance(op, EndProgram):
            pass  # M30 is written in _emit_footer

    # ------------------------------------------------------------------
    # Motion words
    # ------------------------------------------------------------------

    def _emit_rapid(self, op: RapidMove) -> None:
        cfg = self.cfg
        if cfg.rapid_as_g0:
            body = f"G0{self._xy(op.end_x, op.end_y)}"
        else:
            f_part = f"F{self._num(cfg.rapid_feedrate)}" if cfg.rapid_feedrate else ""
            body = f"G1{self._xy(op.end_x, op.end_y)}{f_part}"
        self._nl(body)

    def _emit_linear(self, op: LinearCut) -> None:
        f_part = f"F{self._num(op.feedrate)}" if op.feedrate is not None else ""
        self._nl(f"G1{self._xy(op.end_x, op.end_y)}{f_part}")

    def _emit_arc(self, op: ArcCut) -> None:
        g = "G2" if op.direction == 2 else "G3"
        ij = self._ij(op.center_i, op.center_j)
        f_part = f"F{self._num(op.feedrate)}" if op.feedrate is not None else ""
        self._nl(f"{g}{self._xy(op.end_x, op.end_y)}{ij}{f_part}")

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _num(self, v: float | None) -> str:
        if v is None:
            return ""
        return f"{v:.{self.cfg.precision}f}"

    def _xy(self, x: float, y: float) -> str:
        return f"X{self._num(x)}Y{self._num(y)}"

    def _ij(self, i: float, j: float) -> str:
        """
        Emit I and/or J.  Suppress a zero component when the other is
        non-zero (matches common CAM output style).  When both are zero
        (full-circle / degenerate) emit both explicitly.
        """
        i_zero = abs(i) < 10 ** (-self.cfg.precision - 1)
        j_zero = abs(j) < 10 ** (-self.cfg.precision - 1)
        if i_zero and j_zero:
            return f"I{self._num(0.0)}J{self._num(0.0)}"
        parts = ""
        if not i_zero:
            parts += f"I{self._num(i)}"
        if not j_zero:
            parts += f"J{self._num(j)}"
        return parts

    # ------------------------------------------------------------------
    # Line-number management
    # ------------------------------------------------------------------

    def _nl(self, body: str) -> None:
        """Append one numbered (or bare) body line."""
        if self.cfg.emit_line_numbers:
            line = f"N{self._n:04d}{body}"
            self._n += self.cfg.line_number_step
        else:
            line = body
        self._lines.append(line)


# ---------------------------------------------------------------------------
# Public convenience function
# ---------------------------------------------------------------------------

def emit(operations: Sequence[Operation],
         cfg: EmitterConfig | None = None) -> str:
    """
    Convert a list of Operations to a G-code string.

    Parameters
    ----------
    operations:
        Output of ``parser.parse().operations``.
    cfg:
        Optional EmitterConfig; defaults are used if omitted.

    Returns
    -------
    str
        Complete G-code program, ready to save as a .nc / .gcode file.
    """
    return Emitter(cfg).emit_all(operations)
