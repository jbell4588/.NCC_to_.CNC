"""
ncc_converter.modal
~~~~~~~~~~~~~~~~~~~
Tracks the persistent (modal) state of the Burny controller as lines
are processed left-to-right.

Burny .NCC defaults (no header block in sample files):
  - Incremental coordinates (G91)
  - XY plane (G17)
  - Motion mode: undefined until the first G word
  - Tool: up (not cutting) until M04
  - Units: not declared — treated as opaque (millimetres in practice)

State is updated by feeding LexedLine objects one at a time via .apply().
The object is mutable; callers snapshot() it to freeze a copy if needed.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .lexer import LexedLine, TokKind

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Motion groups
# ---------------------------------------------------------------------------

#: G codes that set the active motion mode.
MOTION_G_CODES: frozenset[int] = frozenset({1, 2, 3})


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModalState:
    """
    Snapshot of the controller state at a point in the program.

    Positions are *absolute* (accumulated from incremental deltas).
    All lengths share the same unit as the source file.
    """

    # --- Motion ---
    motion_g: int | None = None    # 1, 2, or 3; None = not yet established

    # --- Tool ---
    tool_down: bool = False        # True after M04, False after M03

    # --- Position (absolute, accumulated) ---
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    # --- Feed ---
    feedrate: float | None = None  # None = using controller default

    # --- Program flow ---
    program_ended: bool = False    # True after M30

    # --- Diagnostics ---
    line_number: int | None = None     # most-recent Nxxx word value
    source_file_line: int | None = None  # 1-based line in source file

    def snapshot(self) -> "ModalState":
        """Return a frozen copy of the current state."""
        return copy(self)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply(self, ll: LexedLine) -> None:
        """
        Consume one lexed line and update state in place.

        Does NOT produce operations — that is the parser's job.
        """
        tok = {t.kind: t for t in ll.tokens}

        # Source tracking
        self.source_file_line = ll.source.file_line
        if TokKind.N_WORD in tok:
            self.line_number = int(tok[TokKind.N_WORD].value)  # type: ignore[arg-type]

        # Motion mode update (modal — persists until changed)
        if TokKind.G_WORD in tok:
            g = int(tok[TokKind.G_WORD].value)  # type: ignore[arg-type]
            if g in MOTION_G_CODES:
                self.motion_g = g
            # Non-motion G codes (e.g. G17, G20, G21, G90, G91) could be
            # handled here as the format is extended.

        # Feedrate
        if TokKind.F in tok:
            self.feedrate = float(tok[TokKind.F].value)  # type: ignore[arg-type]

        # Miscellaneous functions
        if TokKind.M_WORD in tok:
            m = int(tok[TokKind.M_WORD].value)  # type: ignore[arg-type]
            if m == 3:
                self.tool_down = False   # M03 = lift / tool up
            elif m == 4:
                self.tool_down = True    # M04 = pierce / tool down
            elif m == 30:
                self.program_ended = True

        # Position — apply incremental deltas
        if TokKind.X in tok:
            self.x += float(tok[TokKind.X].value)  # type: ignore[arg-type]
        if TokKind.Y in tok:
            self.y += float(tok[TokKind.Y].value)  # type: ignore[arg-type]
        if TokKind.Z in tok:
            self.z += float(tok[TokKind.Z].value)  # type: ignore[arg-type]
