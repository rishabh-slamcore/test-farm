## Core concepts

### Qdisc, class, filter — the three primitives

A **qdisc** (queueing discipline) is what sits between the kernel's networking stack and a network interface. When the kernel wants to send a packet out an interface, it doesn't write directly to the wire — it hands the packet to the interface's qdisc, and the qdisc decides when (and in what order, and whether at all) to actually transmit. By default every interface has a qdisc; you can see it with `tc qdisc show dev <nic>`, and on a fresh interface it's typically `pfifo_fast` or `fq_codel` depending on your kernel.

A **class** is a subdivision *inside* a classful qdisc. Think of a class as a named bucket of traffic with its own scheduling parameters (rate limits, priority, etc.) and, crucially, its own child qdisc. Classes only exist inside classful qdiscs — HTB has classes, netem doesn't.

A **filter** is the rule that decides which class a packet goes into. A filter is attached to a qdisc (or a class) and inspects packets — header fields, marks, whatever — and on a match it routes the packet to a specific class via `flowid`.

The composition is: a classful qdisc sits at the root, hosts one or more classes, each class can have a child qdisc (classful or classless), and filters attached above the classes steer packets in. That recursion is what gives you a *tree*.

### Classless vs classful qdiscs

A **classless qdisc** is a leaf — it processes packets according to its own algorithm and that's it. `pfifo`, `netem`, `tbf`, `sfq`, `fq_codel` are all classless. You can't attach children to them.

A **classful qdisc** is a container — it has classes inside it, and those classes can have child qdiscs. `htb`, `hfsc`, `prio`, `cbq` are classful.

The practical rule for you: HTB is the only classful qdisc you'll touch. Everything else in your list (netem, TBF, pfifo) is a leaf that you'll hang off an HTB class.

### Egress vs ingress

`tc` shapes **egress** — packets leaving an interface — and that's where almost all the interesting machinery lives. When you write `tc qdisc add dev eth0 root ...`, the word `root` means "the root of the egress qdisc tree on eth0."

There's also an *ingress qdisc*, but it's deliberately limited: it can only **police** (drop or mark) incoming packets, not queue or delay them, because the packet has already arrived — there's nothing to schedule. If you genuinely need to shape inbound traffic with delay/loss/bandwidth, you redirect ingress packets into an **IFB** (Intermediate Functional Block) device and apply a normal egress qdisc tree there. Your spec defers this to "if bidirectional shaping is added later," which is the right call.

The direction question for your project: shaping the *host's client NIC* on *egress* affects **host → client** traffic only. If a client uploads to the host, that's the *client's* egress and the *host's* ingress, and your tree won't touch it. Worth being explicit about in your design doc.

### Handles and class IDs

Every qdisc has a **handle** like `1:` (major number, colon, optional minor). Every class has an ID like `1:10` (same major as its parent qdisc, unique minor). The convention:

- `1:` is the handle of the root qdisc.
- `1:1` is typically the root class (the top of the class hierarchy inside that qdisc).
- `1:10`, `1:20`, `1:99` are children of `1:1`.
- A leaf qdisc attached *under* class `1:10` gets its own handle, conventionally `10:`.

So a tree might look like:

```
       1:    (htb qdisc, root)
        |
       1:1   (htb class, root class)
       / | \
   1:10 1:20 1:99   (htb classes, per-client + default)
    |    |    |
   10:  20:  99:    (leaf qdiscs: netem, netem, pfifo)
```

The `parent` argument in `tc` commands is how you express edges in this tree. `parent 1:` means "attach to the root qdisc." `parent 1:10` means "attach inside class 1:10."

---

## Qdiscs you'll actually use

### HTB (Hierarchical Token Bucket)

HTB is the classful skeleton. Its job in your design is *not* primarily to do bandwidth shaping (though it can) — it's to give you a tree of classes that filters can route packets into. Each class becomes a per-client lane.

The minimum HTB syntax you need:

