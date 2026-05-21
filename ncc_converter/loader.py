"""
ncc_converter.loader
~~~~~~~~~~~~~~~~~~~~
Reads a .NCC file from disk, handles ASCII / UTF-8 / Latin-1 encodings,
strips the program-delimiter lines (%), normalises whitespace, and
returns a list of (line_number_in_file, raw_text) pairs ready for the lexer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RawLine:
    """A single physical line from the source file, before any parsing."""
    file_line: int   # 1-based line number in the source file
    text: str        # normalised text (stripped, CRLF → LF already done)


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

_ENCODINGS = ("ascii", "utf-8", "latin-1")  # latin-1 never fails — use last


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(f"Cannot read {path}: {exc}") from exc


def _decode(raw: bytes, path: Path) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # Should be unreachable — latin-1 is a superset of every byte value.
    raise ValueError(f"Could not decode {path} with any of {_ENCODINGS}")


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_line(text: str) -> str:
    """Strip CR, leading/trailing whitespace; collapse internal runs of space/tab."""
    return " ".join(text.replace("\r", "").split())


def _is_empty_or_delimiter(text: str) -> bool:
    return not text or text == "%"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(path: os.PathLike | str) -> list[RawLine]:
    """
    Load a .NCC file and return its non-empty, non-delimiter lines.

    Parameters
    ----------
    path:
        Path to the .NCC file.

    Returns
    -------
    list[RawLine]
        One entry per meaningful line, in file order.

    Raises
    ------
    FileNotFoundError
        If the file does not exist or cannot be opened.
    ValueError
        If the file cannot be decoded.
    """
    p = Path(path)
    raw = _read_bytes(p)
    text = _decode(raw, p)

    result: list[RawLine] = []
    for file_line, line in enumerate(text.splitlines(), start=1):
        normalised = _normalise_line(line)
        if _is_empty_or_delimiter(normalised):
            continue
        result.append(RawLine(file_line=file_line, text=normalised))

    return result
