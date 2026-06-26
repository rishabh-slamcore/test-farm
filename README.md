# Disruptor

Disruptor applies Linux `tc` network impairment to discovered Slamcore Aware
devices. It is intended for running real-device update-delivery scenarios from a
single host.

The CLI entry point is `disruptor`. From a repository checkout, run it through
`uv`:

```bash
uv run disruptor <scenario.yaml> --interface <nic>
```

For example:

```bash
uv run disruptor scenarios/warehouse-loss.yaml --interface wlan0
```

## Packaging

The installable distribution is `slamcore-disruptor`. It packages the
`disruptor` Python package and exposes these console scripts:

- `disruptor`: apply or dry-run network impairment from a scenario file.
- `device-discovery`: list Slamcore Aware devices discovered over mDNS.

It does not package the broader test-farm harness.

Build a wheel from the repo root:

```bash
uv build --wheel
```

Install the resulting wheel into the target environment, then run:

```bash
disruptor <scenario.yaml> --interface <nic>
```

When run without `--dry-run`, Disruptor:

1. Discovers Slamcore Aware devices with mDNS.
2. Resolves the scenario file against the discovered devices.
3. Deletes any existing root qdisc from the target interface.
4. Applies the rendered `tc` plan.
5. Waits until interrupted.
6. Cleans up the root qdisc when stopped with Ctrl-C.

## Setup

Install the project dependencies:

```bash
uv venv
uv sync --dev
```

Disruptor mutates `tc` state on the selected interface, so the `tc` binary needs
`CAP_NET_ADMIN`:

```bash
TC_BIN="$(readlink -f "$(which tc)")"
setcap cap_net_admin+ep "$TC_BIN"
getcap "$TC_BIN"
>>/usr/sbin/tc cap_net_admin=ep
```

The command above sets and then verifies the capability on the resolved `tc`
binary.

## Scenario Files

A Disruptor scenario file contains a `network_impairment` mapping with a required
`default` policy and optional ordered `overrides`.

This example shows every supported field:

```yaml
network_impairment:
  default:
    delay: 100ms
    loss: 2.5%
    bandwidth_limit: 10mbit
  overrides:
    - name: exact-device
      device_match:
        - sc-aware-10
        - sc-aware-11
      impairment:
        delay: 250ms

    - name: batch-by-name
      regex_match: "^sc-aware-2[0-9]$"
      impairment:
        loss: 25%

    - name: mk3b-bandwidth
      variant_match: mk3b
      impairment:
        bandwidth_limit: 1mbit

    - name: control
      device_match:
        - sc-aware-control
      impairment: none
```

### `network_impairment.default`

`default` is required. Every discovered device uses this impairment unless an
override matches first.

### `network_impairment.overrides`

`overrides` is optional. Overrides are evaluated in file order. The first
matching override wins for a device; later matching overrides are ignored for
that device.

Each override may set `name`. If omitted, Disruptor assigns `override-<index>`.
Names appear in dry-run output and warning messages.

Each override must set exactly one selector:

- `device_match`: non-empty list of exact discovered device ids.
- `regex_match`: regular expression matched against the discovered device id.
- `variant_match`: exact discovered variant, such as `mk3b`.

Each override must set `impairment`.

### Impairments

An impairment can be `none`, or a mapping with one or more of these fields:

- `delay`: duration string using `us`, `ms`, or `s`, such as `500us`, `100ms`,
  or `1.5s`.
- `loss`: percentage from `0` to `100`, either as a number or a string with `%`,
  such as `5` or `5%`.
- `bandwidth_limit`: rate string using `bit`, `kbit`, `mbit`, `gbit`, or `tbit`,
  such as `500kbit`, `10mbit`, or `1gbit`.

Use `impairment: none` for an explicitly unimpaired control device. Disruptor
renders that lane as a large `pfifo` queue rather than `netem` or `tbf`.

## Inspect A Plan

Use `--dry-run` to inspect discovery, policy resolution, warnings, and the exact
`tc` commands without applying them:

```bash
uv run disruptor scenarios/warehouse-loss.yaml --interface wlan0 --dry-run
```

Dry-run output starts with the target interface, lists each discovered device and
the policy selected for it, reports unmatched override selectors as warnings, and
then prints the rendered commands.

If no devices are discovered, dry-run reports `default policy applied` and shows
the default impairment commands without per-device filters.

## Device Discovery

Disruptor browses `_hawkbitc._tcp.local.` for Slamcore Aware devices. A service is
accepted when its TXT properties include:

- `vendor=slamcore`
- `product=aware`
- `variant` set to one of `mk2`, `mk3a`, `mk3b`, or `mk3c`

The device id is the first component of the service name, before the first dot.
The device IP is the first advertised service address.

Scenario selectors use the discovered `device_id`, IP, and `variant`.

Run standalone discovery from a repository checkout with:

```bash
uv run device-discovery
```

After installing the `slamcore-disruptor` wheel, run:

```bash
device-discovery
```

The command browses the same `_hawkbitc._tcp.local.` services used by Disruptor
and logs each accepted device name, address, and variant.

## How Scenarios Become `tc`

The README is intentionally focused on running Disruptor and writing scenario
files. See [Disruptor tc planning](docs/disruptor-tc-planning.md) for how
discovered devices are attached to the HTB tree, assigned classes and handles,
and converted into `tc` commands.
