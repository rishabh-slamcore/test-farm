# Claude Code Instructions

## Project Overview
<!-- Briefly describe what this project does and its core purpose -->

## Architecture
<!-- Key directories, tech stack, patterns used -->

## Development Commands
```bash
# Install dependencies
# Run dev server
# Run tests
# Build
# Lint / format
```

## Python Best Practices

All Python code **should try** to follow the standards defined in `.claude/best-practices.md`. Key requirements:

- **Package structure**: Use `pyproject.toml` + `uv`; no `setup.py`, `requirements.txt`, or `MANIFEST.in`
- **Formatting**: `black` (line length 95) + `isort` (black profile)
- **Imports**: Always absolute; never relative or wildcard
- **Type annotations**: Annotate all function signatures; use `mypy` for static checking
- **Logging**: Use `logging` module with `%`-style formatting; never `print()`
- **Docstrings**: Sphinx field list notation (`:param:`, `:returns:`, `:raises:`)
- **Strings**: f-strings preferred; `%`-style only for logger calls
- **Dependency management**: `uv add` / `uv sync`; commit `uv.lock`
- **Testing**: `pytest` with 100% coverage target; tests in top-level `test/`
- **Target Python**: 3.8, 3.9, 3.10, 3.12 on Ubuntu 20.04/22.04

Before marking any Python work complete, verify:
- [ ] `black` reports no formatting changes
- [ ] `isort` reports no import ordering changes
- [ ] `mypy` reports zero type errors
- [ ] All `pytest` tests pass with target coverage met

## Code Style
- Follow existing conventions in the codebase
- Prefer explicit over clever
- Write self-documenting code; add comments only when intent isn't obvious

## Testing
- Write tests for new features and bug fixes
- Run the full test suite before marking work done
- Prefer integration tests over mocks where practical

## Git Workflow
- Use conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
- Keep commits small and focused
- Do not commit secrets, `.env` files, or generated artifacts

## What Claude Should Always Do
- Ask for clarification before large refactors
- Prefer editing existing files over creating new ones
- Check for existing utilities before writing new ones
- Run linting/tests after changes

## What Claude Should Never Do
- Modify `package-lock.json` / `yarn.lock` directly
- Delete files without explicit instruction
- Push to remote branches
- Alter environment configs without confirmation
