# ncc-converter

Converts Burny plasma-cutter `.NCC` files to standard G-code suitable for
NC Viewer, CAMotics, and modern G-code controllers.

## Why

Burny `.NCC` files use a proprietary compressed format that most standard
G-code tools cannot read:

| Burny `.NCC` | Standard G-code output |
|---|---|
| Implicit incremental coordinates (G91) | Explicit absolute coordinates (G90) |
| Trailing `R` = rapid traverse | `G0` rapid |
| `M04` = pierce (tool down) | `M03` (configurable) |
| `M03` = lift (tool up) | `M05` (configurable) |
| Modal G codes (omitted after first use) | Every line fully explicit |
| Coordinates to 2 decimal places | Configurable precision (default 4 d.p.) |

## Installation

```bash
pip install -e ".[dev]"   # editable install with test dependencies
```

Requires Python 3.10+.

## Command-line usage

### Convert a single file

```bash
ncc convert PART.NCC                     # writes PART.nc
ncc convert PART.NCC output.nc           # explicit output name
ncc convert PART.NCC --precision 3       # 3 decimal places
ncc convert PART.NCC --units inch        # emit G20 instead of G21
ncc convert PART.NCC --no-line-numbers   # suppress N-words
ncc convert PART.NCC --comment "Job 42" # add header comment
ncc convert PART.NCC --tool-down M07 --tool-up M09   # custom M-codes
ncc convert PART.NCC --force             # emit even if geometry errors found
```

### Validate without converting

```bash
ncc validate PART.NCC           # exit code 0 = OK, 1 = errors
ncc validate PART.NCC --verbose # show per-contour geometry details
```

### Batch convert a directory

```bash
ncc batch ./ncc_files/ ./output/
ncc batch ./ncc_files/ ./output/ --pattern "*.NCC" --verbose
```

Files with geometry errors are skipped unless `--force` is used.
The output directory is created if it does not exist.

### Verbose / debug logging

```bash
ncc --verbose convert PART.NCC    # INFO-level log messages
ncc --debug   convert PART.NCC    # DEBUG-level (very detailed)
```

## Python API

```python
from pathlib import Path
from ncc_converter.pipeline import convert
from ncc_converter.emitter import EmitterConfig

cfg = EmitterConfig(
    precision=4,
    header_comment="My job",
    tool_down_code="M03",
    tool_up_code="M05",
)

result = convert(Path("PART.NCC"), cfg)

# Emitted G-code string
print(result.output)

# Save to file
Path("PART.nc").write_text(result.output)

# Inspect geometry
for contour in result.geometry.contours:
    print(f"  Pierce at ({contour.pierce_x:.3f}, {contour.pierce_y:.3f}), "
          f"{len(contour.moves)} moves, closed={contour.is_closed}")

# Check for errors
if not result.validation.ok:
    for err in result.validation.errors:
        print("ERROR:", err)
for warn in result.validation.warnings:
    print("WARN:", warn)
```

### Using individual pipeline stages

```python
from ncc_converter.loader import load
from ncc_converter.lexer import lex
from ncc_converter.parser import parse
from ncc_converter.geometry import build, validate
from ncc_converter.emitter import emit

raw    = load("PART.NCC")
lexed  = lex(raw)
parsed = parse(lexed)
model  = build(parsed.operations)
result = validate(model)
gcode  = emit(parsed.operations)
```

## Burny format notes

### Trailing `R` — rapid flag

In standard G-code, `R` on an arc line specifies the radius.  In Burny
`.NCC`, a trailing `R` on a **G1** line marks a **rapid traverse** — the
torch moves at full speed without cutting.  This converter maps it to `G0`.

### Coordinate precision

Burny stores coordinates to **2 decimal places** (0.01 mm resolution).
This means arc endpoints can't perfectly represent a circle, so arc radius
errors of up to ~0.01 mm are inherent in well-formed files.  The validator
uses a tolerance of 0.015 mm and will not flag these as errors.

### `M03` / `M04` inversion

Burny inverts the standard spindle convention:

- `M04` = **pierce** (tool down / start cutting)
- `M03` = **lift**   (tool up / stop cutting)

The converter maps these to configurable M-codes (`M03`/`M05` by default).

### Lead-in moves

A typical Burny cutting sequence is:

1. Rapid to pierce location
2. `M04` — pierce
3. Short linear lead-in (brings the torch onto the cutting circle)
4. Arc sequence forming the actual hole/profile
5. `M03` — lift

The geometry validator accounts for lead-in moves when checking contour
closure, so a correctly-formed hole reports as closed even though the
contour includes the lead-in segment.

### Skipped line numbers

Burny sometimes emits non-contiguous line numbers (e.g. N067 → N071,
skipping N069).  The converter tolerates this silently.

## Development

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=ncc_converter --cov-report=term-missing

# Lint
python -m ruff check src/
```

### Project structure

```
src/ncc_converter/
    loader.py       File I/O, encoding detection, line normalisation
    lexer.py        Tokeniser — RawLine → LexedLine
    modal.py        Modal state tracker (G/M codes, position, feedrate)
    operations.py   Typed operation dataclasses (RapidMove, ArcCut, …)
    parser.py       Semantic parser — LexedLine[] → Operation[]
    geometry.py     Geometry model, arc resolution, validation
    emitter.py      G-code emitter — Operation[] → string
    pipeline.py     High-level convert() API
    cli.py          Command-line interface (ncc convert / validate / batch)

tests/
    samples/good/   Known-good .NCC files
    samples/bad/    Malformed files used in error-path tests
```

## Test output on real machines

Before running converted G-code on hardware:

1. **NC Viewer** — paste the output at <https://ncviewer.com> and verify
   the toolpath looks correct.
2. **CAMotics** — open the `.nc` file in CAMotics to simulate material
   removal.
3. **Dry run** — run on the machine with the torch off, watching for
   unexpected motion.
4. **Live run** — cut a test piece in scrap material before production.
