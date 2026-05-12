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

Prepare the baseline toy-client runtime image:

```bash
uv run test-farm prepare-runtime
```

This default check only verifies that the prepared image tag already exists.
It does not verify freshness against the current source tree.
Use `uv run test-farm prepare-runtime --force` to rebuild explicitly.

Run the baseline invocation:

```bash
uv run test-farm run <scenario.yaml> --controller-bind-address <host:port>
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
