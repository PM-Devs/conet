# CoNET — Architecture & Implementation Plan

**Document 3 of the CoNET specification set.**

*How CoNET is built: topology, research spikes, architecture decisions, the four-stage plan, technology baseline, and benchmarks. Requirements are in the SRS; buildable detail is in the LLDs.*

---

## 1. Why research precedes implementation

> **The governing rule:** the main risk is not writing code too slowly — it is freezing the wrong contracts too early.

Registration, discovery, identity, authorization, task semantics, and cancellation all become foundational APIs. The plan therefore begins with short research spikes and Architecture Decision Records (ADRs), followed by a small vertical prototype. The manifest and adapter contracts (the most-depended-on of these) are already specified in LLD-01.

---

## 2. Reference topology

```
Organization (CoNET network)
├── Branch / Department A
│     ├── Agent A1
│     └── Agent A2
├── Branch / Department B
│     ├── Agent B1
│     └── Agent B2
└── Shared CoNET Control Plane
      ├── Agent Registry        ├── Task Control
      ├── Skill Registry        ├── Human Approval
      ├── Discovery             ├── Observability / Audit
      ├── Policy / Permissions   └── MCP Gateway
      └── Router / Runtime
```

---

## 3. Research workstreams

Each spike produces a decision artifact (an ADR or a versioned contract). Spikes may run in parallel where they do not depend on each other.

| Spike | Key question | Topics | Output |
|---|---|---|---|
| R1 — Network identity | How does an agent prove who it is? | mTLS/SPIFFE-style identity, JWT/service tokens, cert rotation, trust domains. | ADR: identity and trust model. |
| R2 — Registration & leases | How does an agent join and leave safely? | Lease/heartbeat models, TTLs, duplicate identities, graceful draining, stale records. | ADR: registration protocol and lease rules. |
| R3 — Skill contract | What exactly is a Skill? | Naming, versions, JSON Schema/Protobuf, streaming vs unary, side-effects, idempotency. | Skill Manifest v0.1 (see LLD-01). |
| R4 — Discovery | How do agents find providers? | Exact-name lookup first; metadata filtering; provider health; semantic search later. | ADR: discovery algorithm and cache rules. |
| R5 — Policy | Who may invoke which Skill? | RBAC vs ABAC, department/branch domains, resource attributes, deny precedence, explainable decisions. | ADR: authorization model. |
| R6 — Routing | How is one provider selected among many? | Health, priority, capacity, locality, sticky routing, failover, side effects. | ADR: routing policy v0.1. |
| R7 — gRPC runtime | How are tasks executed reliably? | Deadlines, cancellation propagation, health checking, streaming, retry, backpressure. | Protobuf/runtime contract. |
| R8 — Event plane | Which operations are events not RPCs? | Core NATS vs JetStream, delivery guarantees, consumer groups, subject naming, dedup. | ADR: event taxonomy and durability. |
| R9 — Task control | What does pause/cancel/disable mean? | Task state machine, cooperative cancellation, drain vs kill, retry safety. | Task lifecycle specification. |
| R10 — Human approval | Where should execution stop for review? | Policy-triggered approval, expiry, approver roles, resumption, rejection. | Approval workflow spec. |
| R11 — Observability | How can an operator reconstruct AI activity? | OpenTelemetry propagation, logs/metrics/traces, AI spans, MLflow integration, PII controls. | Telemetry schema + retention plan. |
| R12 — MCP gateway | How do many MCP servers become one managed boundary? | MCP client lifecycle, namespaces, credentials, capability import, policy mapping, server isolation. | Gateway architecture ADR. |
| R13 — Persistence | What must be durable? | MongoDB collections/indexes, optimistic concurrency, audit storage, task history. | Persistence schema v0.1. |
| R14 — Admin UX | What needs immediate operator control? | Agent topology, Skills, tasks, approvals, disable/drain, audit, MCP integrations. | Dashboard information architecture. |

---

## 4. Architecture decisions before v0.1

Fourteen decisions must be recorded before the vertical slice. Two are already committed (shown resolved); the rest are open and owned by their spike.

