## Project Overview
test-farm is a controlled harness for replaying Slamcore Aware update broadcast scenarios
under known network conditions.

Its purpose is to let Slamcore engineers validate update delivery reliability under realistic
warehouse-style network impairment and re-run named failure scenarios on demand from a
single-host CLI workflow.

## Architecture
<!-- Key directories, tech stack, patterns used -->

## Development Commands
```bash
# Create the local virtual environment
uv venv

# Install project and development dependencies
uv sync --dev

# Install pre-commit hooks
uv run pre-commit install

# Run the CLI placeholder / execute a scenario
uv run test-farm run <scenario.yaml>

# Run tests
uv run pytest

# Run local quality checks
uv run pre-commit run

# Run quality checks across the whole repo
uv run pre-commit run --all-files
```

Agent note: in the Codex sandbox, if a `uv` command fails trying to create lock or temporary files under `/home/rishi/.cache/uv`, rerun it as `UV_CACHE_DIR=/tmp/uv-cache uv ...`. This has been verified to fix that cache-path failure mode when invoking `uv` through a shell with extra environment variables. It does not fix unrelated offline dependency-resolution failures.

Agent note: in the Codex sandbox,Do not rely on streamed pytest output. Run pytest with stdout/stderr redirected to /tmp/pytest.out and write the exit code to /tmp/pytest.exit. Then read both files in a separate command. If the exec wrapper does not return, check for pytest/python child processes and inspect the output file. Do not claim tests passed unless /tmp/pytest.exit contains 0. If you the tests are hung, try running the tests outside the sandbox, directly on the host.

Agent note: always run `uv run pytest -k test_execute_invocation_completes_two_client_baseline_with_real_subjects` with elevated permissions outside the sandbox, because this test may need host-level socket binding.

Agent note: when a new test is added that binds to sockets or otherwise requires host-level networking privileges, add that test command to this elevated-permissions note list in `AGENTS.md` so future runs are not attempted in the sandbox first.

## Python Best Practices

All Python code **should try** to follow the standards defined in `docs/specs/best-practices.md`. Key requirements:

- **Package structure**: Use `pyproject.toml` + `uv`; no `setup.py`, `requirements.txt`, or `MANIFEST.in`
- **Formatting**: `black` (line length 120) + `isort` (black profile)
- **Imports**: Always absolute; never relative or wildcard
- **Type annotations**: Annotate all function signatures; use `mypy` for static checking
- **Logging**: Use `logging` module with `%`-style formatting; never `print()`
- **Docstrings**: Sphinx field list notation (`:param:`, `:returns:`, `:raises:`)
- **Strings**: f-strings preferred; `%`-style only for logger calls
- **Dependency management**: `uv add` / `uv sync`; commit `uv.lock`
- **Testing**: `pytest` with 100% coverage target; tests in top-level `test/`
- **Target Python**: 3.12 on Ubuntu 20.04/22.04

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

## What You Should Always Do
- Ask for clarification before large refactors
- Prefer editing existing files over creating new ones
- Check for existing utilities before writing new ones
- Run linting/tests after changes

## What You Should Never Do
- Modify `package-lock.json` / `yarn.lock` directly
- Delete files without explicit instruction
- Push to remote branches
- Alter environment configs without confirmation

## Agent skills

### Issue tracker

Issues live in GitHub Issues for this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo — one `CONTEXT.md` + `docs/adr/` at the root. See `docs/agents/domain.md`.