```bash
tc qdisc add dev eth0 root handle 1: htb default 99
tc class add dev eth0 parent 1: classid 1:1 htb rate 1000mbit
tc class add dev eth0 parent 1:1 classid 1:10 htb rate 1000mbit
tc class add dev eth0 parent 1:1 classid 1:99 htb rate 1000mbit
```

Two things to notice. First, **every HTB class needs a `rate`** — HTB requires it even when you don't care about bandwidth limiting at that level. Set it generously (link speed) when you just want HTB as a routing skeleton. Second, the `default 99` on the root qdisc tells HTB: "any packet that doesn't match a filter goes into class 1:99." This is the default-class mechanism — more on that below.

HTB also has a `ceil` parameter (max rate including borrowing from parent), but for a routing-skeleton use case, `rate` alone is fine.

### netem

netem is your impairment engine. Classless. You attach it as a leaf under an HTB class:

```bash
tc qdisc add dev eth0 parent 1:10 handle 10: netem delay 100ms 20ms loss 1% reorder 25% 50% duplicate 0.5%
```

That single line says: add 100ms ± 20ms of delay, drop 1% of packets, reorder 25% of packets with 50% correlation, duplicate 0.5%. The delay form `100ms 20ms` is mean ± jitter (uniform by default; you can append a distribution like `normal`). Loss and duplication are independent probabilities. Reorder is subtler — it requires a non-zero delay to be meaningful, because reordering is implemented by sending some packets immediately while delaying others.

One gotcha worth knowing: **netem's jitter can produce out-of-order packets even without an explicit `reorder` option**, because if packet N gets a larger random delay than packet N+1, N+1 will pass it. If you want strict ordering with jitter, you have to use the `pfifo` form: `netem delay 100ms 20ms` followed by a child `pfifo` — though for your use case (simulating realistic networks) reordering under jitter is usually fine.

### TBF (Token Bucket Filter)

TBF is a bandwidth limiter. It maintains a bucket that fills with "tokens" at the configured `rate`; sending a packet costs tokens proportional to its size. If the bucket has tokens, packets flow at line rate up to the bucket size (`burst`). If the bucket is empty, packets wait.

```bash
tc qdisc add dev eth0 parent 1:10 handle 10: tbf rate 10mbit burst 32kbit latency 50ms
```

The three parameters interact:

- **`rate`** is the sustained throughput cap.
- **`burst`** (sometimes called `buffer`) is the bucket size — how much traffic can pass at line rate before throttling kicks in. Too small and you can't even achieve `rate` (because the kernel can't refill tokens fast enough at high rates due to timer granularity). Too large and the limiter is "loose" — long bursts get through unchecked. A common rule of thumb is `burst ≥ rate / HZ`, where HZ is your kernel timer frequency (usually 250 or 1000).
- **`latency`** is the maximum time a packet can sit in TBF's queue before being dropped. It implicitly bounds the queue size.

### Why TBF must come before netem in a chain

This is the gotcha that bites everyone, and it deserves unpacking because it's about how the pipeline composes.

A qdisc chain processes packets in order: packet enters the first qdisc, gets enqueued/dequeued, then enters the next. When you put TBF *before* netem (i.e., TBF closer to the root), the order of operations is:

1. Packet arrives, hits TBF first.
2. TBF rate-limits — packets stream out at the configured rate.
3. Stream then hits netem, which adds delay/loss/etc.

Net effect: a stream at `rate` bandwidth with the configured impairments. Sane.

If you reverse it — netem first, then TBF — the order is:

1. Packet arrives, hits netem.
2. netem applies delay. Crucially, **netem's internal queue can hold a large backlog of delayed packets**, and when their delay expires they're all dequeued *at once* into the next qdisc.
3. That burst hits TBF, which then has to absorb it. TBF's queue (sized by `latency`) overflows, packets get dropped en masse, and your "10 Mbps + 100 ms delay" link behaves like a lossy mess.

