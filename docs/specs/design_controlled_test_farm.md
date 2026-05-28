# Design of a Controlled Test Farm for Update Broadcasting

> **Related:** [SAW-2763](https://slamcore.atlassian.net/browse/SAW-2763)

---

## Ask

Set up a dedicated farm of Slamcore Aware boxes to test update distribution. Ideally we want them on a dedicated network which we can "disturb" at will for testing reliability scenarios.

---

## Context and Background

Update distribution reliability is a critical quality gate for warehouse deployments where wifi conditions, congestion, and device churn are common. Work in [SAW-2761](https://slamcore.atlassian.net/browse/SAW-2761) will introduce a standalone update broadcast server.

---

## Decision Problem

How should we design a controllable test farm to validate broadcasting reliability while enabling systematic network impairment and collect telemetry?

---

## Network Impairment via `tc`, `qdiscs`, and Virtual Interfaces

### `qdisc` — Queueing Discipline

A `qdisc` is a kernel object attached to a network interface that controls what happens to packets before they leave that interface. In Linux, outgoing traffic passes through a queue first, and the qdisc defines the rules for that queue.

A qdisc can control:
- In what order packets are sent
- Whether some packets wait longer than others
- Whether packets are dropped
- Whether bandwidth is limited
- Whether delay, jitter, or loss is injected

**Why queues exist:** Suppose an application wants to send 100 MB immediately, but the link can only transmit at 10 Mbps. The kernel cannot emit all packets at once — they need to be buffered and scheduled over time. That scheduling point is where the qdisc lives.

### `tc` — Traffic Control CLI

`tc` is a CLI to attach queueing disciplines, classify packets, and apply shaping, delay, loss, etc.

```bash
# netem (Network Emulator) qdisc

# Fixed latency
tc qdisc add dev eth1 root netem delay 100ms

# Latency with jitter
tc qdisc add dev eth1 root netem delay 100ms 20ms

# Latency with jitter — gaussian distribution
tc qdisc add dev eth1 root netem delay 100ms 20ms distribution normal

# Packet loss — random
tc qdisc add dev eth1 root netem loss 5%

# Packet loss — consecutive, correlated 25%
tc qdisc add dev eth1 root netem loss 5% 25%

# Packet reordering (25% sent immediately, rest delayed 10ms)
tc qdisc add dev eth1 root netem delay 10ms reorder 25% 50%

# Packet duplication
tc qdisc add dev eth1 root netem duplicate 1%

# Packet corruption (bit flips)
tc qdisc add dev eth1 root netem corrupt 0.1%

# Bandwidth limit — chain TBF + netem
# Step 1: attach TBF as root (rate limiter)
tc qdisc add dev eth1 root handle 1: tbf rate 1mbit

# Step 2: attach netem as child of TBF (adds delay/loss on top of rate-limited packets)
tc qdisc add dev eth1 parent 1:1 handle 10: netem delay 100ms loss 5%
```

**Why TBF before netem?**

```
application sends packet
        ↓
tbf (handle 1:)
  - enforces rate limit
  - holds tokens, releases packet when token available
  - smooths burst traffic into steady stream
        ↓
netem (handle 10:, child of 1:1)
  - takes the rate-limited stream
  - adds delay / jitter / loss on top
        ↓
physical NIC
```

Applying netem first and then rate-limiting would cause the two delays to interfere destructively.

### `veth` — Virtual Ethernet Pairs

A **veth** is a virtual Ethernet pair: two linked virtual interfaces that behave like the ends of a cable. Packets sent on one end appear on the other.

**Why veth is useful for containers:**

Containers are isolated using network namespaces (each with its own interfaces, routing table, ARP table, and firewall state). To connect a container namespace to the host network, Linux creates a veth pair:
- One end appears inside the container as `eth0`
- The other end remains in the host namespace and is attached to a bridge

This is the standard networking pattern used by Docker and many other container runtimes.

**Why veth matters for traffic control:**

Each end of a veth pair is a normal network interface, so `tc` can be applied to it. If each container has its own veth interface, network impairments can be applied on a **per-container** basis.

**Client isolation:**

If all clients run as processes in the host network namespace sharing the same host interface, `tc` can only shape the common path — per-client isolation is not possible. The essential mechanism requires:
- One network namespace per client
- One veth pair per client
- `tc` applied to the relevant virtual interfaces

> Containers are simply a convenient packaging of network namespace, filesystem, and process isolation — they are not strictly required.

---

## Goal

Introduce a **Controller** component responsible for orchestrating the test environment. Core responsibilities:

- Provisioning and managing the required number of client instances
- Applying pre-decided fleet-wide network impairment rules at the router container
- Triggering the update broadcast process
- Collecting and reporting metrics on successful and failed executions

**Downside:** The Controller is a single host process, so it is limited by the capabilities of a single host to run client containers. Multi-host Controllers would be more complicated to implement.

---

## Validation Plan

Build a lightweight prototype using toy HTTP servers. This prototype can exercise representative scenarios such as bandwidth throttling and injected packet latency, allowing us to validate the orchestration model and impairment approach before investing in a more complete Hawkbit-focused test framework.

### If Validation Succeeds — Follow-up Steps

1. **Build a Docker Image for a Hawkbit Client**
   Create a Docker image containing the Hawkbit client components and all dependencies required to register with Hawkbit, receive a deployment, and download and store a bundle.

2. **Experiment with network impairment using Docker**

3. **Implement a Controller for Download Orchestration and Container Lifecycle**
   Develop a controller that creates test targets, triggers downloads, applies scenario settings, and manages the lifecycle of containers in each test run.

---

## Out of Scope

- **Device installation:** These tests are limited to receiving a file over the network onto a Slamcore Aware device. This is not an attempt to add a devcontainer-like setup to `jetson_flash`.
- **Service discovery via mDNS:** Could be attempted later with additional setup, but not addressed here.

---

## Scenario File Direction

The next **Scenario File** expansion should use an optional nested `network_impairment` mapping for static fleet-wide impairment at the router container.

The first stage should cover:

- Delay
- Loss
- Bandwidth limit

A second stage can extend the same nested mapping with:

- Jitter and delay distribution
- Loss correlation
- Reordering
- Duplication
- Corruption

Time-based action sequences are out of scope for this direction.

---

## Implementation Details

| Question | Answer |
|---|---|
| Target client count? | Hopefully 5+ clients; limited by single host capacity |
| How does Controller know download succeeded/failed? | Via server callback/health check/timeout (TBD) |
| Network metrics collection? | TBD |
| How does Controller address individual containers? | Simple naming scheme — no need to track specific devices |
| Should Controller itself be a container? | No — a host process is sufficient |
| Other options explored? | ContainerLab (too complex), ToxiProxy (insufficient effect coverage) |


## Router network
# Router Container — Network Impairment Architecture

## Overview

The test farm uses a **router container** as a dedicated network impairment node
sitting between the Hawkbit update server and the client containers. All update
traffic flows through the router container, making it the single point where
network conditions can be controlled for the entire client fleet.

```
┌──────────────┐       ┌─────────────────────┐       ┌─────────────────────┐
│    Server    │       │  Router Container   │       │   Client Containers │
│              │       │                     │       │                     │
│  (Hawkbit)   ├─eth0──┤ eth0        eth1 ───┼─eth1──┤ client1             │
│              │       │       (tc here)     │       │ client2             │
│              │       │                     │       │ client3  ...        │
└──────────────┘       └─────────────────────┘       └─────────────────────┘
  server network         two NICs, one foot            client network
                         in each network
```

The router container has two network interfaces: one facing the server network
and one facing the client network. `tc` rules are applied on the client-facing
interface (`eth1`) to inject network impairments. Since update traffic is
predominantly server-to-client (downloads), shaping the egress of `eth1`
covers the primary use case cleanly.

---

## Why a Router Container

### Alternative: per-client veth impairment

It is possible to apply `tc` directly on the host-side veth interface of each
client container. This enables per-client independent impairment but introduces
significant complexity:

- The Controller must track the host-side veth name for every client container
- Impairment of download traffic (ingress from the client's perspective) requires
  an IFB (Intermediate Functional Block) device to redirect ingress packets to a
  virtual egress interface before `tc` can be applied
- Per-client setup and teardown adds operational overhead

### Why the router container is simpler

By placing a router container in the network path, impairment is applied on
**egress toward the client network** — a straightforward `tc` operation requiring
no IFB workarounds. From the kernel's perspective, packets leaving the router
container's `eth1` are outgoing traffic, and `tc netem` attaches directly.

The tradeoff is that all clients are impaired uniformly. The router container
approach cannot independently impair individual clients. For the primary goal of
validating broadcast reliability under general network stress, uniform impairment
is sufficient.

---
