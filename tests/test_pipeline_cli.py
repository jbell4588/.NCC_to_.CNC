"""
Tests for the pipeline, CLI, and regression suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ncc_converter.emitter import EmitterConfig
from ncc_converter.geometry import ArcRadiusError, ContourClosureWarning
from ncc_converter.lexer import LexError
from ncc_converter.parser import ParseError
from ncc_converter.pipeline import ComparisonResult, ConversionResult, convert

SAMPLES_GOOD = Path(__file__).parent / "samples" / "good"
SAMPLES_BAD  = Path(__file__).parent / "samples" / "bad"

SAMPLE        = SAMPLES_GOOD / "4HDNOAA.NCC"
BAD_RADIUS    = SAMPLES_BAD  / "bad_arc_radius.NCC"
BAD_NO_G      = SAMPLES_BAD  / "bad_no_modal_g.NCC"
QUANTIZED_ARC = SAMPLES_BAD  / "quantized_arc_edge.NCC"


# ---------------------------------------------------------------------------
# Pipeline — good file
# ---------------------------------------------------------------------------

class TestPipelineGoodFile:
    def setup_method(self):
        self.result = convert(SAMPLE)

    def test_returns_conversion_result(self):
        assert isinstance(self.result, ConversionResult)

    def test_source_path_set(self):
        assert self.result.source_path == SAMPLE

    def test_output_is_string(self):
        assert isinstance(self.result.output, str)

    def test_output_nonempty(self):
        assert len(self.result.output) > 100

    def test_no_parse_warnings(self):
        assert self.result.parse_result.warnings == []

    def test_no_geometry_errors(self):
        assert self.result.validation.errors == [], str(self.result.validation)

    def test_geometry_warnings_only_open_profile(self):
        # Only the outer profile contour should be open
        from ncc_converter.geometry import ContourClosureWarning
        cw = [w for w in self.result.validation.warnings
              if isinstance(w, ContourClosureWarning)]
        assert len(cw) == 1

    def test_comparison_ok(self):
        assert self.result.comparison.ok

    def test_ok_property(self):
        # ok = no errors (one open-profile warning is acceptable)
        assert self.result.validation.ok   # no errors even if warnings

    def test_nine_contours(self):
        assert len(self.result.geometry.contours) == 9

    def test_summary_contains_source_name(self):
        assert "4HDNOAA" in self.result.summary()

    def test_verbose_summary_contains_contour_details(self):
        s = self.result.summary(verbose=True)
        assert "Contour at" in s

    def test_output_starts_and_ends_with_percent(self):
        lines = self.result.output.splitlines()
        assert lines[0] == "%" and lines[-1] == "%"

    def test_custom_cfg_propagates(self):
        cfg = EmitterConfig(precision=2, header_comment="HELLO")
        r = convert(SAMPLE, cfg)
        assert "(HELLO)" in r.output
        # 2 d.p. precision — coordinates have .XX format
        assert "X4.37" in r.output   # 4.37 rounds to 4.37 at 2 d.p. too

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            convert(SAMPLES_GOOD / "nonexistent.NCC")


# ---------------------------------------------------------------------------
# Pipeline — bad files (regression)
# ---------------------------------------------------------------------------

class TestPipelineBadRadius:
    def setup_method(self):
        self.result = convert(BAD_RADIUS)

    def test_has_radius_errors(self):
        errors = [e for e in self.result.validation.errors
                  if isinstance(e, ArcRadiusError)]
        assert len(errors) >= 1

    def test_not_ok(self):
        assert not self.result.validation.ok

    def test_summary_mentions_error(self):
        assert "ERROR" in self.result.summary()


class TestPipelineBadNoModalG:
    def test_raises_parse_error(self):
        with pytest.raises(ParseError):
            convert(BAD_NO_G)


class TestPipelineQuantizedArcEdge:
    """Full-circle arc where start == end (I/J offset encodes the full circle)."""

    def setup_method(self):
        self.result = convert(QUANTIZED_ARC)

    def test_converts_without_crash(self):
        assert self.result.output

    def test_ij_present_in_output(self):
        # Full-circle arc must emit both I and J explicitly
        assert "I" in self.result.output
        assert "J" in self.result.output


# ---------------------------------------------------------------------------
# Comparison result
# ---------------------------------------------------------------------------

class TestComparisonResult:
    def test_matching_counts_ok(self):
        c = ComparisonResult(contour_count_source=3, contour_count_emitted=3)
        assert c.contour_counts_match

    def test_mismatched_counts_not_ok(self):
        c = ComparisonResult(contour_count_source=3, contour_count_emitted=2)
        assert not c.contour_counts_match
        assert not c.ok

    def test_summary_mentions_mismatch(self):
        c = ComparisonResult(contour_count_source=3, contour_count_emitted=2)
        assert "mismatch" in c.summary().lower()

    def test_empty_comparison_ok(self):
        c = ComparisonResult(contour_count_source=0, contour_count_emitted=0)
        assert c.ok


# ---------------------------------------------------------------------------
# CLI — integration (via subprocess so we test the real entry point)
# ---------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ncc_converter.cli", *args],
        capture_output=True, text=True,
    )


class TestCLIHelp:
    def test_root_help(self):
        r = _cli("--help")
        assert r.returncode == 0
        assert "convert" in r.stdout
        assert "validate" in r.stdout
        assert "batch" in r.stdout

    def test_convert_help(self):
        r = _cli("convert", "--help")
        assert r.returncode == 0
        assert "--units" in r.stdout

    def test_validate_help(self):
        r = _cli("validate", "--help")
        assert r.returncode == 0

    def test_batch_help(self):
        r = _cli("batch", "--help")
        assert r.returncode == 0
        assert "--pattern" in r.stdout


class TestCLIConvert:
    def test_convert_good_file(self, tmp_path):
        out = tmp_path / "out.nc"
        r = _cli("convert", str(SAMPLE), str(out))
        assert r.returncode == 0
        assert out.exists()
        content = out.read_text()
        assert content.startswith("%")
        assert content.strip().endswith("%")

    def test_convert_default_output_name(self, tmp_path):
        import shutil
        src = tmp_path / "4HDNOAA.NCC"
        shutil.copy(SAMPLE, src)
        r = _cli("convert", str(src))
        assert r.returncode == 0
        expected = tmp_path / "4HDNOAA.nc"
        assert expected.exists()

    def test_convert_with_options(self, tmp_path):
        out = tmp_path / "out.nc"
        r = _cli("convert", str(SAMPLE), str(out),
                 "--precision", "2", "--units", "mm",
                 "--comment", "test comment")
        assert r.returncode == 0
        content = out.read_text()
        assert "(test comment)" in content
        assert "G21" in content

    def test_convert_bad_radius_fails_without_force(self, tmp_path):
        out = tmp_path / "out.nc"
        r = _cli("convert", str(BAD_RADIUS), str(out))
        assert r.returncode != 0

    def test_convert_bad_radius_force_succeeds(self, tmp_path):
        out = tmp_path / "out.nc"
        r = _cli("convert", str(BAD_RADIUS), str(out), "--force")
        assert r.returncode == 0
        assert out.exists()

    def test_convert_missing_file(self, tmp_path):
        r = _cli("convert", str(tmp_path / "ghost.NCC"))
        assert r.returncode != 0

    def test_convert_no_modal_g_fails(self, tmp_path):
        out = tmp_path / "out.nc"
        r = _cli("convert", str(BAD_NO_G), str(out))
        assert r.returncode != 0


class TestCLIValidate:
    def test_validate_good_file(self):
        r = _cli("validate", str(SAMPLE))
        assert r.returncode == 0
        assert "4HDNOAA" in r.stdout

    def test_validate_bad_radius(self):
        r = _cli("validate", str(BAD_RADIUS))
        assert r.returncode != 0
        assert "ERROR" in r.stdout

    def test_validate_verbose(self):
        r = _cli("validate", "--verbose", str(SAMPLE))
        assert r.returncode == 0
        assert "Contour at" in r.stdout

    def test_validate_missing_file(self):
        r = _cli("validate", "/no/such/file.NCC")
        assert r.returncode != 0


class TestCLIBatch:
    def test_batch_converts_all(self, tmp_path):
        r = _cli("batch", str(SAMPLES_GOOD), str(tmp_path))
        assert r.returncode == 0
        nc_files = list(tmp_path.glob("*.nc"))
        ncc_files = list(SAMPLES_GOOD.glob("*.NCC"))
        assert len(nc_files) == len(ncc_files)

    def test_batch_creates_output_dir(self, tmp_path):
        out_dir = tmp_path / "subdir" / "output"
        r = _cli("batch", str(SAMPLES_GOOD), str(out_dir))
        assert out_dir.exists()

    def test_batch_bad_files_fail_without_force(self, tmp_path):
        # SAMPLES_BAD contains files with geometry errors and parse errors;
        # batch should report failures without crashing
        r = _cli("batch", str(SAMPLES_BAD), str(tmp_path))
        # Some files will fail; command should still exit cleanly
        assert "failed" in r.stdout.lower() or r.returncode != 0

    def test_batch_no_line_numbers(self, tmp_path):
        r = _cli("batch", str(SAMPLES_GOOD), str(tmp_path), "--no-line-numbers")
        assert r.returncode == 0
        for f in tmp_path.glob("*.nc"):
            content = f.read_text()
            body_lines = [
                l for l in content.splitlines()
                if l and l != "%" and not l.startswith("(")
            ]
            # Preamble line starts with G; subsequent lines start with G or M
            for line in body_lines:
                assert line[0] in "GM", f"Line numbers present: {line!r}"


# ---------------------------------------------------------------------------
# Regression — known-good output snapshot
# ---------------------------------------------------------------------------

class TestRegression:
    """Snapshot tests: ensure the sample file always produces identical output."""

    def test_output_line_count_stable(self):
        r = convert(SAMPLE)
        assert len(r.output.splitlines()) == 114

    def test_first_rapid_coordinates_stable(self):
        r = convert(SAMPLE)
        lines = r.output.splitlines()
        # Second body line (index 2 = preamble, index 3 = first G0)
        rapid_line = next(l for l in lines if "G0" in l)
        assert "X4.3700" in rapid_line
        assert "Y-2.2000" in rapid_line

    def test_arc_count_stable(self):
        r = convert(SAMPLE)
        assert len(r.geometry.resolved_arcs) == 52

    def test_tool_down_count_stable(self):
        r = convert(SAMPLE)
        cfg_default = EmitterConfig()
        downs = [l for l in r.output.splitlines()
                 if l.strip().endswith(cfg_default.tool_down_code)]
        assert len(downs) == 9

    def test_contour_count_stable(self):
        r = convert(SAMPLE)
        assert len(r.geometry.contours) == 9
