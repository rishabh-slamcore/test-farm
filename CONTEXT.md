# Test Farm

test-farm is a controlled harness for replaying Slamcore Aware update broadcast scenarios under known network conditions.

## Language

**baseline result**:
The result produced by an invocation with no intentional network impairment, used to prove the harness lifecycle, update transfer, logging, reporting, and teardown work before reliability scenarios are introduced.
_Avoid_: Baseline Run, simple impairment result, smoke test

**invocation**:
A single invocation of `test-farm run` from environment provisioning through final report and teardown.
_Avoid_: Run, session, test, job

**invocation_instance**:
A monotonically increasing local invocation number allocated from existing result filenames, starting at 1 when no results exist.
_Avoid_: Run ID, timestamp ID, random ID, UUID

**Result File**:
The machine-readable report artifact for one invocation, named with the invocation_instance it records.
_Avoid_: State file, log file

**Client ID**:
The stable name assigned to one client instance within an invocation, used to correlate receipts, logs, and report entries.
_Avoid_: Docker container ID, hostname

**Bundle ID**:
The identifier of the update bundle the client is expected to download and verify during an invocation.
_Avoid_: Filename, URL, artifact path

**Scenario File**:
A developer-authored YAML file describing the intended invocation shape.
_Avoid_: Bundle manifest, result file

**Receipt**:
A client-posted outcome observation delivered to the Controller over the Receipt Channel.
For a successful download it includes a reported bundle; the Controller then derives the final client outcome.
_Avoid_: Verified receipt, server-side delivery, HTTP 200 success

**Client Status**:
The terminal outcome recorded for one client in a Result File.
_Avoid_: HTTP status, process exit code

**Invocation Status**:
The overall outcome of an invocation, derived from its Client Status values.
_Avoid_: Partial success, manual verdict

**Receipt Channel**:
A test-farm-owned path for clients to report their final update outcome to the Controller independently of the Update Server.
_Avoid_: Hawkbit status, server logs, shared host files

**Controller Bind Address**:
The local interface and port where the Controller listens for Receipt Channel HTTP requests.
The **Controller Reportback URL** is derived from this by adding the `http://` scheme for clients posting Receipts to the Receipt Channel.
_Avoid_: Implicit port, auto-detected listener

**Update Server**:
The server role that makes an update bundle available to clients during an invocation, implemented by a toy server in v1 and Hawkbit in a later phase.
_Avoid_: Update source, file server

**Router Container**:
The network node between the Update Server network and client network where fleet-wide network impairment is applied.
_Avoid_: Per-client veth shaper, NAT gateway

**Explicit Routing**:
A routed test-network model where server and client subnets reach each other through the Router Container without rewriting packet addresses.
_Avoid_: NAT, masquerading

**Bundle File**: The host-side file whose bytes define the expected bundle for an invocation. The host derives `byte_count` and `checksum` from the Bundle File, and the Update Server serves those same bytes to clients.
_Avoid_: Manifest file, fixture, artifact path, generated bundle


## Relationships

- A **baseline result** precedes impaired scenario results in the roadmap.
- A **baseline result** exercises the same harness lifecycle as later reliability scenarios, but without applying network impairment.
- A **baseline result** is successful only when every required client produces a successful **Receipt** outcome after Controller validation.
- An **invocation** has exactly one **invocation_instance**.
- An **invocation_instance** is allocated by scanning existing **Result Files** and incrementing the highest recorded number.
- A **Result File** is JSON named `result_<invocation_instance>.json`.
- A **Result File** records invocation timing, scenario file, overall status, expected bundle metadata, and per-client outcomes.
- Milestone 1 requires a final **Result File** and post-completion client log capture for failures; live log streaming is deferred.
- An **invocation** tears down its containers and Docker networks by default, unless `--keep-containers` is set for debugging.
- An **invocation** has one or more **Client IDs**.
- **Client IDs** are Controller-generated from the requested client count as `client-001`, `client-002`, and so on.
- A **Receipt** identifies the **invocation_instance**, **Client ID**, and **Bundle ID** it belongs to.
- The v1 **Bundle ID** is required and defaults to `baseline`.
- The v1 **Scenario File** declares only the client count; bundle size and checksum come from the Update Server manifest.
- The Controller passes invocation_instance, Client ID, Update Server URL, the **Controller Reportback URL** derived from the **Controller Bind Address**, and Bundle ID to each client container as environment variables.
- Milestone 1 requires an explicit **Controller Bind Address**.
- The Controller and Update Server expose `GET /health` for readiness and debugging.
- The Receipt Channel receives `POST /invocations/{invocation_instance}/clients/{client_id}/receipt`.
- The Update Server serves `GET /bundles/{bundle_id}/manifest` and `GET /bundles/{bundle_id}`.
- Client containers receive `TEST_FARM_INVOCATION_INSTANCE`, `TEST_FARM_CLIENT_ID`, `TEST_FARM_UPDATE_SERVER_URL`, `TEST_FARM_CONTROLLER_REPORTBACK_URL`, and `TEST_FARM_BUNDLE_ID`.
- A **Receipt** is delivered over the **Receipt Channel**.
- A **Client Status** is one of `success`, `download_failed`, `checksum_mismatch`, or `timed_out`.
- An **Invocation Status** is `success` only when every expected client has **Client Status** `success`; otherwise it is `failed`.
- An **Update Server** provides the bundle that clients use to produce a **Receipt**.
- A **Router Container** connects the Update Server network and client network using **Explicit Routing**.
- A **Router Container** applies impairment uniformly to client-bound update traffic.
- Milestone 1 includes the **Router Container** for forwarding but performs no `tc` operations.
- Milestone 1 uses separate runtime images/containers for the Router Container, Update Server, and client.
- The Controller remains a host-side process in Milestone 1.

