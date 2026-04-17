# Slamcore Python Best Practices

This document is intended for use by agents and developers in other Slamcore repositories.
Follow these conventions when generating or reviewing Python code.

---

## Target Environment

- **Python versions:** 3.8, 3.9, 3.10, 3.12
- **OS:** Ubuntu 20.04 / Ubuntu 22.04 (default to 20.04 when uncertain)
- Strive for OS interoperability across supported Ubuntu releases.

---

## Package Structure

Python code must be distributed as a Python package configured via `pyproject.toml`.
Do not use `setup.py`, `setup.cfg`, `requirements.txt`, or `MANIFEST.in`.

```
project-root/
├── pyproject.toml
├── uv.lock
├── <package-name>/
│   ├── __init__.py
│   ├── library_module.py
│   ├── subdirectory/
│   │   ├── __init__.py
│   │   └── more_modules.py
│   └── scripts/
│       ├── __init__.py
│       └── executable_script.py
├── test/
│   ├── __init__.py
│   ├── test_module.py
│   └── test_data/
│       └── sample_data.csv
└── data/
    └── sample_datasets/
```

**File placement rules:**

| File type | Location |
|-----------|----------|
| Library modules (imported) | `<package>/` or subdirectories |
| Executable scripts | `<package>/scripts/` or `<package>/__main__.py` |
| Package data (configs, samples) | Top-level `data/` |
| Test data | `test/test_data/` |
| Tests | Top-level `test/` (not packaged in distribution) |

Declare all executables in `pyproject.toml`:

```toml
[project.scripts]
my_script = "package_name.scripts.my_script:main"
```

Use `cookiecutter` with the `python_bootstrap` template to generate new packages.

---

## Naming Conventions

Follow PEP 8:

| Element | Style | Example |
|---------|-------|---------|
| Classes | `PascalCase` | `class DataProcessor:` |
| Functions / methods | `snake_case` | `def process_frame():` |
| Private methods | `_snake_case` | `def _internal_helper():` |
| Private class attributes | `__double_underscore` | `self.__buffer = []` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_BUFFER_SIZE = 1024` |
| Variables | `snake_case` | `frame_count = 0` |
| Module / file names | `snake_case` | `depth_estimator.py` |

---

## Code Formatting

### black

Use `black` as the uncompromising code formatter. Line length is **95 characters**.

```toml
[tool.black]
preview = true
line-length = 95
target-version = ['py38', 'py39', 'py310', 'py312']
include = '\.pyi?$'
```

Disable selectively when manual formatting is intentional:

```python
# fmt: off
matrix = [
    1, 0, 0,
    0, 1, 0,
    0, 0, 1,
]
# fmt: on
```

### isort

Use `isort` to sort and group imports. Configure for black compatibility:

```toml
[tool.isort]
line_length = 95
include_trailing_comma = true
multi_line_output = 3
profile = "black"
```

### VS Code integration

`.vscode/settings.json`:

```json
{
    "[python]": {
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
        },
        "editor.defaultFormatter": "ms-python.black-formatter",
        "editor.formatOnSave": true,
        "editor.rulers": [95]
    },
    "isort.check": true,
    "black-formatter.importStrategy": "fromEnvironment"
}
```

---

## Imports

- **Always use absolute imports.** Never use relative imports.
- **Never use `from <package> import *`.** Wildcard imports break linters, IDEs, and static analysis.

```python
# Correct
from package.module import ClassName, function_name
import package.module as pm

# Wrong
from .module import something          # relative import
from package.module import *           # wildcard import
```

---

## String Formatting

Prefer f-strings. Use `.format()` when f-strings are not available. Avoid `%` formatting.

```python
var = "pi"
val = 3.14

f"{var}'s value is {val}"          # best
"{}'s value is {}".format(var, val)  # acceptable
"%s's value is %s" % (var, val)    # avoid
```

**Exception — logging:** Always use `%`-style with the standard logger (deferred evaluation):

```python
import logging
logger = logging.getLogger(__name__)
logger.info("Processing frame %s of %s", current, total)
```

---

## Conditionals and Return Values

Do not add unnecessary parentheses in simple conditions:

```python
if a_condition:      # correct
if (a_condition):    # wrong
```

Do not wrap multi-value returns in parentheses:

```python
return 1, 2, 3       # correct
return (1, 2, 3)     # wrong
```

Use parentheses in complex conditionals to aid readability:

```python
if (very_long_condition_a
        and very_long_condition_b
        and very_long_condition_c):
```

---

## List Comprehensions

Prefer list comprehensions over manual loop-and-append:

```python
# Correct
evens_squared = [i ** 2 for i in range(10) if i % 2 == 0]

