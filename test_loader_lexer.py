"""
Tests for loader and lexer — using the real 4HDNOAA.NCC sample.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from ncc_converter.loader import RawLine, load
from ncc_converter.lexer import LexError, LexedLine, TokKind, Token, lex, lex_line


SAMPLE = Path(__file__).parent / "samples" / "good" / "4HDNOAA.NCC"


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------

class TestLoader:
    def test_returns_list_of_raw_lines(self):
        lines = load(SAMPLE)
        assert isinstance(lines, list)
        assert all(isinstance(l, RawLine) for l in lines)

    def test_strips_percent_delimiter(self):
        lines = load(SAMPLE)
        assert not any(l.text == "%" for l in lines)

    def test_no_empty_lines(self):
        lines = load(SAMPLE)
        assert all(l.text for l in lines)

    def test_crlf_removed(self):
        lines = load(SAMPLE)
        assert not any("\r" in l.text for l in lines)

    def test_trailing_space_stripped(self):
        # N085 in the sample has a trailing space after R
        lines = load(SAMPLE)
        assert not any(l.text != l.text.strip() for l in lines)

    def test_first_line_is_n005(self):
        lines = load(SAMPLE)
        assert lines[0].text.startswith("N005")

    def test_last_line_is_m30(self):
        lines = load(SAMPLE)
        assert lines[-1].text == "N227M30"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load("/nonexistent/path/to/file.NCC")

    def test_line_count(self):
        lines = load(SAMPLE)
        # The sample has 111 meaningful lines (excluding the % delimiter)
        assert len(lines) == 111


# ---------------------------------------------------------------------------
# Lexer tests — individual lines
# ---------------------------------------------------------------------------

def make_raw(text: str, file_line: int = 1) -> RawLine:
    return RawLine(file_line=file_line, text=text)


class TestLexerTokenKinds:
    def test_n_word(self):
        ll = lex_line(make_raw("N005G1X4.37Y-2.2R"))
        kinds = [t.kind for t in ll.tokens]
        assert kinds[0] == TokKind.N_WORD
        assert ll.tokens[0].value == 5

    def test_g_word(self):
        ll = lex_line(make_raw("N005G1X4.37Y-2.2R"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.G_WORD in kinds

    def test_m_word(self):
        ll = lex_line(make_raw("N007M04"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.M_WORD in kinds
        m = next(t for t in ll.tokens if t.kind == TokKind.M_WORD)
        assert m.value == 4

    def test_trailing_r_is_rapid_token(self):
        ll = lex_line(make_raw("N005G1X4.37Y-2.2R"))
        assert ll.tokens[-1].kind == TokKind.RAPID

    def test_trailing_r_with_trailing_space(self):
        ll = lex_line(make_raw("N085G1X0.07Y-0.31R "))
        assert ll.tokens[-1].kind == TokKind.RAPID

    def test_no_trailing_r_on_cut_line(self):
        ll = lex_line(make_raw("N009X0.4Y-0.48"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.RAPID not in kinds

    def test_arc_line_ij(self):
        ll = lex_line(make_raw("N011G3X0.23Y0.48I-0.4J0.48"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.I in kinds
        assert TokKind.J in kinds

    def test_arc_line_i_only(self):
        # N013 has only I, no J
        ll = lex_line(make_raw("N013X-0.62Y0.62I-0.62"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.I in kinds
        assert TokKind.J not in kinds

    def test_arc_line_j_only(self):
        # N015 has only J, no I
        ll = lex_line(make_raw("N015X-0.62Y-0.63J-0.63"))
        kinds = [t.kind for t in ll.tokens]
        assert TokKind.J in kinds
        assert TokKind.I not in kinds

    def test_negative_decimal(self):
        ll = lex_line(make_raw("N009X0.4Y-0.48"))
        y = next(t for t in ll.tokens if t.kind == TokKind.Y)
        assert y.value == pytest.approx(-0.48)

    def test_positive_decimal(self):
        ll = lex_line(make_raw("N009X0.4Y-0.48"))
        x = next(t for t in ll.tokens if t.kind == TokKind.X)
        assert x.value == pytest.approx(0.4)


class TestLexerErrors:
    def test_unknown_word_letter(self):
        with pytest.raises(LexError):
            lex_line(make_raw("N001Q99"))

    def test_partial_word_no_number(self):
        # A lone letter with no digits should fail
        with pytest.raises(LexError):
            lex_line(make_raw("N001X"))


# ---------------------------------------------------------------------------
# Lexer integration — full file
# ---------------------------------------------------------------------------

class TestLexerFullFile:
    def setup_method(self):
        self.raw_lines = load(SAMPLE)
        self.lexed = lex(self.raw_lines)

    def test_all_lines_lexed(self):
        assert len(self.lexed) == len(self.raw_lines)

    def test_all_lines_have_tokens(self):
        assert all(len(ll.tokens) > 0 for ll in self.lexed)

    def test_every_line_starts_with_n_word(self):
        for ll in self.lexed:
            assert ll.tokens[0].kind == TokKind.N_WORD, (
                f"Expected N_WORD first on line {ll.source.file_line}: {ll.source.text}"
            )

    def test_g_codes_present(self):
        g_codes = {
            t.value
            for ll in self.lexed
            for t in ll.tokens
            if t.kind == TokKind.G_WORD
        }
        assert 1 in g_codes   # G1 linear
        assert 2 in g_codes   # G2 CW arc
        assert 3 in g_codes   # G3 CCW arc

    def test_m_codes_present(self):
        m_codes = {
            t.value
            for ll in self.lexed
            for t in ll.tokens
            if t.kind == TokKind.M_WORD
        }
        assert 3 in m_codes   # M03 lift
        assert 4 in m_codes   # M04 pierce/cut
        assert 30 in m_codes  # M30 end of program

    def test_rapid_tokens_on_rapid_lines(self):
        rapid_lines = [ll for ll in self.lexed if any(t.kind == TokKind.RAPID for t in ll.tokens)]
        # All should also carry a G1 explicitly or inherit it
        assert len(rapid_lines) > 0

    def test_no_k_tokens_in_sample(self):
        # This file uses only X/Y plane arcs — no K expected
        k_tokens = [
            t for ll in self.lexed for t in ll.tokens if t.kind == TokKind.K
        ]
        assert k_tokens == []