The reason it's specifically a *netem-then-TBF* problem and not a general qdisc-ordering issue is that netem releases delayed packets in clumps rather than smoothly. TBF in front of netem smooths the input first; netem in front of TBF gives TBF a chunky input it can't handle.

So: **TBF before netem, always**, when you need both. In an HTB-rooted tree, that means TBF is the child of the HTB class, and netem is the child of TBF. Wait — but TBF is classless, you say. How does TBF have a child?

The answer is that classless qdiscs in `tc` *do* have an implicit "inner" qdisc — for TBF, by default it's `bfifo`. You can replace that inner qdisc by adding a new qdisc with the TBF qdisc as the parent. So:

```bash
tc qdisc add dev eth0 parent 1:10 handle 10: tbf rate 10mbit burst 32kbit latency 50ms
tc qdisc add dev eth0 parent 10: handle 100: netem delay 100ms 20ms loss 1%
```

Notice the chain: `1:10` (HTB class) → `10:` (TBF) → `100:` (netem). Each child's `parent` is the previous level's handle.

### pfifo

pfifo is the simplest possible qdisc: a packet FIFO with a max length in packets. No impairment, no shaping, just queueing.

```bash
tc qdisc add dev eth0 parent 1:99 handle 99: pfifo limit 1000
```

Use it for two things in your design:

1. The default class (the "unmatched traffic" lane), so unimpaired traffic to non-target destinations passes through untouched.
2. A "no impairment" client class, when a client is configured but currently has no impairment active. Cleaner than removing and re-adding the class.

---

## Filters and classification

### `u32` filter — matching on IP header fields

`u32` is the workhorse filter. It does bitwise matching on offsets into the packet. The syntax is dense but the model is simple: specify an offset, a value, and a mask, and the filter matches if `(packet[offset:offset+len] & mask) == value`.

For destination IP, the common shorthand is `match ip dst <addr>`:

```bash
tc filter add dev eth0 parent 1: protocol ip prio 1 u32 \
    match ip dst 10.0.0.10/32 flowid 1:10
```

Breaking that down:

- `parent 1:` — attach to the root qdisc (filters attached at the root see all packets entering the qdisc tree).
- `protocol ip` — only consider IPv4 packets.
- `prio 1` — priority 1 (lower numbers match first, more on this below).
- `u32` — the filter type.
- `match ip dst 10.0.0.10/32` — the matching condition. The `/32` is the prefix length; `/24` would match a subnet.
- `flowid 1:10` — on match, send the packet to class `1:10`.

You can chain multiple `match` clauses for AND conditions: `match ip dst 10.0.0.10/32 match ip dport 443 0xffff` would match HTTPS traffic to that host.

### Filter priority (`prio`)

When multiple filters could match a packet, priority decides order. Lower `prio` is checked first; the first match wins. Filters with the same priority are checked in insertion order, but relying on insertion order is fragile — give every filter an explicit `prio`.

For your design: if every filter matches a unique destination IP, priorities don't matter functionally, but I'd still set them explicitly. It makes the intent readable and protects you if you ever add overlapping rules (e.g., a per-IP override on top of a per-subnet default).

### `flowid` — routing matched packets into a class

`flowid 1:10` is the "then" of the filter's if-then. The flowid must reference a class that exists in the parent qdisc. If you set `flowid` to a class that doesn't exist, the filter installs but matched packets get dropped — silently, in older kernels — so always create classes before filters.

### Default class behavior

HTB's `default <minor>` argument on the root qdisc tells it where to send packets that match no filter. If you don't set `default`, unmatched packets are sent directly to the network device, bypassing your tree entirely. That's *usually not what you want* — it makes troubleshooting confusing because some traffic flows through your shaping and some doesn't, depending on whether a filter matched.

Provision a default class explicitly:

```bash
tc qdisc add dev eth0 root handle 1: htb default 99
tc class add dev eth0 parent 1:1 classid 1:99 htb rate 1000mbit
tc qdisc add dev eth0 parent 1:99 handle 99: pfifo limit 1000
```

