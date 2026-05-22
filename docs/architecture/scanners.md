# Scanners

DepPulse uses a **Strategy Pattern** for language-specific parsing. Each scanner implements `BaseScanner` and is registered in the orchestrator.

## Base interface

```python
class BaseScanner(ABC):
    @property
    def name(self) -> str: ...

    def can_scan(self, path: Path) -> bool: ...

    def scan(self, file_path: Path, project_root: Path, file_index: dict[str, Path]) -> ScanResult: ...
```

## Python Scanner

Uses Python's built-in `ast` module to parse import statements and extract function/class/method symbols.

**Supported imports:**
- `import x`
- `from x import y`
- Relative imports (`from . import x`)
- Dynamic imports (`__import__()`, `importlib.import_module()`)

## Java Scanner

Uses the `javalang` library to parse Java source files.

**Supported constructs:**
- `import com.example.Utils;`
- `import static com.example.Utils.method;`
- Wildcard imports (`import com.example.*;`)
- Class, interface, enum, and annotation symbols

## Kotlin Scanner

Uses tree-sitter-kotlin to parse Kotlin source files.

**Supported constructs:**
- `import com.example.Utils`
- Top-level functions, classes, interfaces, objects, annotations
- Extension functions and properties

## C/C++ Scanner

Uses tree-sitter-cpp to parse C/C++ source files.

**Supported constructs:**
- `#include "local.h"` (local includes)
- `#include <system.h>` (system includes)
- Header guards are handled implicitly via the file index

## JavaScript Scanner

Uses tree-sitter-javascript to parse JavaScript and JSX files.

**Supported constructs:**
- ESM: `import { x } from 'y'`, `import React from 'react'`, `import * as x from 'y'`
- CJS: `const x = require('y')`
- Dynamic imports: `import('y')`

## TypeScript Scanner

Uses tree-sitter-typescript to parse TypeScript and TSX files.

Same import constructs as JavaScript, plus:
- Path alias resolution via `tsconfig.json` `compilerOptions.paths`
- Type-only imports (`import type { X }`)