| ADR | Decision | Status / direction |
|---|---|---|
| ADR-001 | One control plane per organization, or multi-tenant? | **RESOLVED** — single-org, single-trust-domain for v0.1. |
| ADR-002 | Canonical agent identity and how it is authenticated? | Leaning mTLS per agent, single internal CA (R1). |
| ADR-003 | Mandatory fields in AgentManifest and SkillManifest? | **RESOLVED** — specified in LLD-01. |
| ADR-004 | Registry writes via HTTP/gRPC, events, or both? | Open (R2, R8). |
| ADR-005 | Source of truth for live health: lease, gRPC health, NATS heartbeat, or combination? | Open (R2, R7, R8). |
| ADR-006 | Which policy model, and the default-deny rule? | Leaning deny-by-default RBAC via Casbin (R5). |
| ADR-007 | How are provider ranking and failover made deterministic and explainable? | Open (R6). |
| ADR-008 | Which task types may be retried safely? | Governed by Skill side_effects/idempotency (R3, R9). |
| ADR-009 | What semantics does cancellation guarantee? | Leaning cooperative, propagated (R9). |
| ADR-010 | Which events require JetStream persistence vs Core NATS only? | Open (R8). |
| ADR-011 | How are trace IDs, task IDs, and idempotency keys propagated? | Leaning OpenTelemetry context (R11). |
| ADR-012 | How does the MCP gateway namespace capabilities from many servers? | Open (R12). |
| ADR-013 | How are MCP secrets isolated from agents and logs? | Gateway-only secret store, by reference (R12). |
| ADR-014 | What information is allowed in AI activity logs? | Data-minimized by default (R11, NFR-012). |

> **Recommended practice:** keep each ADR as its own one-page record (context → options → decision → consequences) in the repo, updated as its spike closes. The status column above is the running index.

---

## 5. Technology baseline (provisional)

Starting hypotheses, not irreversible decisions. Confirm each in its spike.

| Candidate | Role | Reason to evaluate |
|---|---|---|
| Python 3.12+ | Reference implementation and SDK | Strong async ecosystem; FastAPI/gRPC compatibility. |
| FastAPI | Control / admin API | Typed HTTP APIs and clean auth integration. |
| Pydantic | Manifests and API models | Validation and schema generation. |
| gRPC + Protobuf | Internal sync/streaming data plane | Typed contracts, streaming, deadlines, cancellation, health checking. |
| NATS Core + JetStream | Event plane | Core for ephemeral events; JetStream for events/tasks that must survive consumer outages. |
| MongoDB + PyMongo Async | Durable registry / config / task / audit | Matches current MongoDB + async Python preference. |
| Casbin (evaluate R5) | Policy prototype | RBAC/ABAC candidate; do not lock in until validated. |
| OpenTelemetry | Distributed telemetry | Trace context across HTTP, gRPC, messaging. |
| MLflow (optional) | AI-specific tracking/eval | Integration, not a dependency. |
| Official MCP SDK | Managed MCP gateway | Do not implement MCP wire behavior from scratch. |
| A2A SDK (later) | Candidate external/edge adapter | Adopt for external agent-to-agent delegation rather than reimplementing. |

---

## 6. Four-stage build plan

### Stage A — Architecture laboratory (throwaway prototypes)
Goal: answer foundational questions before committing package APIs.

- Prototype AgentManifest and SkillManifest with Pydantic (against LLD-01).
- Prototype a gRPC unary and a server-streaming Skill call; test deadline, cancellation, standard health checking.
- Run a small NATS setup; compare Core NATS events vs a durable JetStream stream.
- Prototype an agent lease: register, renew, expire, become undiscoverable.
- Prototype Casbin policies for org → department → agent → Skill (deny-by-default).
- Instrument one request across FastAPI → discovery → gRPC with OpenTelemetry.
- Connect one MCP server via the official SDK; map one tool to a temp Skill; confirm the credential never leaves the gateway.

**Exit criteria:** all ADRs required for the v0.1 slice are decided, with evidence from experiments.

### Stage B — v0.1 vertical slice
Goal: prove the idea end to end with the smallest useful network.

- CoNET control service with MongoDB persistence.
- Agent registration, lease renewal, unregister.
- Skill publication and exact-name discovery.
- Basic permission check (deny-by-default).
- Provider selection.
- Execution over gRPC.
- Task IDs, trace IDs, deadlines, cancellation.
- Lifecycle/task events to NATS.
- Minimal append-only audit event.
- CLI for network status, agent listing, task cancellation.

**Demonstration:** Agent A discovers `math.add` from Agent B and executes it without knowing B's endpoint beforehand — the SRS §10 acceptance test passes.

### Stage C — Enterprise control
- Department / branch / network zones.
- Full policy model with policy explanation.
- Human-approval workflow.
- Agent pause, disable, drain.
- Task cancellation and task history.
- Provider failover and capacity metadata.
- OpenTelemetry dashboards and the AI activity ledger.
- Admin web dashboard.

