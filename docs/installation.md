# Installation

## Requirements

- Python >= 3.10
- pip

## Standard install

```bash
pip install deppulse
```

## Development install

```bash
git clone https://github.com/deppulse/deppulse
cd deppulse
pip install -e ".[dev]"
```

## Tree-sitter grammars

DepPulse uses tree-sitter for parsing non-Python languages. The following grammars are installed as dependencies:

- `tree-sitter-kotlin` — Kotlin source files
- `tree-sitter-cpp` — C and C++ source files
- `tree-sitter-typescript` — TypeScript and TSX source files
- `tree-sitter-javascript` — JavaScript and JSX source files

## Optional dependencies

```bash
# Documentation generation
pip install -e ".[docs]"

# All optional dependencies
pip install -e ".[dev,docs]"
```
