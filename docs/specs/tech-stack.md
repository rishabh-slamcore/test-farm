# Tech Stack — test-farm

Technology choices for test-farm. Language-level defaults inherit from `.claude/best-practices.md`; this document records only what test-farm adds, specialises, or explicitly defers.

## Language & Platform

| | |
|---|---|
| Language | **Python** (3.8, 3.9, 3.10, 3.12) |
| OS | **Ubuntu 20.04 / 22.04** |
| Kernel features | `netem`, `tbf` qdiscs; `veth`; network namespaces |

## Packaging & Dependency Management

| | |
|---|---|
| Build config | `pyproject.toml` (no `setup.py`, no `requirements.txt`, no `setup.cfg`) |
| Dep manager | **uv** (`uv sync`, `uv add`, `uv run`) |
| Lock file | `uv.lock`, committed to VCS |

## Code Quality Tooling

| | |
|---|---|
| Formatter | **black** (line length 95) |
| Import sort | **isort** (black profile) |
| Type checker | **mypy** |
| Tests | **pytest**; tests in top-level `test/`; 100% coverage target |
| Logging | stdlib `logging`, module-level via `__name__`, `%`-style format strings |
| Docstrings | Sphinx field list (`:param:`, `:returns:`, `:raises:`) |

All four quality gates (`black`, `isort`, `mypy`, `pytest`) must pass before any work is considered complete.

## Runtime Components

### CLI

**Typer.** Leverages the mandatory type annotations so CLI entry points are declared by type-hinting a regular function. Inherits Click's ecosystem if we later need richer prompts or nested command groups.

### Scenario files

**PyYAML** for parsing + **pydantic** for schema validation. Scenario files must fail loudly with field-level errors on malformed input — never silently default or skip unknown keys.

### Toy update server (v1)

**FastAPI + uvicorn.** Serves the bundle, exposes a health endpoint and callback routes the Controller uses to observe client-side outcomes. Chosen for typed request/response, async support, and low ceremony.

## Client Isolation

A deliberate two-phase approach:

| Phase | Backend | Why |
|---|---|---|
| **v1 — toy HTTP** | **Docker** containers | Mature tooling, fast iteration, well-understood veth/netns wiring |
| **v2+ — Hawkbit** | **LXD** system containers (Canonical) | System-container semantics match the Slamcore Aware environment; LXD's REST API *may* remove the single-host Controller constraint — **open for validation** |

v1 drives Docker via `subprocess` + the `docker` CLI (no Python SDK dependency). The LXD integration mechanism (REST client library vs CLI subprocess) is a v2-phase decision.

## Network Impairment

`tc` with `netem` and `tbf` qdiscs on per-client veth interfaces. The invocation mechanism is **deferred**: prototype with `subprocess` calls to the `tc` CLI; revisit `pyroute2` if shelling out becomes a bottleneck or a correctness risk.

## Privileges

`tc`, `veth`, and `netns` operations require root or `CAP_NET_ADMIN`. How the Controller acquires them — assume-root, targeted `sudo` wrapper, or rootless via user namespaces — is **deferred**. The only firm commitment: the Controller must **fail fast with a clear error** if it cannot perform the operations it needs.

## Reporting

Format is **deferred**. Whatever is chosen must be:

- **Machine-readable** — consumable by CI and scripts without post-processing.
- **Per-client** — each client's outcome, timings, and failure mode recorded individually.
- **Impairment-aware** — records exactly what impairments were applied, and when, during the run.

Streamed human-readable logs during a run are a **v1 requirement**; they supplement, not replace, the final report.

## Rejected Alternatives

| Option | Reason |
|---|---|
| **ContainerLab** | Too heavyweight for the lifecycle and control model we need |
| **ToxiProxy** | Insufficient coverage of network-layer effects (jitter, bandwidth, reordering, duplication) |

## Deferred Decisions — Summary

| Decision | Status |
|---|---|
| Reporting format | Constraints pinned; concrete format TBD |
| `tc` invocation mechanism | Prototype with subprocess; revisit if needed |
| Privilege model | TBD; must fail fast when insufficient |
| LXD viability for multi-host Controller | To validate before the Hawkbit phase |
| v1 scale target | Concrete upper bound TBD (design doc baseline: 5+ clients) |