# Avoid
evens_squared = []
for i in range(10):
    if i % 2 == 0:
        evens_squared.append(i ** 2)
```

---

## Logging

Use the standard library `logging` module. Do not use `print()` or third-party logging libraries.

Get a module-level logger using `__name__` so log output identifies its origin:

```python
import logging

logger = logging.getLogger(__name__)

logger.debug("Detailed diagnostic info")
logger.info("Processing started")
logger.warning("Low memory, continuing")
logger.error("Failed to read file: %s", path)
logger.critical("Unrecoverable error, shutting down")
```

Configure logging once at the application entry point, not inside library code:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
```

Library modules must never call `logging.basicConfig()` or add handlers — leave configuration
to the application.

---

## Type Annotations

Annotate all function signatures (parameters and return types). Use `mypy` for static checking.
Prefer type annotations over `:type:` / `:rtype:` docstring fields.

```python
from typing import List, Optional

def search_by_name(
    search_term: str,
    vocab_id_or_name: Optional[str] = None,
) -> List[str]:
    ...
```

`mypy` configuration:

```toml
[tool.mypy]
python_version = "3.8"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true
```

---

## Documentation

Use Sphinx field list notation for all docstrings.

```python
from typing import List, Optional

def search_by_name(
    search_term: str,
    vocab_id_or_name: Optional[str] = None,
) -> List[str]:
    """Return all tags whose names contain a given string.

    By default only free tags (tags which do not belong to any vocabulary)
    are returned. If the optional argument ``vocab_id_or_name`` is given then
    only tags from that vocabulary are returned.

    :param search_term: the string to search for in the tag names
    :param vocab_id_or_name: the id or name of the vocabulary to look in
                             (optional, default: None)

    :returns: a list of tags that match the search term
    :raises ValueError: if search_term is empty
    """
```

Required docstring fields:

| Field | Purpose |
|-------|---------|
| `:param <name>:` | Parameter description |
| `:returns:` | Return value description |
| `:raises <ExceptionType>:` | Exceptions that may be raised |

Generate HTML docs with Sphinx. Validate links in CI.

---

## Testing

- Write unit tests for all non-trivial functions and classes.
- Target **100% code coverage**.
- Separate unit tests from system/integration tests.
- Tests live in top-level `test/` and are **not** packaged with the distribution.
- Test data lives in `test/test_data/`.

Run tests and checks via CI. A typical `lint.sh` covers:

1. `black` — formatting validation
2. `isort` — import sorting validation
3. `mypy` — static type checking
4. `pytest` — test execution
5. Coverage report generation
6. Sphinx doc build + link check

---

## Dependency Management

Use `uv` for all dependency management. All configuration belongs in `pyproject.toml`.

```bash
# Install project with dependencies
uv sync

# Run a declared script inside the managed environment
uv run my_script

# Run an arbitrary command inside the environment
uv run python -m my_package

# Add a runtime dependency
uv add <package>

# Add a development dependency
uv add --dev <package>
```

`uv` generates a `uv.lock` file — commit this file to version control for reproducible installs.

---

## pyproject.toml Reference

```toml
[project]
name = "my-package"
version = "0.1.0"
description = "Short description"
authors = [{ name = "Author", email = "author@slamcore.com" }]
requires-python = ">=3.8"
dependencies = []

[project.optional-dependencies]
dev = [
    "black",
    "isort",
    "mypy",
    "pytest",
    "pytest-cov",
    "sphinx",
]

[project.scripts]
my_script = "my_package.scripts.my_script:main"

[tool.black]
preview = true
line-length = 95
target-version = ['py38', 'py39', 'py310', 'py312']
include = '\.pyi?$'

[tool.isort]
line_length = 95
include_trailing_comma = true
multi_line_output = 3
profile = "black"

[tool.mypy]
python_version = "3.8"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## CI/CD Checklist

Before merging any pull request, ensure:

- [ ] `black` reports no formatting changes
- [ ] `isort` reports no import ordering changes
- [ ] `mypy` reports zero type errors
- [ ] All `pytest` tests pass
- [ ] Code coverage meets target (100% goal)
- [ ] Sphinx documentation builds without errors
- [ ] No broken links in generated documentation

---

## Quick Reference — Common Mistakes to Avoid

| Wrong | Correct |
|-------|---------|
| `from module import *` | `from module import SpecificName` |
| `from .sibling import X` | `from package.sibling import X` |
| `print("debug info")` | `logger.debug("debug info")` |
| `"%s" % value` | `f"{value}"` |
| `if (condition):` | `if condition:` |
| `return (a, b)` | `return a, b` |
| `setup.py` / `requirements.txt` | `pyproject.toml` with uv |
| Unannotated functions | `def fn(x: int) -> str:` |
| No docstring | Sphinx field list docstring |
