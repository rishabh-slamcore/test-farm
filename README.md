# test-farm

test-farm is a controlled harness for replaying Slamcore Aware update broadcast scenarios
under known network conditions.

## Getting started

This repository uses:

- `uv` for dependency management and command execution
- a local virtual environment at `.venv/`

## Setup

After cloning:

```bash
uv venv
uv sync --dev
uv run pre-commit install
```

Run the CLI placeholder:

```bash
uv run test-farm run
```

Run the local quality gates:

```bash
uv run pre-commit run
```

Optionally install the pre-commit hooks:

```bash
uv run pre-commit install
```

To run the hooks across the whole repo instead of just changed files:

```bash
uv run pre-commit run --all-files
```
