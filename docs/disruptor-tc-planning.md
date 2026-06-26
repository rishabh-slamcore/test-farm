# Disruptor tc planning

This document explains how Disruptor converts a scenario file and discovered
devices into Linux `tc` commands.

The operator workflow and scenario-file reference live in
[the README](../README.md).

Read (./tc-reference.md) first to understand `tc` concepts used in this doc.
## Planning Inputs

The planner receives:

- the target interface name, such as `wlan0`
- the discovered devices
- the parsed Disruptor scenario

Discovery returns Slamcore Aware devices sorted by `device_id`. The planner uses
the order it receives when assigning handles and class ids.

## Root HTB Tree

Every plan starts with the same HTB root:

```bash
tc qdisc add dev <nic> root handle 1: htb default 99
tc class add dev <nic> parent 1: classid 1:1 htb rate 1000mbit
tc class add dev <nic> parent 1:1 classid 1:99 htb rate 1000mbit
tc qdisc add dev <nic> parent 1:99 handle 99: pfifo limit 10000
```

Class `1:1` is the parent for per-device classes. HTB is not used as the
rate-limiting mechanism here; the high `1000mbit` class rate keeps it out of the
way for normal Disruptor scenarios.

Class `1:99` is the root qdisc fallback. Traffic that is not matched by a
per-device filter follows this fallback lane.

## Device Classes

Each discovered device gets one HTB class under `1:1`.

Disruptor assigns class minors in tens using device order:

| Device order | Class id | Qdisc handle |
| --- | --- | --- |
| 1 | `1:10` | `10:` |
| 2 | `1:20` | `20:` |
| 3 | `1:30` | `30:` |

For a device with id `sc-aware-10` and IP `192.0.2.10`, the planner adds:

```bash
tc class add dev wlan0 parent 1:1 classid 1:10 htb rate 1000mbit
tc qdisc add dev wlan0 parent 1:10 handle 10: <leaf-qdisc>
tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dst 192.0.2.10/32 flowid 1:10
```

The filter is destination-IP based. Packets leaving the Disruptor host for that
device IP are sent into the device class.

Duplicate discovered device ids are rejected before command rendering because
they would require the same class id and handle assignment.

## Policy Resolution

For each device, the planner checks scenario overrides in file order.

The first override whose selector accepts the device supplies the device policy.
If no override matches, the device uses `network_impairment.default`.

Dry-run output reports the selected policy name for each device:

```text
sc-aware-10 192.0.2.10 -> high-loss
sc-aware-11 192.0.2.11 -> default
```

An override selector that matches no discovered device becomes a warning. It
does not stop plan construction.

## Leaf Qdiscs

The resolved impairment determines the qdisc attached to the device class.

### No Impairment

`impairment: none` and an empty impairment model both render as `pfifo`:

```bash
tc qdisc add dev wlan0 parent 1:10 handle 10: pfifo limit 10000
```

Use this for explicit control devices that should stay unimpaired while other
devices are impaired.

### Delay And Loss

Delay and loss use `netem`:

```bash
tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms loss 5%
```

If only one field is set, only that `netem` argument is rendered.

### Bandwidth Only

Bandwidth-only impairment uses `tbf`:

```bash
tc qdisc add dev wlan0 parent 1:10 handle 10: tbf rate 1mbit burst 6000 latency 50ms
```

The burst is computed from the selected interface MTU and target rate. It is at
least four MTUs and is validated to avoid very large free-pass windows.

### Bandwidth With Delay Or Loss

When `bandwidth_limit` is combined with delay or loss, Disruptor renders `tbf`
first, then attaches `netem` below it:

```bash
tc qdisc add dev wlan0 parent 1:10 handle 10: tbf rate 1mbit burst 6000 latency 50ms
tc qdisc add dev wlan0 parent 10: handle 10:1 netem delay 100ms loss 5%
```

This preserves bandwidth limiting while still applying `netem` behavior.

## No Devices Discovered

If discovery returns no devices, the planner creates one default impairment node
without a device and without destination-IP filters.

Dry-run output reports:

```text
default policy applied
```

The rendered plan still includes the root HTB fallback lane plus a default
impairment class, for example:

```bash
tc class add dev wlan0 parent 1:1 classid 1:10 htb rate 1000mbit
tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms
```

Because no device IPs were discovered, no `tc filter add ... match ip dst`
commands are rendered.

## Apply And Cleanup

Before applying a plan, Disruptor deletes the existing root qdisc from the
target interface. It then runs each rendered command in order and waits.

When the process is interrupted, Disruptor deletes the root qdisc again during
cleanup. The same cleanup path runs if command execution fails after application
has started.