### Stage D — Managed external boundary
- Implement the logical CoNET MCP gateway.
- Connect multiple MCP servers through the gateway.
- Import and namespace approved MCP capabilities.
- Map CoNET policy to external capability invocation.
- Store external credentials only in the gateway secret mechanism.
- Trace and audit every external invocation.
- Disconnect one MCP server without affecting internal agents or other connections.

---

## 7. Research checklist by feature

| Feature | Questions to answer | Proof you understand it |
|---|---|---|
| Self-registration | Can agents renew a TTL lease? How are stale registrations removed? Duplicate identity? | Agent disappears from discovery automatically after missed renewals. |
| Self-discovery | What query shape is stable for v0.1 — exact name, tags, versions, metadata? | Provider can change without requester code changes. |
| Permissions | Can rules express department/agent/Skill/resource restrictions and explain denials? | Cross-department allow/deny scenarios pass predictable tests. |
| gRPC | How do deadlines, cancellation, health checks, streaming behave under failure? | Cancelling a task stops provider work where cooperative cancellation is implemented. |
| Event bus | Which events must be replayable? How do consumers deduplicate? | A durable consumer restarts and continues without duplicate business effects. |
| Routing | What happens with 0, 1, or N providers? How are unhealthy ones removed? | Routing decision is deterministic and logged. |
| Human approval | How does a paused task resume? What if approval expires? | High-risk task cannot execute before approval. |
| Observability | Which attributes identify network/agent/Skill/task? What must be redacted? | One task can be traced from requester to provider and back. |
| Agent stop/drain | How is new work blocked while existing work finishes? | Drained agent accepts no new tasks while unrelated activity continues. |
| MCP gateway | How are tool collisions, credentials, connection failures, per-agent permissions handled? | Internal agent uses an external capability without handling MCP credentials. |

---

## 8. Benchmark plan

**Do not choose performance targets from intuition.** Build a baseline and measure:

- Registry read/write latency.
- Discovery p50/p95/p99 with 10, 100, 1,000, and 10,000 registered providers.
- Policy evaluation latency.
- gRPC overhead for small unary calls, large payloads, and streaming.
- Cancellation propagation time.
- Lease-expiry accuracy and registry convergence.
- NATS event throughput and redelivery behavior.
- MongoDB index performance for `network_id + skill_id + status` queries.
- Trace overhead with sampling enabled.
- Failover time after a provider becomes unhealthy.

---

## 9. Target repository layout (after ADRs stabilize)

Do not create all of these on day one; this is the target separation once the ADRs settle.

```
conet/
├── sdk/            # manifests, registration, discovery, task client
├── control/        # registry, policy, approvals, admin API
├── runtime/        # routing, execution, cancellation, failover
├── protocols/
│     ├── grpc/
│     └── events/
├── gateway/
│     └── mcp/
├── observability/
├── persistence/
├── cli/
└── examples/
```

---

## 10. Definition of ready to implement

Begin production implementation only when all of the following are true:

- Agent and Skill manifests are defined and versioned (LLD-01 — done).
- Identity / trust model is selected (v0.1: single trust domain, mTLS, operator bootstrap).
- Registration / lease behavior is written down.
- Authorization model and deny behavior are defined (deny-by-default RBAC).
- Task state machine and cancellation semantics are defined.
- gRPC contract style is chosen.
- Event taxonomy and durability rules are chosen.
- MongoDB source-of-truth collections and indexes are sketched.
- Trace / audit identifiers are standardized.
- MCP gateway is clearly separated from internal communication.
- At least one architecture-laboratory prototype validates the riskiest decisions.

---

## 11. Reference sources

**Positioning (verify before committing dependent architecture):**

- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/) — current agent-to-agent standard; discovery via Agent Cards, task lifecycle.
- [Governance Gaps in Agent Interoperability Protocols (arXiv 2606.31498)](https://arxiv.org/abs/2606.31498v1) — governance as a missing architectural layer above MCP/A2A.
- [MCP Official SDKs](https://modelcontextprotocol.io/docs/sdk) — for the managed gateway; do not reimplement the wire protocol.

**Implementation references:**

- [gRPC Health Checking](https://grpc.io/docs/guides/health-checking/)
- [gRPC Deadlines](https://grpc.io/docs/guides/deadlines/)
- [gRPC Cancellation](https://grpc.io/docs/guides/cancellation/)
- [NATS Clustering](https://docs.nats.io/running-a-nats-service/configuration/clustering)
- [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
- [Casbin RBAC](https://www.casbin.org/docs/rbac)
- [MLflow](https://mlflow.org/docs/latest/)
- [PyMongo Async migration](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/)
