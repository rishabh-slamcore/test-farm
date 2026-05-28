# Mission — test-farm

## Vision

A controlled, network-impaired harness for Slamcore Aware update broadcasts — so reliability can be proven under realistic warehouse conditions, and named failure scenarios can be replayed on demand.

## Problem

Update distribution is a critical quality gate for warehouse deployments, where wifi congestion, device churn, and marginal links are common. Without a controllable, reproducible environment, broadcast reliability can only be validated in the field — where failures are expensive, slow, and hard to reproduce.

## Primary Goals

1. **Update reliability under real-world network conditions.** Validate that the update broadcast server ([SAW-2761](https://slamcore.atlassian.net/browse/SAW-2761)) delivers bundles to Slamcore Aware clients under systematic packet loss, latency, jitter, reordering, and bandwidth impairment.
2. **Scenario replay.** Provide a reproducible harness where named failure scenarios (`node_drop_off`, `node_timeout`, …) are expressed as YAML and re-run on demand.

## Users

Primary audience: **Slamcore engineers** validating broadcast-server changes and iterating on reliability fixes. The farm is developer-operated in v1. QA adoption and CI integration are explicit follow-ons, not v1 requirements.

## v1 Scope

- Single-host **Controller** driven as a **one-shot CLI**:
  `test-farm run scenario.yaml` → provision clients → execute scenario → report → tear down.
- **Toy HTTP server** as the update source, so the orchestration and impairment machinery can be validated independently of Hawkbit.
- **Fleet-wide static network impairment** at the **Router Container** via `tc` / `netem` / `tbf`: start with delay, loss, and bandwidth caps, then extend the same model to the remaining impairment options.
- **YAML scenario files** with baseline invocation fields plus an optional nested `network_impairment` mapping. Omitting `network_impairment` produces a baseline result with no intentional impairment.
- **Streamed per-client logs** to the operator during the run, plus a final machine-readable report.

## Post-v1 Direction

- Replace the toy HTTP source with a **Hawkbit client image** backed by **LXD system containers**. LXD's REST API is expected to relax the single-host Controller constraint — **pending validation** before that phase begins.
- CI integration once the local CLI is stable.

## Non-Goals

- **Device installation.** test-farm verifies receipt of an update bundle over the network. It will not flash or install on real devices, and will not extend `jetson_flash`.
- **Service discovery via mDNS.** Possible later with additional setup; not in scope.
- **Multi-host Controller in v1.** Single-host is an accepted v1 constraint. LXD may lift it in a later phase; until validated, assume single-host.
- **Production-grade test infrastructure.** test-farm is an internal reliability tool, not a customer-facing service.

## Guiding Principles

1. **Real-world fidelity.** Impairments must reflect what Slamcore Aware devices encounter in warehouses — wifi congestion, intermittent drop-off, constrained uplinks — not decorative defaults. A scenario that cannot reproduce a real field failure is not a valuable scenario.
2. **Low setup cost.** An engineer who has just cloned the repo should reach a successful scenario run in minutes. Every dependency, privilege, or manual step that gets between them and a green run is a cost to be justified.

## Success Criteria

- A new engineer can run a named scenario on their machine and get a deterministic, machine-readable verdict.
- At least one field-observed failure mode is reproduced as a replayable scenario.
- Reliability regressions in the broadcast server are caught before they reach customers.

## Open Questions

- **Reporting format.** Undecided. Whatever is chosen must be machine-readable and per-client.
- **v1 scale target.** The design doc suggests "5+ clients hopefully". A concrete upper bound is deferred until host constraints are measured.
- **LXD viability for multi-host.** An assumption to validate before the Hawkbit phase.
- **Privilege model.** `tc`, `veth`, and `netns` require root or `CAP_NET_ADMIN`. How the Controller acquires them is deferred.

## Rejected Alternatives

| Option | Reason |
|---|---|
| **ContainerLab** | Too heavy for the lifecycle and control model we need |
| **ToxiProxy** | Insufficient coverage of network-layer effects (jitter, bandwidth, reordering) |

## Related Work

- [SAW-2761](https://slamcore.atlassian.net/browse/SAW-2761) — standalone update broadcast server
- [SAW-2763](https://slamcore.atlassian.net/browse/SAW-2763) — this project
- `specs/design_controlled_test_farm.md` — design background and `tc` / `qdisc` / `veth` primer
