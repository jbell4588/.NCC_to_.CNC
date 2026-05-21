"""
ncc_converter.lexer
~~~~~~~~~~~~~~~~~~~
Converts a RawLine into a sequence of typed tokens.

Burny .NCC grammar (derived from sample analysis):

    line     ::= N_word? token+ RAPID?
    N_word   ::= 'N' INTEGER
    token    ::= G_word | M_word | coord | arc_offset
    G_word   ::= 'G' INTEGER
    M_word   ::= 'M' INTEGER
    coord    ::= ('X' | 'Y' | 'Z' | 'F' | 'K') DECIMAL
    arc_off  ::= ('I' | 'J' | 'K') DECIMAL
    RAPID    ::= 'R'          # trailing R — Burny rapid flag, NOT arc radius
    DECIMAL  ::= '-'? DIGIT+ ('.' DIGIT*)?

Notes
-----
- Coordinates and arc offsets are incremental (G91 implied).
- A trailing 'R' token means the motion is a rapid traverse, not a cut.
- Modal G codes: once set, they persist until changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Sequence

from .loader import RawLine


# ---------------------------------------------------------------------------
# Token types
# ---------------------------------------------------------------------------

class TokKind(Enum):
    N_WORD     = auto()   # line number
    G_WORD     = auto()   # motion / mode code
    M_WORD     = auto()   # miscellaneous function
    X          = auto()
    Y          = auto()
    Z          = auto()
    I          = auto()
    J          = auto()
    K          = auto()
    F          = auto()   # feedrate
    RAPID      = auto()   # trailing R — Burny rapid flag


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokKind
    raw: str              # original text of the token, for error messages
    value: int | float | None = None  # numeric payload; None for RAPID


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

# One regex that matches every possible token in one pass.
# Groups: (letter)(number)  OR  trailing-R
_TOKEN_RE = re.compile(
    r"""
    (?:
        ([NGMXYZIJKFngmxyzijkf])   # word letter
        \s*                         # optional space (rare but seen in some dialects)
        (-?[0-9]+(?:\.[0-9]*)?)    # numeric value
    )
    |
    (R)                             # trailing rapid flag (standalone R at end)
    """,
    re.VERBOSE,
)

_LETTER_TO_KIND: dict[str, TokKind] = {
    "N": TokKind.N_WORD,
    "G": TokKind.G_WORD,
    "M": TokKind.M_WORD,
    "X": TokKind.X,
    "Y": TokKind.Y,
    "Z": TokKind.Z,
    "I": TokKind.I,
    "J": TokKind.J,
    "K": TokKind.K,
    "F": TokKind.F,
}


class LexError(ValueError):
    """Raised when a line cannot be tokenised."""

    def __init__(self, message: str, raw_line: RawLine) -> None:
        super().__init__(f"Line {raw_line.file_line}: {message} — {raw_line.text!r}")
        self.raw_line = raw_line


@dataclass(frozen=True, slots=True)
class LexedLine:
    source: RawLine
    tokens: tuple[Token, ...]


def lex_line(raw: RawLine) -> LexedLine:
    """Tokenise a single normalised line."""
    tokens: list[Token] = []
    text = raw.text

    # Consume the trailing R *before* running the main regex so the regex
    # doesn't misidentify it.  We strip it from the right after accounting
    # for optional whitespace.
    has_trailing_r = False
    stripped = text.rstrip()
    if stripped.endswith("R"):
        # Make sure it isn't part of a word like "N123G1X1.0R" where R is
        # genuinely isolated.  In practice the Burny format always has the R
        # at the very end after all coordinate words, so this is safe.
        before_r = stripped[:-1].rstrip()
        # Verify the character before R is a digit or dot (end of a number)
        # or that R is the only token — if so it's the rapid flag.
        if not before_r or (before_r[-1].isdigit() or before_r[-1] == "."):
            has_trailing_r = True
            text = before_r

    pos = 0
    for m in _TOKEN_RE.finditer(text):
        # Detect any unmatched gap (unrecognised characters)
        if m.start() > pos:
            gap = text[pos:m.start()].strip()
            if gap:
                raise LexError(f"Unexpected characters {gap!r}", raw)
        pos = m.end()

        letter_grp, number_grp, rapid_grp = m.group(1), m.group(2), m.group(3)

        if rapid_grp:
            # A standalone R in the middle of the line would be unusual;
            # treat it like the trailing R flag.
            tokens.append(Token(kind=TokKind.RAPID, raw="R", value=None))
            continue

        letter = letter_grp.upper()
        kind = _LETTER_TO_KIND.get(letter)
        if kind is None:
            raise LexError(f"Unknown word letter {letter!r}", raw)

        # Parse numeric value
        num_str = number_grp
        try:
            value: int | float = int(num_str) if "." not in num_str else float(num_str)
        except ValueError as exc:
            raise LexError(f"Bad numeric value {num_str!r}", raw) from exc

        tokens.append(Token(kind=kind, raw=m.group(0), value=value))

    # Check for trailing unparsed content
    remainder = text[pos:].strip()
    if remainder:
        raise LexError(f"Unparsed trailing content {remainder!r}", raw)

    if has_trailing_r:
        tokens.append(Token(kind=TokKind.RAPID, raw="R", value=None))

    return LexedLine(source=raw, tokens=tuple(tokens))


def lex(lines: Sequence[RawLine]) -> list[LexedLine]:
    """Lex every line in the file.  Propagates LexError on the first failure."""
    return [lex_line(line) for line in lines]
