"""
ncc_converter.parser
~~~~~~~~~~~~~~~~~~~~
Converts a sequence of LexedLines into a list of fully-resolved Operations.

Responsibilities
----------------
1. Expand implicit modal G codes — if a line has no G word, inherit the
   previously-seen motion G.
2. Resolve omitted coordinates — missing X / Y delta defaults to 0.
3. Resolve omitted arc offsets — missing I / J defaults to 0.
4. Convert incremental deltas into absolute start / end positions.
5. Classify each line into the correct Operation subtype.
6. Detect and report lines that are structurally invalid.

Non-responsibilities (handled elsewhere)
-----------------------------------------
- Geometric validation (arc radius consistency, contour continuity) → validator
- Emission / formatting → emitter
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .lexer import LexedLine, TokKind
from .modal import ModalState, MOTION_G_CODES
from .operations import (
    ArcCut,
    EndProgram,
    LinearCut,
    Operation,
    RapidMove,
    ToolDown,
    ToolUp,
)


# ---------------------------------------------------------------------------
# Errors and warnings
# ---------------------------------------------------------------------------

@dataclass
class ParseWarning:
    """Non-fatal issue noted during parsing."""
    file_line: int
    message: str

    def __str__(self) -> str:
        return f"Warning (line {self.file_line}): {self.message}"


class ParseError(ValueError):
    """Fatal structural error — parsing cannot continue."""

    def __init__(self, message: str, ll: LexedLine) -> None:
        super().__init__(f"Parse error (line {ll.source.file_line}): {message} — {ll.source.text!r}")
        self.lexed_line = ll


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tok_val(tok_map: dict[TokKind, float | int | None], kind: TokKind,
             default: float) -> float:
    """Return the numeric value for *kind* from *tok_map*, or *default*."""
    v = tok_map.get(kind)
    return float(v) if v is not None else default


def _has_motion_tokens(tok_map: dict) -> bool:
    """True if the line carries any coordinate / arc-offset tokens."""
    return any(k in tok_map for k in (
        TokKind.X, TokKind.Y, TokKind.Z,
        TokKind.I, TokKind.J, TokKind.K,
    ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    operations: list[Operation]
    warnings: list[ParseWarning]


def parse(lexed_lines: Sequence[LexedLine]) -> ParseResult:
    """
    Parse a full program into operations.

    Parameters
    ----------
    lexed_lines:
        Output of ``lexer.lex()``.

    Returns
    -------
    ParseResult
        ``.operations`` — ordered list of resolved operations.
        ``.warnings``   — non-fatal issues encountered.

    Raises
    ------
    ParseError
        On any structurally unresolvable line.
    """
    state = ModalState()
    operations: list[Operation] = []
    warnings: list[ParseWarning] = []

    for ll in lexed_lines:
        # Build a kind → value map for quick lookup.
        # Multiple tokens of the same kind would be overwritten (last wins);
        # that's an error in the source but we tolerate it silently.
        tok: dict[TokKind, float | int | None] = {
            t.kind: t.value for t in ll.tokens
        }
        has_rapid = any(t.kind == TokKind.RAPID for t in ll.tokens)

        # ------------------------------------------------------------------
        # 1. Determine effective motion G *before* mutating state
        # ------------------------------------------------------------------
        if TokKind.G_WORD in tok:
            g_raw = int(tok[TokKind.G_WORD])  # type: ignore[arg-type]
            if g_raw in MOTION_G_CODES:
                effective_g: int | None = g_raw
            else:
                # Non-motion G code (would be G17/G20/G90 etc.) — note it and
                # keep the existing modal motion G.
                warnings.append(ParseWarning(
                    file_line=ll.source.file_line,
                    message=f"Unrecognised / non-motion G{g_raw} ignored",
                ))
                effective_g = state.motion_g
        else:
            effective_g = state.motion_g   # inherit modal

        # ------------------------------------------------------------------
        # 2. Capture start position (before state mutates)
        # ------------------------------------------------------------------
        start_x = state.x
        start_y = state.y

        # ------------------------------------------------------------------
        # 3. Apply the line to modal state (updates position, tool, etc.)
        # ------------------------------------------------------------------
        state.apply(ll)

        # ------------------------------------------------------------------
        # 4. Emit M-code operations (independent of motion)
        # ------------------------------------------------------------------
        if TokKind.M_WORD in tok:
            m = int(tok[TokKind.M_WORD])  # type: ignore[arg-type]
            if m == 3:
                operations.append(ToolUp(source=ll.source))
            elif m == 4:
                operations.append(ToolDown(source=ll.source))
            elif m == 30:
                operations.append(EndProgram(source=ll.source))
            else:
                warnings.append(ParseWarning(
                    file_line=ll.source.file_line,
                    message=f"Unrecognised M{m} — ignored",
                ))

        # ------------------------------------------------------------------
        # 5. Emit motion operations
        # ------------------------------------------------------------------
        if not _has_motion_tokens(tok):
            # Pure M-code or N-word-only line — no motion to emit.
            continue

        if effective_g is None:
            raise ParseError(
                "Motion tokens present but no G code has been established yet",
                ll,
            )

        end_x = state.x
        end_y = state.y

        if effective_g == 1:
            if has_rapid:
                operations.append(RapidMove(
                    source=ll.source,
                    start_x=start_x,
                    start_y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                ))
            else:
                operations.append(LinearCut(
                    source=ll.source,
                    start_x=start_x,
                    start_y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                    feedrate=state.feedrate,
                ))

        elif effective_g in (2, 3):
            # Arc offsets default to 0 when absent.
            center_i = _tok_val(tok, TokKind.I, 0.0)
            center_j = _tok_val(tok, TokKind.J, 0.0)

            if center_i == 0.0 and center_j == 0.0:
                warnings.append(ParseWarning(
                    file_line=ll.source.file_line,
                    message="Arc has I=0 and J=0 — full-circle or degenerate arc",
                ))

            operations.append(ArcCut(
                source=ll.source,
                direction=effective_g,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                center_i=center_i,
                center_j=center_j,
                feedrate=state.feedrate,
            ))

        else:
            # Shouldn't be reachable given MOTION_G_CODES, but be safe.
            raise ParseError(f"Unexpected effective G code G{effective_g}", ll)

    return ParseResult(operations=operations, warnings=warnings)
