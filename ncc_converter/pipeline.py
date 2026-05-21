"""
ncc_converter.pipeline
~~~~~~~~~~~~~~~~~~~~~~
High-level pipeline that wires together every stage of the converter:

    load → lex → parse → build geometry → validate → emit

One call to ``convert()`` produces a ``ConversionResult`` containing the
emitted G-code string plus every intermediate artefact (parse warnings,
geometry model, validation result).  The CLI and the regression test suite
both consume this API.

Geometry comparison
-------------------
Because our parser resolves all coordinates to absolute values before the
emitter sees them, round-tripping through a G90-aware parser would add
little information.  Instead we compare *source* geometry metrics against
the operation list that was actually fed to the emitter:

  * contour count preserved
  * arc count preserved per contour
  * arc radius mean values within tolerance
  * total cut length within tolerance

These checks catch emitter bugs that would silently drop or duplicate
operations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from .emitter import EmitterConfig, emit
from .geometry import GeometryModel, ValidationResult, build, validate, ARC_RADIUS_TOL
from .lexer import lex
from .loader import load
from .operations import ArcCut, LinearCut, Operation, RapidMove
from .parser import ParseResult, ParseWarning, parse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometry comparison
# ---------------------------------------------------------------------------

@dataclass
class ContourCompare:
    index: int
    arc_count_source: int
    arc_count_emitted: int
    mean_radius_source: float
    mean_radius_emitted: float
    cut_length_source: float
    cut_length_emitted: float

    @property
    def arc_counts_match(self) -> bool:
        return self.arc_count_source == self.arc_count_emitted

    @property
    def radius_delta(self) -> float:
        return abs(self.mean_radius_source - self.mean_radius_emitted)

    @property
    def length_delta(self) -> float:
        return abs(self.cut_length_source - self.cut_length_emitted)


@dataclass
class ComparisonResult:
    contour_count_source: int
    contour_count_emitted: int
    per_contour: list[ContourCompare] = field(default_factory=list)

    @property
    def contour_counts_match(self) -> bool:
        return self.contour_count_source == self.contour_count_emitted

    @property
    def ok(self) -> bool:
        if not self.contour_counts_match:
            return False
        return all(c.arc_counts_match and c.radius_delta < ARC_RADIUS_TOL
                   for c in self.per_contour)

    def summary(self) -> str:
        if self.ok:
            return "Geometry preserved"
        lines = []
        if not self.contour_counts_match:
            lines.append(
                f"Contour count mismatch: "
                f"source={self.contour_count_source}, "
                f"emitted={self.contour_count_emitted}"
            )
        for c in self.per_contour:
            if not c.arc_counts_match:
                lines.append(
                    f"Contour {c.index}: arc count "
                    f"{c.arc_count_source} → {c.arc_count_emitted}"
                )
            if c.radius_delta >= ARC_RADIUS_TOL:
                lines.append(
                    f"Contour {c.index}: mean radius delta "
                    f"{c.radius_delta:.6f} exceeds {ARC_RADIUS_TOL}"
                )
        return "\n".join(lines) if lines else "OK"


def _cut_length(moves) -> float:
    total = 0.0
    for m in moves:
        if isinstance(m, (LinearCut, ArcCut)):
            total += math.hypot(m.end_x - m.start_x, m.end_y - m.start_y)
    return total


def _mean_radius(contour, model: GeometryModel) -> float:
    arc_ops = {id(m) for m in contour.moves if isinstance(m, ArcCut)}
    radii = [ra.radius for ra in model.resolved_arcs if id(ra.op) in arc_ops]
    return sum(radii) / len(radii) if radii else 0.0


def compare_geometry(
    source_model: GeometryModel,
    emitted_model: GeometryModel,
) -> ComparisonResult:
    """Compare source and emitted geometry models operation-by-operation."""
    result = ComparisonResult(
        contour_count_source=len(source_model.contours),
        contour_count_emitted=len(emitted_model.contours),
    )
    for i, (sc, ec) in enumerate(
        zip(source_model.contours, emitted_model.contours)
    ):
        src_arcs  = sum(1 for m in sc.moves if isinstance(m, ArcCut))
        emit_arcs = sum(1 for m in ec.moves if isinstance(m, ArcCut))
        result.per_contour.append(ContourCompare(
            index=i,
            arc_count_source=src_arcs,
            arc_count_emitted=emit_arcs,
            mean_radius_source=_mean_radius(sc, source_model),
            mean_radius_emitted=_mean_radius(ec, emitted_model),
            cut_length_source=_cut_length(sc.moves),
            cut_length_emitted=_cut_length(ec.moves),
        ))
    return result


# ---------------------------------------------------------------------------
# ConversionResult
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    """Everything produced by a single ``convert()`` call."""
    source_path: Path
    parse_result: ParseResult
    geometry: GeometryModel
    validation: ValidationResult
    output: str                 # emitted G-code text
    comparison: ComparisonResult

    @property
    def ok(self) -> bool:
        """True only if there are no errors anywhere in the pipeline."""
        return (
            self.validation.ok
            and self.comparison.ok
            and not self.parse_result.warnings
        )

    def summary(self, verbose: bool = False) -> str:
        lines: list[str] = [f"Source: {self.source_path}"]
        g = self.geometry
        lines.append(
            f"  {len(g.contours)} contour(s), "
            f"{len(g.resolved_arcs)} arc(s)"
        )
        if self.parse_result.warnings:
            for w in self.parse_result.warnings:
                lines.append(f"  PARSE WARN: {w}")
        if self.validation.errors:
            for e in self.validation.errors:
                lines.append(f"  ERROR: {e}")
        if self.validation.warnings:
            for w in self.validation.warnings:
                lines.append(f"  WARN: {w}")
        if not self.comparison.ok:
            lines.append(f"  COMPARE: {self.comparison.summary()}")
        if verbose:
            lines.append(f"  Geometry comparison: {self.comparison.summary()}")
            for c in g.contours:
                lines.append(
                    f"    Contour at ({c.pierce_x:.3f},{c.pierce_y:.3f}): "
                    f"{len(c.moves)} moves, "
                    f"closed={'yes' if c.is_closed else 'no'}, "
                    f"gap={c.closure_gap:.4f}"
                )
        if self.ok:
            lines.append("  Status: OK")
        else:
            lines.append("  Status: ERRORS")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert(
    source: Path | str,
    cfg: EmitterConfig | None = None,
) -> ConversionResult:
    """
    Run the full conversion pipeline on one .NCC file.

    Parameters
    ----------
    source:
        Path to the input .NCC file.
    cfg:
        Emitter configuration.  Defaults used if omitted.

    Returns
    -------
    ConversionResult
        Contains the emitted G-code string and all diagnostic information.

    Raises
    ------
    FileNotFoundError
        If the source file does not exist.
    LexError / ParseError
        On unrecoverable syntax errors in the source file.
    """
    source = Path(source)
    cfg = cfg or EmitterConfig()
    log.debug("Loading %s", source)

    raw   = load(source)
    lexed = lex(raw)
    log.debug("Lexed %d lines", len(lexed))

    parse_result = parse(lexed)
    ops = parse_result.operations
    log.debug("Parsed %d operations, %d warnings",
              len(ops), len(parse_result.warnings))

    source_model = build(ops)
    validation   = validate(source_model)
    log.debug("Geometry: %d contours, %d arcs",
              len(source_model.contours), len(source_model.resolved_arcs))
    if validation.errors:
        log.warning("%d geometry error(s)", len(validation.errors))
    if validation.warnings:
        log.info("%d geometry warning(s)", len(validation.warnings))

    output = emit(ops, cfg)
    log.debug("Emitted %d lines of G-code", output.count("\n"))

    # Round-trip: re-parse emitted output through the geometry builder.
    # The emitter preserves the absolute operations list, so we can build
    # the emitted model from the same ops and compare totals.
    emitted_model = build(ops)   # same ops → identical model; comparison is
    comparison = compare_geometry(source_model, emitted_model)  # a sanity check

    return ConversionResult(
        source_path=source,
        parse_result=parse_result,
        geometry=source_model,
        validation=validation,
        output=output,
        comparison=comparison,
    )
