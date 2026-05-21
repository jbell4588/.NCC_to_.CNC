"""
ncc_converter.geometry
~~~~~~~~~~~~~~~~~~~~~~
Internal geometry model and validation for a parsed operation list.

Two responsibilities:

1. **GeometryModel** — a structured view of the program as a list of
   Contours, each grouping the pierce, cutting moves, and lift for one
   feature.  Each ArcCut is augmented with its resolved centre and radius
   via ResolvedArc.

2. **validate()** — geometric sanity checks, returning a ValidationResult.

Checks performed
----------------
* Arc radius consistency — |r_start - r_end| must be within ARC_RADIUS_TOL.
  Burny stores coordinates to 2 decimal places (0.01 mm resolution), so
  quantisation errors up to ~0.01 mm are inherent in the format; the
  tolerance is set to 0.015 to give a small margin above that floor.
* Arc direction plausibility — G2/G3 is verified against the sign of the
  cross product (centre→start) × (centre→end).
* Within-contour contiguity — consecutive cutting moves inside one contour
  must connect end-to-end (always true by construction; kept as a defensive
  check).
* Contour closure — checks whether the cutting sequence (excluding any
  linear lead-in as the first move) forms a closed loop.  Reported as a
  warning, not an error, because open profiles are valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .operations import ArcCut, EndProgram, LinearCut, Operation, RapidMove, ToolDown, ToolUp


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

# Burny encodes coordinates to 2 d.p. (0.01 mm).  Arc radius errors up to
# one coordinate step are therefore expected and not indicative of corrupt
# data.  We use 1.5× that step as the hard error threshold.
ARC_RADIUS_TOL: float = 0.015   # mm (or source units)
CONTIGUITY_TOL: float = 1e-6    # max gap between consecutive moves
CLOSURE_TOL:    float = 0.05    # max closure gap to count as "closed"


# ---------------------------------------------------------------------------
# ResolvedArc
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResolvedArc:
    """An ArcCut with its absolute centre and radius computed."""
    op: ArcCut
    center_x: float
    center_y: float
    radius: float           # mean of r_start and r_end
    radius_error: float     # |r_start - r_end|
    start_angle: float      # radians from centre
    end_angle: float        # radians from centre

    @classmethod
    def from_arc(cls, arc: ArcCut) -> "ResolvedArc":
        cx = arc.start_x + arc.center_i
        cy = arc.start_y + arc.center_j
        r_start = math.hypot(arc.start_x - cx, arc.start_y - cy)
        r_end   = math.hypot(arc.end_x   - cx, arc.end_y   - cy)
        return cls(
            op=arc,
            center_x=cx,
            center_y=cy,
            radius=(r_start + r_end) / 2.0,
            radius_error=abs(r_start - r_end),
            start_angle=math.atan2(arc.start_y - cy, arc.start_x - cx),
            end_angle  =math.atan2(arc.end_y   - cy, arc.end_x   - cx),
        )


# ---------------------------------------------------------------------------
# Contour
# ---------------------------------------------------------------------------

CuttingMove = LinearCut | ArcCut


@dataclass
class Contour:
    """
    One cutting feature: everything between a ToolDown and the next ToolUp.

    ``pierce_x / pierce_y`` — absolute position where M04 fired.
    ``moves``  — ordered list of LinearCut / ArcCut operations.
    ``lift``   — the closing ToolUp, or None if the program ends without one.

    Closure is measured from the start of the *cutting sequence*, defined
    as the first move after any initial linear lead-in.  This avoids false
    open-contour warnings on pierce-and-lead-in patterns.
    """
    pierce_x: float
    pierce_y: float
    pierce_op: ToolDown
    moves: list[CuttingMove] = field(default_factory=list)
    lift: ToolUp | None = None

    # ------------------------------------------------------------------
    # Closure helpers
    # ------------------------------------------------------------------

    def _cutting_origin(self) -> tuple[float, float]:
        """
        Start of the actual cutting loop, skipping a linear lead-in if present.

        A lead-in is the first move when it is a LinearCut *and* the moves
        that follow it are arc cuts (the circular profile proper).  If all
        moves are linear we measure closure from the very first move's start.
        """
        if (len(self.moves) > 1
                and isinstance(self.moves[0], LinearCut)
                and any(isinstance(m, ArcCut) for m in self.moves[1:])):
            return (self.moves[1].start_x, self.moves[1].start_y)
        if self.moves:
            return (self.moves[0].start_x, self.moves[0].start_y)
        return (self.pierce_x, self.pierce_y)

    @property
    def closure_gap(self) -> float:
        """Distance between the cutting loop end and its start."""
        if not self.moves:
            return 0.0
        sx, sy = self._cutting_origin()
        last = self.moves[-1]
        return math.hypot(last.end_x - sx, last.end_y - sy)

    @property
    def is_closed(self) -> bool:
        return len(self.moves) >= 2 and self.closure_gap <= CLOSURE_TOL


# ---------------------------------------------------------------------------
# GeometryModel
# ---------------------------------------------------------------------------

@dataclass
class GeometryModel:
    contours: list[Contour]
    resolved_arcs: list[ResolvedArc]   # one per ArcCut, in program order


def build(operations: Sequence[Operation]) -> GeometryModel:
    """Build a GeometryModel from a parsed operation list."""
    contours: list[Contour] = []
    resolved_arcs: list[ResolvedArc] = []

    current_x = 0.0
    current_y = 0.0
    current_contour: Contour | None = None

    for op in operations:
        if isinstance(op, RapidMove):
            current_x = op.end_x
            current_y = op.end_y

        elif isinstance(op, LinearCut):
            current_x = op.end_x
            current_y = op.end_y
            if current_contour is not None:
                current_contour.moves.append(op)

        elif isinstance(op, ArcCut):
            current_x = op.end_x
            current_y = op.end_y
            resolved_arcs.append(ResolvedArc.from_arc(op))
            if current_contour is not None:
                current_contour.moves.append(op)

        elif isinstance(op, ToolDown):
            current_contour = Contour(
                pierce_x=current_x,
                pierce_y=current_y,
                pierce_op=op,
            )
            contours.append(current_contour)

        elif isinstance(op, ToolUp):
            if current_contour is not None:
                current_contour.lift = op
            current_contour = None

        elif isinstance(op, EndProgram):
            current_contour = None

    return GeometryModel(contours=contours, resolved_arcs=resolved_arcs)


# ---------------------------------------------------------------------------
# Validation issue types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ArcRadiusError:
    file_line: int
    start_radius: float
    end_radius: float
    tolerance: float = ARC_RADIUS_TOL

    def __str__(self) -> str:
        return (
            f"Line {self.file_line}: arc radius mismatch "
            f"(r_start={self.start_radius:.6f}, r_end={self.end_radius:.6f}, "
            f"tol={self.tolerance})"
        )


@dataclass(frozen=True, slots=True)
class ArcDirectionWarning:
    file_line: int
    declared_direction: int
    implied_direction: int

    def __str__(self) -> str:
        label = {2: "CW (G2)", 3: "CCW (G3)"}
        return (
            f"Line {self.file_line}: arc declared as "
            f"{label[self.declared_direction]} but geometry implies "
            f"{label[self.implied_direction]}"
        )


@dataclass(frozen=True, slots=True)
class ContiguityError:
    prev_file_line: int
    next_file_line: int
    gap: float

    def __str__(self) -> str:
        return (
            f"Contiguity break between lines "
            f"{self.prev_file_line} and {self.next_file_line}: gap={self.gap:.6f}"
        )


@dataclass(frozen=True, slots=True)
class ContourClosureWarning:
    file_line: int    # the ToolDown source line
    gap: float

    def __str__(self) -> str:
        return (
            f"Open contour at pierce line {self.file_line}: "
            f"closure gap={self.gap:.4f}"
        )


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    errors:   list[ArcRadiusError | ContiguityError]              = field(default_factory=list)
    warnings: list[ArcDirectionWarning | ContourClosureWarning]   = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"ERROR: {e}" for e in self.errors]
        lines += [f"WARN:  {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "OK"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cross_z(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _arc_implied_direction(ra: ResolvedArc) -> int:
    vs_x = ra.op.start_x - ra.center_x
    vs_y = ra.op.start_y - ra.center_y
    ve_x = ra.op.end_x   - ra.center_x
    ve_y = ra.op.end_y   - ra.center_y
    cross = _cross_z(vs_x, vs_y, ve_x, ve_y)
    if math.isclose(cross, 0.0, abs_tol=1e-9):
        return ra.op.direction   # full circle — indeterminate
    return 3 if cross > 0 else 2


# ---------------------------------------------------------------------------
# Public validation API
# ---------------------------------------------------------------------------

def validate(model: GeometryModel) -> ValidationResult:
    """Run all geometric checks and return a ValidationResult."""
    result = ValidationResult()

    # Arc checks
    for ra in model.resolved_arcs:
        r_start = math.hypot(ra.op.start_x - ra.center_x, ra.op.start_y - ra.center_y)
        r_end   = math.hypot(ra.op.end_x   - ra.center_x, ra.op.end_y   - ra.center_y)

        if ra.radius_error > ARC_RADIUS_TOL:
            result.errors.append(ArcRadiusError(
                file_line=ra.op.source.file_line,
                start_radius=r_start,
                end_radius=r_end,
            ))

        implied = _arc_implied_direction(ra)
        if implied != ra.op.direction:
            result.warnings.append(ArcDirectionWarning(
                file_line=ra.op.source.file_line,
                declared_direction=ra.op.direction,
                implied_direction=implied,
            ))

    # Per-contour checks
    for contour in model.contours:
        # Contiguity within contour
        for prev, curr in zip(contour.moves, contour.moves[1:]):
            gap = math.hypot(curr.start_x - prev.end_x, curr.start_y - prev.end_y)
            if gap > CONTIGUITY_TOL:
                result.errors.append(ContiguityError(
                    prev_file_line=prev.source.file_line,
                    next_file_line=curr.source.file_line,
                    gap=gap,
                ))

        # Contour closure (warning only)
        if contour.moves and not contour.is_closed:
            result.warnings.append(ContourClosureWarning(
                file_line=contour.pierce_op.source.file_line,
                gap=contour.closure_gap,
            ))

    return result
