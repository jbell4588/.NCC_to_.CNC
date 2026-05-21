
# ncc-converter

Convert Burny plasma cutter `.NCC` files into standard G-code for use in modern tools like NC Viewer, CAMotics, and G-code controllers.

---

## What this does

Burny `.NCC` files are a legacy proprietary format that most modern software cannot read directly.

This tool converts them into clean, explicit G-code:

- Converts implicit motion to explicit commands (G0 / G1 / G2 / G3)
- Expands modal commands into full form
- Normalizes coordinate handling (absolute positioning)
- Translates Burny-specific cutting commands into standard M-codes
- Produces simulation-ready output for modern tooling

---

## Installation

```bash
pip install -e ".[dev]"
```

Requires Python **3.10+**

---

## Quick start

### Convert a file

```bash
python -m ncc_converter.cli convert PART.NCC
```

Output:

```text
PART.nc
```

---

### Convert with options

```bash
python -m ncc_converter.cli convert PART.NCC --precision 3
python -m ncc_converter.cli convert PART.NCC --no-line-numbers
python -m ncc_converter.cli convert PART.NCC --units inch
python -m ncc_converter.cli convert PART.NCC --comment "Job 42"
```

---

### Validate a file (no output generated)

```bash
python -m ncc_converter.cli validate PART.NCC
```

Exit codes:
- `0` = valid
- `1` = errors found

---

### Batch convert a folder

```bash
python -m ncc_converter.cli batch ./ncc_files/ ./output/
```

Optional:

```bash
python -m ncc_converter.cli batch ./ncc_files/ ./output/ --pattern "*.NCC" --verbose
```

Notes:
- Output directory is created automatically
- Files with geometry errors are skipped unless `--force` is used

---

## Command options (summary)

| Option | Description |
|--------|-------------|
| `--precision N` | Decimal precision for coordinates |
| `--units inch` | Output in inches (G20) instead of mm (G21) |
| `--no-line-numbers` | Remove N-word line numbering |
| `--comment TEXT` | Add header comment |
| `--tool-down CODE` | Custom tool-down M-code |
| `--tool-up CODE` | Custom tool-up M-code |
| `--force` | Convert even if errors are detected |
| `--verbose` | Verbose output |
| `--debug` | Debug-level logging |

---

## Examples directory

See:

```
examples/
```

for sample `.NCC` input files and expected output behavior.

---

## How it works (technical overview)

This section is optional for advanced users or contributors.

The pipeline is:

```
load → lex → parse → geometry → validate → emit
```

Core modules:

```
ncc_converter/
    loader.py      File input + normalization
    lexer.py       Tokenization
    parser.py      Semantic parsing
    geometry.py    Geometry reconstruction + validation
    emitter.py     G-code generation
    pipeline.py    High-level convert() API
    cli.py         Command-line interface
```

---

## Burny format notes (important)

### Trailing `R` = rapid move

In Burny `.NCC`, a trailing `R` on a G1 line indicates a **rapid traverse**, not a radius.

This is converted to `G0`.

---

### M-code inversion

Burny uses inverted semantics:

| Burny | Meaning | Standard |
|------|--------|----------|
| M04 | pierce / start cutting | M03 (configurable) |
| M03 | lift / stop cutting | M05 (configurable) |

---

### Coordinate precision

Burny files typically use **2 decimal places (0.01 mm resolution)**.

Small arc tolerances (~0.015 mm) are expected and handled safely.

---

### Lead-in moves

Cutting paths often include:

1. Rapid move to pierce point
2. Pierce command
3. Lead-in motion
4. Cutting arcs
5. Lift command

The validator accounts for lead-in geometry when checking closure.

---

## Safety notes

Before running on a machine:

1. Simulate in **NC Viewer** → https://ncviewer.com
2. Simulate in **CAMotics**
3. Run a dry test (torch off)
4. Always test on scrap material first

---

## Windows note (important)

If you encounter Unicode errors (rare on Windows terminals), use:

- Windows Terminal (recommended)
- Or ensure UTF-8 output is enabled

This affects display of symbols like arrows in logs only, not conversion output.

---

## Development

```bash
python -m pytest
python -m pytest --cov=ncc_converter
python -m ruff check src/
```

---

## Tests

```
tests/
    samples/good/   Valid .NCC files
    samples/bad/    Invalid test cases
```

---

## License

See LICENSE file.
```
````