## Example Dialogue

> **Dev:** "Should the first milestone inject latency so we know `tc` works?"
> **Domain expert:** "No — first produce a **baseline result** that proves the harness can move a bundle without impairment."

> **Dev:** "Can the invocation_instance be a timestamp so it is globally unique?"
> **Domain expert:** "No — an **invocation_instance** is the next number after the latest **Result File**, so reports are easy to read and compare."

> **Dev:** "Should client identity come from Docker container IDs?"
> **Domain expert:** "No — the Controller assigns **Client IDs** like `client-001` and uses them across receipts, logs, and reports."

> **Dev:** "Do we need a Bundle ID when v1 only has one bundle?"
> **Domain expert:** "Yes — use `baseline` so receipts and reports prove which expected bundle was verified."

> **Dev:** "Should scenario authors specify bundle size and checksum?"
> **Domain expert:** "No — the **Scenario File** expresses invocation intent, while the Update Server manifest provides verification metadata."

> **Dev:** "Should clients read the scenario file?"
> **Domain expert:** "No — the Controller passes each client's configuration as environment variables."

> **Dev:** "The toy server returned HTTP 200 for the bundle. Is that enough?"
> **Domain expert:** "No — the client must produce a **Receipt**, and the Controller decides whether it counts as success."

> **Dev:** "Should the Result File record raw HTTP status codes as client outcomes?"
> **Domain expert:** "No — record a **Client Status** and keep raw details in an error field."

> **Dev:** "Can an invocation be partially successful?"
> **Domain expert:** "No — the **Invocation Status** is derived as `success` only when all expected clients succeed."

> **Dev:** "Can the Controller infer success from Hawkbit or toy-server logs?"
> **Domain expert:** "No — clients report outcomes through the **Receipt Channel** so verification stays under test-farm's control."

> **Dev:** "Can clients post receipts to localhost?"
> **Domain expert:** "No — clients post to the **Controller Reportback URL** derived from the **Controller Bind Address**, and it must be reachable from their container network."

> **Dev:** "Can the Controller choose its receipt port automatically?"
> **Domain expert:** "No — Milestone 1 uses an explicit **Controller Bind Address**."

> **Dev:** "Should readiness use `/healthz`?"
> **Domain expert:** "No — use `GET /health` for both the Controller and Update Server."

> **Dev:** "Where does a client post its receipt?"
> **Domain expert:** "To `POST /invocations/{invocation_instance}/clients/{client_id}/receipt` on the **Controller Reportback URL** derived from the **Controller Bind Address**."

> **Dev:** "Does the first baseline result need live client log streaming?"
> **Domain expert:** "No — Milestone 1 needs the final **Result File** and failure log capture; live streaming can follow later."

> **Dev:** "Should failed invocations leave containers running for inspection?"
> **Domain expert:** "No by default — use `--keep-containers` when debugging requires preserving them."

> **Dev:** "Should we call the toy HTTP server an update source?"
> **Domain expert:** "No — use **Update Server**, because the same role is later filled by Hawkbit."

> **Dev:** "Can the Update Server see which client downloaded the bundle?"
> **Domain expert:** "Yes — use **Explicit Routing** through the **Router Container**, not NAT."

> **Dev:** "Should the first baseline result configure `tc` even without impairment?"
> **Domain expert:** "No — Milestone 1 proves routing and verification only; `tc` starts in the impairment milestone."

> **Dev:** "Can the toy Update Server and client share one image?"
> **Domain expert:** "No — keep router, Update Server, and client as separate runtime images so later replacements stay clean."

## Flagged Ambiguities

- "first milestone" initially included a simple impairment; resolved: the first milestone produces a **baseline result** with no intentional impairment.
- "run" was used for a `test-farm run` execution; resolved: use **invocation**.
- "run id" could mean a timestamp, UUID, random identifier, or hidden state counter; resolved: use **invocation_instance**, allocated by scanning **Result Files** named `result_<invocation_instance>*`.
- "bundle id" could mean a filename, URL, storage path, or unnecessary v1 concept; resolved: **Bundle ID** is required, identifies the expected update bundle independently of where it is served, and defaults to `baseline` in v1.
- "scenario file" could include artifact verification metadata; resolved: the v1 **Scenario File** declares client count only, while bundle size and checksum come from the Update Server manifest.
- "client id" could mean Docker's generated container ID or hostname; resolved: **Client ID** is a test-farm-assigned stable client name within an **invocation**.
- "successful update" could mean server-side response or client-side receipt; resolved: success requires a Controller-validated successful **Receipt**.
- "results" could mean source-server state, Hawkbit state, logs, or client callback; resolved: test-farm client outcomes flow through the **Receipt Channel**.
- "controller advertise URL" referred to where clients post receipts; resolved: use **Controller Reportback URL** only as the URL derived from the **Controller Bind Address**.
- "controller port" could be implicit or auto-selected; resolved: use an explicit **Controller Bind Address** in Milestone 1.
- "update source" and "toy server" referred to the server role that provides bundles; resolved: use **Update Server**.
- "router" could mean a NAT gateway or a routed impairment node; resolved: the **Router Container** uses **Explicit Routing**, not NAT.
