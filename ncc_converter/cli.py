"""
ncc_converter.cli
~~~~~~~~~~~~~~~~~
Command-line interface for the NCC converter.

Sub-commands
------------
ncc convert  <input.NCC> [output.nc]   Convert one file
ncc validate <input.NCC>               Validate without emitting
ncc batch    <input_dir> <output_dir>  Batch-convert a directory

Run ``ncc --help`` or ``ncc <subcommand> --help`` for full option docs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .emitter import EmitterConfig
from .pipeline import convert

log = logging.getLogger("ncc_converter")


# ---------------------------------------------------------------------------
# Shared emitter option builder
# ---------------------------------------------------------------------------

def _add_emitter_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("output formatting")
    g.add_argument(
        "--units", choices=["mm", "inch"], default="mm",
        help="Output units (default: mm)",
    )
    g.add_argument(
        "--precision", type=int, default=4, metavar="N",
        help="Decimal places for coordinates (default: 4)",
    )
    g.add_argument(
        "--no-line-numbers", action="store_true",
        help="Suppress N-word line numbers in output",
    )
    g.add_argument(
        "--line-start", type=int, default=10, metavar="N",
        help="First line number (default: 10)",
    )
    g.add_argument(
        "--line-step", type=int, default=10, metavar="N",
        help="Line number increment (default: 10)",
    )
    g.add_argument(
        "--rapid-as-g1", action="store_true",
        help="Emit G1+feedrate for rapids instead of G0",
    )
    g.add_argument(
        "--rapid-feedrate", type=float, default=None, metavar="F",
        help="Feedrate to use when --rapid-as-g1 is set",
    )
    g.add_argument(
        "--tool-down", default="M03", metavar="CODE",
        help="M-code for tool down / pierce (default: M03)",
    )
    g.add_argument(
        "--tool-up", default="M05", metavar="CODE",
        help="M-code for tool up / lift (default: M05)",
    )
    g.add_argument(
        "--comment", default=None, metavar="TEXT",
        help="Header comment inserted after the opening %%",
    )


def _cfg_from_args(args: argparse.Namespace) -> EmitterConfig:
    return EmitterConfig(
        units_code="G21" if args.units == "mm" else "G20",
        precision=args.precision,
        emit_line_numbers=not args.no_line_numbers,
        line_number_start=args.line_start,
        line_number_step=args.line_step,
        rapid_as_g0=not args.rapid_as_g1,
        rapid_feedrate=args.rapid_feedrate,
        tool_down_code=args.tool_down,
        tool_up_code=args.tool_up,
        header_comment=args.comment,
    )


# ---------------------------------------------------------------------------
# Sub-command: convert
# ---------------------------------------------------------------------------

def _cmd_convert(args: argparse.Namespace) -> int:
    src = Path(args.input)
    cfg = _cfg_from_args(args)

    # Determine output path
    if args.output:
        dst = Path(args.output)
    else:
        dst = src.with_suffix(".nc")

    try:
        result = convert(src, cfg)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:        # LexError, ParseError
        log.error("Failed to convert %s: %s", src, exc)
        return 1

    if args.verbose:
        print(result.summary(verbose=True))
    else:
        print(result.summary())

    if result.validation.errors and not args.force:
        log.error("Conversion aborted due to geometry errors. Use --force to emit anyway.")
        return 1

    dst.write_text(result.output, encoding="utf-8")
    print(f"Written: {dst}  ({len(result.output.splitlines())} lines)")
    return 0


# ---------------------------------------------------------------------------
# Sub-command: validate
# ---------------------------------------------------------------------------

def _cmd_validate(args: argparse.Namespace) -> int:
    src = Path(args.input)
    cfg = EmitterConfig()   # default config — validate only cares about parsing

    try:
        result = convert(src, cfg)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("Failed to parse %s: %s", src, exc)
        return 1

    print(result.summary(verbose=args.verbose))
    return 0 if result.validation.ok else 1


# ---------------------------------------------------------------------------
# Sub-command: batch
# ---------------------------------------------------------------------------

def _cmd_batch(args: argparse.Namespace) -> int:
    src_dir = Path(args.input_dir)
    dst_dir = Path(args.output_dir)
    cfg = _cfg_from_args(args)

    if not src_dir.is_dir():
        log.error("Not a directory: %s", src_dir)
        return 1
    dst_dir.mkdir(parents=True, exist_ok=True)

    pattern = args.pattern or "*.NCC"
    files = sorted(src_dir.glob(pattern))
    if not files:
        log.warning("No files matching %r in %s", pattern, src_dir)
        return 0

    ok_count = err_count = 0
    for src in files:
        dst = dst_dir / src.with_suffix(".nc").name
        try:
            result = convert(src, cfg)
        except Exception as exc:
            log.error("FAIL  %s: %s", src.name, exc)
            err_count += 1
            continue

        has_errors = bool(result.validation.errors)
        if has_errors and not args.force:
            log.error("SKIP  %s (geometry errors; use --force to emit)", src.name)
            err_count += 1
            continue

        dst.write_text(result.output, encoding="utf-8")
        status = "WARN " if result.validation.warnings else "OK   "
        print(f"{status} {src.name} → {dst.name}")
        if args.verbose:
            print(result.summary(verbose=True))
        ok_count += 1

    print(f"\n{ok_count} converted, {err_count} failed.")
    return 0 if err_count == 0 else 1


# ---------------------------------------------------------------------------
# Argument parser construction
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="ncc",
        description="Convert Burny .NCC plasma-cutter G-code to standard G-code.",
    )
    root.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose output",
    )
    root.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging",
    )
    subs = root.add_subparsers(dest="command", metavar="COMMAND")
    subs.required = True

    # -- convert --
    p_conv = subs.add_parser("convert", help="Convert a single .NCC file")
    p_conv.add_argument("input", help="Source .NCC file")
    p_conv.add_argument("output", nargs="?", help="Output .nc file (default: same name)")
    p_conv.add_argument("--force", action="store_true",
                        help="Emit output even if geometry errors are found")
    _add_emitter_options(p_conv)

    # -- validate --
    p_val = subs.add_parser("validate", help="Validate without emitting output")
    p_val.add_argument("input", help="Source .NCC file")
    p_val.add_argument("-v", "--verbose", action="store_true",
                       help="Show per-contour details")

    # -- batch --
    p_bat = subs.add_parser("batch", help="Batch-convert a directory of .NCC files")
    p_bat.add_argument("input_dir",  help="Directory containing .NCC files")
    p_bat.add_argument("output_dir", help="Directory for converted .nc files")
    p_bat.add_argument(
        "--pattern", default="*.NCC", metavar="GLOB",
        help="File glob pattern (default: *.NCC)",
    )
    p_bat.add_argument("--force", action="store_true",
                       help="Emit output even if geometry errors are found")
    _add_emitter_options(p_bat)

    return root


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Configure logging
    level = logging.DEBUG if args.debug else (
        logging.INFO if args.verbose else logging.WARNING
    )
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)

    dispatch = {
        "convert":  _cmd_convert,
        "validate": _cmd_validate,
        "batch":    _cmd_batch,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
