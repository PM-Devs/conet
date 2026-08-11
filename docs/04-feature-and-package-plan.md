# CoNET — Document 4 — Feature-by-Feature Build & Package Plan

*Every feature, its packages, its build stage, and where each dependency belongs*

> The bridge from spec to code. For each feature: what it does, the Python packages to build it with, what it depends on, and which stage it lands in. Includes the two new subsystems — human accounts/teams and the operator dashboard.

**Consistent doc set · v0.1→v1 · Draft, 10 August 2026**

## Contents

- [0. How to read this document](#0-how-to-read-this-document)
- [1. Package map at a glance](#1-package-map-at-a-glance)
- [2. Where MLflow and OpenTelemetry each belong](#2-where-mlflow-and-opentelemetry-each-belong)
- [3. Agent-side features](#3-agent-side-features)
  - [F1  Manifests & Skill contract](#f1-manifests-skill-contract)
  - [F2  Registration & lease](#f2-registration-lease)
  - [F3  Discovery](#f3-discovery)
  - [F4  gRPC runtime & execution](#f4-grpc-runtime-execution)
  - [F5  Routing, retry & failover](#f5-routing-retry-failover)
  - [F6  Agent policy (RBAC)](#f6-agent-policy-rbac)
  - [F7  Human approval workflow](#f7-human-approval-workflow)
  - [F8  Health & lifecycle](#f8-health-lifecycle)
  - [F9  Persistence](#f9-persistence)
  - [F10  Event plane](#f10-event-plane)
  - [F11  Observability & audit (live)](#f11-observability-audit-live)
  - [F12  MCP gateway (external boundary)](#f12-mcp-gateway-external-boundary)
  - [F13  Evaluation (offline, optional)](#f13-evaluation-offline-optional)
- [A. Human accounts, teams & roles  (NEW)](#a-human-accounts-teams-roles-new)
  - [A1  Human authentication](#a1-human-authentication)
  - [A2  Teams & human roles](#a2-teams-human-roles)
- [B. The operator dashboard  (NEW)](#b-the-operator-dashboard-new)
    - [Panels (each is a build unit)](#panels-each-is-a-build-unit)
- [C. Consolidated build order](#c-consolidated-build-order)
    - [Stage A — lab (throwaway)](#stage-a-lab-throwaway)
    - [Stage B — v0.1 vertical slice](#stage-b-v01-vertical-slice)
    - [Stage C — enterprise control + dashboard](#stage-c-enterprise-control-dashboard)
    - [Stage D — external boundary](#stage-d-external-boundary)

---

## 0. How to read this document

Each feature below is a self-contained build unit. It lists: the packages to install, what it depends on (so you build in the right order), the stage it belongs to, and a definition of done. Two subsystems here are new and not in the earlier specs — they are marked NEW and explained before their feature entries.

**Two authorization systems, kept separate:**  CoNET governs AGENTS (which agent may call which Skill). The dashboard also needs to manage HUMANS (who may log in, who is admin, who is on the team). These are different data models with different packages. Do not merge them — a human admin configures agent policy, but a human is not an agent. §A and §B below are the human side; §1–10 are the agent side.

## 1. Package map at a glance

The full dependency set for v0.1→v1, grouped by the job it does. Detail and rationale per feature follow.

| Job | Packages | Notes |
|---|---|---|
| Control / admin API | fastapi, uvicorn, pydantic | Typed HTTP API and settings. The backbone. |
| Internal data plane | grpcio, grpcio-tools, protobuf | Skill execution: unary + streaming, deadlines, cancellation. |
| Event plane | nats-py | Core NATS for ephemeral events; JetStream for durable ones. |
| Persistence | pymongo (async), motor-free async API | Registry, config, tasks, audit metadata. |
| Agent policy (RBAC) | casbin, pycasbin adapters | Deny-by-default org→dept→agent→Skill→action rules. |
| Human auth & teams (NEW) | fastapi-users, passlib[bcrypt], python-jose | Human accounts, login, JWT, invite/team roles. |
| Dashboard (server-rendered) | jinja2, htmx (CDN), python-multipart | One operator console; HTMX for live updates without a SPA. |
| Observability (live) | opentelemetry-sdk, -instrumentation-fastapi, -instrumentation-grpc, -exporter-otlp | Traces across HTTP→discovery→gRPC; the dashboard's live data source. |
| Evaluation (offline, optional) | mlflow | Separate tool/UI for agent/model quality. Linked, not embedded. |
| External tools boundary | mcp (official SDK) | Managed MCP gateway at Stage D. |
| External agent delegation (later) | a2a SDK | Edge adapter only; not a v0.1 dependency. |
| Testing / tooling | pytest, pytest-asyncio, ruff, mypy | Quality floor from day one. |

## 2. Where MLflow and OpenTelemetry each belong

These two are often confused. They do different jobs and live in different places. Committing this split now prevents polluting the operator dashboard with the wrong tool.

|  | OpenTelemetry | MLflow |
|---|---|---|
| Question it answers | What is happening in the network right now, and what happened on this task? | How good is this agent/model, measured over many runs offline? |
| Data | Traces, spans, metrics, live events | Experiments, evaluation runs, metrics, model/prompt versions |
| Where it shows | INSIDE the operator dashboard — network map, live traffic, per-task trace | In MLflow's OWN separate UI, linked from the dashboard |
| When you build it | Stage B–C (core to operating) | Stage C+ (optional; when evaluating quality matters) |
| In the product | The dashboard's live data source | A separate, optional companion tool |

**The rule:**  the operator dashboard is built on OpenTelemetry data (live operations). MLflow is a separate, optional tool for offline evaluation, linked out from the dashboard — never embedded in it. You keep MLflow in the project; you do not force its interface into the admin console.

## 3. Agent-side features

### F1  Manifests & Skill contract

**What it does.** Define AgentManifest and SkillManifest as validated models; the JSON-Schema-describes / Protobuf-carries contract from LLD-01. This is the foundation everything else reads.

**Packages.** `pydantic, protobuf, grpcio-tools`

**Depends on.** Nothing. Build first.

**Stage.** A (prototype) → B (finalize).

**Done when.** AgentManifest/SkillManifest validate; SkillRuntime.proto compiles; Struct ↔ JSON Schema round-trips both ways with rejection on mismatch.

### F2  Registration & lease

**What it does.** An agent registers its manifest, gets an identity, and holds a TTL lease it must renew. On expiry it disappears from discovery. Handles duplicate-identity rejection and graceful drain.

**Packages.** `fastapi, pydantic, pymongo (async)`

**Depends on.** F1 (manifests), F9 (persistence).

**Stage.** A (lease prototype) → B.

**Done when.** Agent registers, renews, and is auto-removed from discovery after missed renewals; duplicate identity is rejected (FR-001, FR-002, FR-004).

### F3  Discovery

**What it does.** A requester finds providers by Skill name (exact-name first), version, tags, and permitted metadata. Discovery is permission-aware: unauthorized providers are excluded or marked.

**Packages.** `fastapi, pymongo (async); casbin (for the permission filter)`

**Depends on.** F1, F2, F6 (policy).

**Stage.** B.

**Done when.** A requester discovers a Skill without a hard-coded endpoint; unauthorized providers do not appear (FR-005, FR-006, FR-007).

### F4  gRPC runtime & execution

**What it does.** Execute a chosen provider over the one generic SkillRuntime gRPC service: unary and streaming, bounded deadlines, cooperative cancellation propagated to the provider.

**Packages.** `grpcio, grpcio-tools, protobuf`

**Depends on.** F1.

**Stage.** A (prototype deadline/cancel/health) → B.

**Done when.** Unary and streaming calls work; a cancelled task stops provider work where cooperative cancellation is implemented (FR-009, FR-010, FR-011).

### F5  Routing, retry & failover

**What it does.** Choose one provider among many by health, priority, capacity, locality. Retry and fail over ONLY when Skill side-effects/idempotency permit. 0/1/N-provider cases are deterministic and logged.

**Packages.** `(pure Python; uses F4 + health data)`

**Depends on.** F1, F3, F4, F8 (health).

**Stage.** B (basic selection) → C (failover/capacity).

**Done when.** Routing decision is deterministic and logged; non-idempotent Skills are never auto-retried (FR-008, FR-012, FR-013).

### F6  Agent policy (RBAC)

**What it does.** Deny-by-default authorization over organization → department → agent → Skill → action. Every protected discovery and execution passes policy before running. Denials are explainable. Mints the signed short-TTL auth context the provider verifies.

**Packages.** `casbin (+ a MongoDB adapter), python-jose (sign auth context)`

**Depends on.** F1, F9.

**Stage.** A (Casbin prototype) → B (basic check) → C (full model + explanation).

**Done when.** Cross-department allow/deny scenarios pass predictable tests; a denial can be explained (FR-014, NFR-001).

### F7  Human approval workflow

**What it does.** A policy can place a high-risk task into WAITING_APPROVAL. An authorized human approves or rejects with an auditable decision; the task resumes or is rejected. Approvals expire.

**Packages.** `fastapi, pymongo (async); ties to §A human identities`

**Depends on.** F6, and the human-auth subsystem (§A).

**Stage.** C.

**Done when.** A high-risk task cannot execute before approval; expiry and rejection paths work (FR-015, FR-016).

### F8  Health & lifecycle

**What it does.** Agents expose health (gRPC health + lease + optional NATS heartbeat). The runtime avoids unhealthy providers. Admins can pause, disable, or drain one agent without stopping the network.

**Packages.** `grpcio-health-checking, nats-py`

**Depends on.** F2, F4.

**Stage.** B (health) → C (pause/drain).

**Done when.** A drained agent accepts no new work while unrelated activity continues (FR-018, FR-019, NFR-003).

### F9  Persistence

**What it does.** Durable source of truth for registry, config, tasks, approvals, integrations, and audit. Indexed for network_id + skill_id + status queries. Optimistic concurrency where needed.

**Packages.** `pymongo (async)`

**Depends on.** F1.

**Stage.** A (schema sketch) → B.

**Done when.** Collections and indexes exist; registry/task/audit reads and writes meet benchmark latency (R13, NFR-004).

### F10  Event plane

**What it does.** Publish registration, lease, task, approval, health, and policy events. Core NATS for ephemeral; JetStream for events that must survive consumer outages and be replayable.

**Packages.** `nats-py`

**Depends on.** F2.

**Stage.** A (compare Core vs JetStream) → B.

**Done when.** A durable consumer restarts and continues without duplicate business effects (FR-020, R8).

### F11  Observability & audit (live)

**What it does.** Propagate trace context across FastAPI → discovery → gRPC → events. Append-only audit ledger with actor/action/resource/outcome/trace. This is the DATA SOURCE the operator dashboard reads.

**Packages.** `opentelemetry-sdk, opentelemetry-instrumentation-fastapi, opentelemetry-instrumentation-grpc, opentelemetry-exporter-otlp`

**Depends on.** F2, F4, F9, F10.

**Stage.** A (instrument one request) → B → C.

**Done when.** One task is traceable requester→provider→back; every significant action writes an audit event; secrets/prompts are redacted by default (FR-021, FR-022, NFR-006, NFR-012).

### F12  MCP gateway (external boundary)

**What it does.** One managed gateway connects many external MCP servers, imports/namespaces approved capabilities as CoNET Skills, maps CoNET policy onto them, and keeps external credentials in the gateway's secret store only.

**Packages.** `mcp (official SDK), fastapi`

**Depends on.** F1, F6, F11.

**Stage.** D.

**Done when.** An internal agent uses an external capability without ever handling MCP credentials; one server can be disconnected without affecting others (FR-023–025, R12).

### F13  Evaluation (offline, optional)

**What it does.** Track agent/model quality across runs — experiments, evaluation metrics, prompt/model versions. A SEPARATE tool with its own UI, linked from the dashboard, not embedded.

**Packages.** `mlflow`

**Depends on.** Independent; reads nothing from the live path.

**Stage.** C+ (optional).

**Done when.** An evaluation run is recorded and viewable in MLflow's own UI; the dashboard links to it (R11).

## A. Human accounts, teams & roles  (NEW)

**Why this is separate:**  this is the “like Django” part — people signing up, an admin inviting teammates, assigning human roles. FastAPI (unlike Django) ships no built-in auth or admin, so you assemble it from packages. It is deliberately NOT the agent RBAC (§F6); a human role like “operator” governs what a PERSON can do in the dashboard, not what an AGENT may call.

### A1  Human authentication

**What it does.** First user signs up and becomes owner/admin. Users log in; sessions are JWT-based. Password reset, email verification. Self-hosted — no third-party auth SaaS, to honor “private-network first.”

**Packages.** `fastapi-users, passlib[bcrypt], python-jose, python-multipart`

**Stage.** B (basic login) → C (verification, reset).

**Done when.** A person signs up, becomes admin, logs in, and reaches the dashboard behind an auth wall.

### A2  Teams & human roles

**What it does.** An admin invites teammates by email and assigns a human role: Owner, Admin, Operator, Approver, Auditor, Viewer. Roles gate dashboard actions (e.g. only Admin edits policy; Approver can approve tasks; Auditor is read-only on audit). This is the “create their own permissions and add team members” piece.

**Packages.** `fastapi-users (user model), casbin (reused for HUMAN role rules), pymongo`

**Note.** You can reuse Casbin for human roles too — same engine, a separate policy set. Keep the agent policy set and the human policy set clearly namespaced so they never collide.

**Stage.** C.

**Done when.** An admin invites a user, assigns a role, and that role visibly gates what the user can see and do (FR-016, FR-027 tie-ins).

## B. The operator dashboard  (NEW)

One console where a system admin operates the whole network — see it, watch traffic, change policy, approve tasks, manage people. Server-rendered HTML with HTMX for live updates (no separate React app). Built on the OpenTelemetry/audit data from F11.

#### Panels (each is a build unit)

| Panel | What the admin does | Reads from |
|---|---|---|
| Network map | See agents, departments, who may call whom; spot unhealthy agents. | F2, F3, F8 |
| Live traffic | Watch requests in/out in real time; click a task to see its full trace. | F11 (OTel) |
| Policy editor | Read and change agent policy; see explained allow/deny. | F6 |
| Approvals queue | Approve/reject high-risk tasks awaiting a human. | F7, A2 |
| Audit log | Search who-did-what; export. | F11 |
| Integrations | Connect/disconnect MCP servers; see their health. | F12 |
| Team & accounts | Invite people, assign human roles. | A1, A2 |

**Packages.** `jinja2 (templates), htmx (via CDN, no build step), fastapi, python-multipart; charts via a light JS lib or server-rendered SVG`

**Stage.** C (the console comes together once policy, approvals, health, and audit exist to show).

**Done when.** An admin can, in one place: view the live network, watch a request flow through and read its trace, change a policy and see the effect, approve a pending task, and invite a teammate (FR-026).

**Design direction (for later, when you build the UI):**  this is an operations console, not a marketing site — think Kubernetes dashboard or a network operations center: dense, legible, calm, monospaced numerals, status colors that mean one thing each. Optimize for an operator scanning for problems, not a visitor being impressed. A dedicated UI design pass belongs at Stage C, not now.

## C. Consolidated build order

Features listed in the order dependencies allow. Do not start a feature before the ones it depends on.

#### Stage A — lab (throwaway)

F1 manifests, F4 gRPC (deadline/cancel/health), F10 event compare, F2 lease, F6 Casbin prototype, F11 instrument one request, F12 one MCP tool mapping.

#### Stage B — v0.1 vertical slice

F9 persistence → F1 finalize → F2 registration → F3 discovery → F6 basic check → F5 basic selection → F4 execution → F11 trace+audit → F10 events → F8 health → A1 login → CLI. Target: SRS §10 acceptance test passes.

#### Stage C — enterprise control + dashboard

F6 full policy → F7 approvals → F8 pause/drain → F5 failover → A2 teams/roles → B dashboard panels → F13 MLflow (optional).

#### Stage D — external boundary

F12 MCP gateway full. A2A edge adapter is post-v1, behind the LLD-01 adapter boundary.

**The proof that ties it together:**  wire your own CrewAI and LangGraph agents through F1–F11 as the launch demo. Two agents, two frameworks, discovering and calling each other under one governance layer, watched live in the dashboard — that is the thing nobody else is showing, and it is your LinkedIn/GitHub headline.

*Document 4 of the CoNET set. Companions: Project Overview; SRS; Architecture & Implementation Plan; LLD-01 Manifest & Adapter. Package choices are provisional starting points — confirm each in its Stage-A spike. Verify fast-moving SDKs (MCP, A2A, MLflow) against current docs before pinning versions.*