Now unmatched traffic goes through `1:99` → `99:` (pfifo), which is a no-op, and you can confirm via stats that *all* egress traffic is accounted for in your tree.

---

## Practical operation

### Building a qdisc tree — command order

The order matters because each command references the level above:

1. **Root qdisc**: `tc qdisc add dev eth0 root handle 1: htb default 99`
2. **Root class** (HTB requires this — it's the parent of all rate accounting): `tc class add dev eth0 parent 1: classid 1:1 htb rate 1000mbit`
3. **Per-client classes**: `tc class add dev eth0 parent 1:1 classid 1:10 htb rate 1000mbit` (and 1:20, 1:99, etc.)
4. **Leaf qdiscs** under each class: `tc qdisc add dev eth0 parent 1:10 handle 10: netem delay 100ms loss 1%`
5. **Filters** at the root, steering packets into classes: `tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip dst 10.0.0.10/32 flowid 1:10`

If you reverse the order — e.g., add a filter before its target class exists — the command fails with a less-than-helpful error. Always build top-down.

Putting it all together for two clients with different impairments:

```bash
# Root
tc qdisc add dev eth0 root handle 1: htb default 99
tc class add dev eth0 parent 1: classid 1:1 htb rate 1000mbit

# Client A: 10.0.0.10, 100 Mbps + 50ms delay + 1% loss
tc class add dev eth0 parent 1:1 classid 1:10 htb rate 1000mbit
tc qdisc add dev eth0 parent 1:10 handle 10: tbf rate 100mbit burst 32kbit latency 50ms
tc qdisc add dev eth0 parent 10: handle 100: netem delay 50ms loss 1%

# Client B: 10.0.0.20, 10 Mbps + 200ms delay, no loss
tc class add dev eth0 parent 1:1 classid 1:20 htb rate 1000mbit
tc qdisc add dev eth0 parent 1:20 handle 20: tbf rate 10mbit burst 32kbit latency 200ms
tc qdisc add dev eth0 parent 20: handle 200: netem delay 200ms

# Default class — unimpaired
tc class add dev eth0 parent 1:1 classid 1:99 htb rate 1000mbit
tc qdisc add dev eth0 parent 1:99 handle 99: pfifo limit 1000

# Filters
tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip dst 10.0.0.10/32 flowid 1:10
tc filter add dev eth0 parent 1: protocol ip prio 2 u32 match ip dst 10.0.0.20/32 flowid 1:20
```

Read that block top to bottom and you should now be able to predict every effect.

### Inspecting the live tree

Three commands cover everything:

```bash
tc qdisc show dev eth0       # all qdiscs on the interface
tc class show dev eth0       # all classes
tc filter show dev eth0      # all filters
tc -s qdisc show dev eth0    # with stats: bytes/packets/drops per qdisc
tc -s class show dev eth0    # with stats per class
```

The `-s` (statistics) flag is essential for debugging. It shows packets sent, bytes sent, drops, overlimits, and queue backlog per node. When something looks wrong, `tc -s` tells you whether traffic is reaching the class you expect (drop counters on the wrong class = your filters are misclassifying; overlimits on a TBF = your `burst` is too small).

Tip: pipe through `column -t` or just eyeball it; the output is verbose but consistent.

### Atomic teardown

```bash
tc qdisc del dev eth0 root
```

Deletes the root qdisc and *everything underneath it* — all classes, leaf qdiscs, filters. The interface reverts to its kernel-default qdisc (`pfifo_fast` or whatever). This is atomic: there's no half-torn-down state. You don't need to delete classes and filters individually.

### Idempotent teardown

If you run `tc qdisc del dev eth0 root` and there's no qdisc to delete, you get:

```
RTNETLINK answers: No such file or directory
```

with a non-zero exit code. For an idempotent teardown script, treat this as success. Two patterns:

```bash
tc qdisc del dev eth0 root 2>/dev/null || true
```

or, more careful — check first:

```bash
if tc qdisc show dev eth0 | grep -q '^qdisc htb 1:'; then
    tc qdisc del dev eth0 root
fi
```

The first is simpler. The second is better if you want to log "found stale tree, cleaning up" vs "nothing to do."

### Stale-state recovery

If a previous run crashed mid-setup, you might have a partial tree on the interface. The safest startup sequence:

1. Check current state: `tc qdisc show dev eth0`.
2. If anything other than the kernel default is present, tear it down: `tc qdisc del dev eth0 root`.
3. Build fresh.

The grep approach above generalizes: look for *your* qdisc handle (`1:`) or qdisc type (`htb`) to decide whether there's something to clear. Don't blindly delete the root qdisc on every startup if the interface might be shared with other tools — but in your case, the host's client NIC is yours alone, so unconditional `tc qdisc del dev eth0 root 2>/dev/null || true` followed by your setup is fine.

---

## Environment

### `CAP_NET_ADMIN`

`tc` modifies kernel networking state, so it requires `CAP_NET_ADMIN`. In practice that means running as root, or as a process with that capability granted explicitly. For a daemon you'll likely:

- Run it as root (simplest, common for network-management tools), or
- Run it as a non-root user with `CAP_NET_ADMIN` set on the binary via `setcap cap_net_admin+ep /path/to/binary`, or
- Use systemd's `AmbientCapabilities=CAP_NET_ADMIN` and `CapabilityBoundingSet=CAP_NET_ADMIN` so the service starts with only that capability and nothing else.

The third option is the most defensible — minimum privilege, no setuid binary, declarative in the unit file.

### Which direction `tc` shapes

Worth restating because it's the most common conceptual mistake: `tc qdisc add dev eth0 root ...` shapes packets *leaving* eth0. On a host with a client-facing NIC, that's host→client traffic. Client→host traffic is *unaffected* by anything you put on the host's eth0 egress.

If the test scenarios need to simulate degraded client uploads, you need IFB (see next).

### IFB (Intermediate Functional Block)

IFB is a virtual interface you create and route ingress traffic into. The flow:

1. Create an IFB device: `ip link add ifb0 type ifb && ip link set ifb0 up`.
2. Attach an ingress qdisc to your real interface: `tc qdisc add dev eth0 handle ffff: ingress`.
3. Add a filter on the ingress qdisc that redirects all matched packets to the IFB device: `tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0`.
4. Now build your normal egress qdisc tree on `ifb0` instead of `eth0`. Packets arriving on eth0 get redirected to ifb0's egress, where your HTB/netem/TBF tree shapes them.

You've correctly scoped this out of v1. When you do add it, the conceptual model is straightforward — it's the same tree, just attached to a virtual interface that intercepts ingress.

---

## A few things worth knowing that aren't on your list

These don't change your v1 design but they'll come up.

**Burst sizing and timer granularity.** TBF's `burst` parameter is in bytes (despite the example I gave above using `32kbit` — `tc` parses both, and it's confusing). At very high rates (gigabit+), the default burst is often too small and you get throughput well below the configured rate. Rule of thumb: `burst = rate / HZ`, where HZ is your kernel timer tick (usually 250 or 1000). For 1 Gbit and HZ=1000, that's 125 KB. The `latency` parameter is a separate ceiling on queue *time* and indirectly bounds queue size.

**Bytes vs bits in `tc` arguments.** `rate 10mbit` is megabits per second. `rate 10mbps` is megabytes per second. Always sanity-check — `mbit` is what you almost always want.

**netem's loss model is independent per packet by default.** Real-world loss is often bursty, not Bernoulli. netem supports `loss state` and `loss gemodel` for correlated loss models. Worth knowing exists; probably not needed for v1.

**`tc` errors are often cryptic.** "RTNETLINK answers: Invalid argument" usually means an HTB class is missing a required parameter (almost always `rate`), or a filter references a non-existent flowid. "No such file or directory" on a del means the object isn't there. "File exists" on an add means it already is. Keep these three in mind and 90% of debugging is fast.
