"""
ncc_converter.operations
~~~~~~~~~~~~~~~~~~~~~~~~
Typed, immutable dataclasses for every operation the parser can emit.

Each operation carries:
  - ``source``  — the original RawLine for error reporting / round-tripping
  - geometry / state fields specific to that operation type

All positions are *absolute* (accumulated from incremental source deltas).
Arc offsets (center_i, center_j) are relative to the arc start point,
matching standard G-code convention.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import RawLine


# ---------------------------------------------------------------------------
# Motion operations
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RapidMove:
    """G1 + trailing R — rapid traverse (tool up, no cutting)."""
    source: RawLine
    start_x: float
    start_y: float
    end_x: float
    end_y: float


@dataclass(frozen=True, slots=True)
class LinearCut:
    """G1 without R — linear cutting move (tool down)."""
    source: RawLine
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    feedrate: float | None


@dataclass(frozen=True, slots=True)
class ArcCut:
    """G2 (CW) or G3 (CCW) arc cutting move (tool down).

    center_i / center_j are the I / J offsets from the arc *start* to the
    arc centre.  Missing I or J in the source defaults to 0.0.
    """
    source: RawLine
    direction: int          # 2 = clockwise, 3 = counter-clockwise
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    center_i: float         # offset: start_x + center_i = center_x
    center_j: float         # offset: start_y + center_j = center_y
    feedrate: float | None


# ---------------------------------------------------------------------------
# Tool / miscellaneous operations
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolUp:
    """M03 — lift the tool / end of cut."""
    source: RawLine


@dataclass(frozen=True, slots=True)
class ToolDown:
    """M04 — pierce / lower the tool."""
    source: RawLine


@dataclass(frozen=True, slots=True)
class EndProgram:
    """M30 — end of program."""
    source: RawLine


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

Operation = RapidMove | LinearCut | ArcCut | ToolUp | ToolDown | EndProgram
