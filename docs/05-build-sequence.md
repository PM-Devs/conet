# CoNET — Document 5: Build Sequence

*Classes & functions, one by one — the ordered list of what to code first: signatures, purpose, and internal steps.*

> Work down this list top to bottom. Each unit lists its file, signature, what it does, and its internal steps. Stage A and early Stage B are in full depth; later stages are outlined. Never build a unit before the ones it depends on.

**Build checklist** · Stage A → early Stage B in depth · Draft, 10 August 2026

## Contents

- [0. How to use this](#0-how-to-use-this)
- [Stage A — Architecture laboratory (throwaway prototypes)](#stage-a--architecture-laboratory-throwaway-prototypes)
- [Stage B — v0.1 vertical slice (the real code)](#stage-b--v01-vertical-slice-the-real-code)
- [The very first thing to type](#the-very-first-thing-to-type)
- [Stage C — enterprise control (outline)](#stage-c--enterprise-control-outline)
- [Stage D — managed external boundary (outline)](#stage-d--managed-external-boundary-outline)

---

## 0. How to use this

Each entry is one buildable unit. The format is deliberately uniform so you always know the next move:

- File — where it lives in src/conet/.
- Signature — the class or function shape to write.
- Does — one line: its single responsibility.
- Steps — the internal logic, as a few bullets. You write the actual code from these.

The golden rule of order:  build strictly top to bottom. Every unit only depends on units above it. If you are tempted to jump ahead, you have found a missing dependency — go back and build it first.

Guiding principle from the architecture plan: prototype the risky contracts as throwaways first (Stage A), then build the real vertical slice (Stage B). Stage A code is allowed to be ugly and disposable. Stage B code is the real thing.

## Stage A — Architecture laboratory (throwaway prototypes)

Goal: prove the risky contracts work before you commit real APIs. Build these seven prototypes, in order. Each answers one dangerous question. Throw the code away after; keep the decision.

#### A1. Manifest models

**File.** `prototypes/manifests.py  (scratch)`

**Signature.**

```python
class SkillDef(BaseModel):
    skill_id: str; version: str; summary: str = ''
    execution_modes: list[str] = ['unary']
    side_effects: Literal['read_only','idempotent_write','unsafe_write']
    idempotency: Literal['not_required','key_required'] = 'not_required'
    input_schema: dict; output_schema: dict; tags: list[str] = []

class AgentManifest(BaseModel):
    name: str; framework: str; department: str; role: str = 'worker'
    version: str; endpoint: str; identity_ref: str
    lease_ttl_seconds: int = 30; skills: list[SkillDef] = []
```

**Does.** Define the Skill and Agent manifests as Pydantic models — the contract from LLD-01.

**Steps.**

- Write both models with the fields above; let Pydantic enforce types.
- Add a validator that rejects an empty skills list only if role == 'worker'.
- Add .to_json_schema() helper returning the input/output schema for discovery.
- Write a 5-line script that builds one manifest and prints it as JSON.

**Proves.** The manifest shape is expressible and validates. (Resolves ADR-003 for real.)

#### A2. gRPC SkillRuntime prototype

**File.** `prototypes/skillruntime.proto + skill_server.py (scratch)`

**Signature.**

```python
# proto: service SkillRuntime { Execute; ExecuteStream; Cancel; }
class SkillServer(SkillRuntimeServicer):
    async def Execute(self, request, context) -> SkillResponse
    async def ExecuteStream(self, request, context)  # yields SkillChunk
    async def Cancel(self, request, context) -> CancelAck
```

**Does.** Stand up the one generic gRPC service all Skills share; prove deadline, streaming, cancellation, health.

**Steps.**

- Write the .proto with SkillRequest/Response carrying a google.protobuf.Struct payload.
- Compile with grpcio-tools; implement a trivial Execute that echoes input.
- Implement ExecuteStream that yields 3 chunks with a sleep between them.
- Test: call with a 1s deadline against a 3s handler — confirm it times out.
- Test: cancel mid-stream from the client — confirm the server sees cancellation.
- Add grpc_health_checking and hit it from the client.

**Proves.** gRPC gives you deadlines, streaming, and cooperative cancellation. (Feeds the runtime contract, R7.)

#### A3. Lease prototype

**File.** `prototypes/lease.py (scratch)`

**Signature.**

```python
class LeaseRegistry:
    async def register(self, agent_id: str, ttl: int) -> None
    async def renew(self, agent_id: str) -> bool
    async def active_agents(self) -> list[str]   # only non-expired
```

**Does.** Prove the register → renew → expire → disappear lifecycle with a TTL.

**Steps.**

- Store {agent_id: expires_at} in a dict (in-memory is fine for the prototype).
- register sets expires_at = now + ttl; renew pushes it forward, returns False if already expired.
- active_agents returns only ids whose expires_at > now.
- Test: register with ttl=2, sleep 3, confirm the agent is gone from active_agents.

**Proves.** An agent auto-disappears from discovery after missed renewals. (Feeds FR-004, R2.)

#### A4. Policy prototype (Casbin)

**File.** `prototypes/policy.py (scratch)`

**Signature.**

```python
class PolicyEngine:
    def authorize(self, subject: str, skill_id: str, action: str) -> bool
    def explain(self, subject, skill_id, action) -> str
```

**Does.** Prove deny-by-default RBAC over org → department → agent → Skill → action with Casbin.

**Steps.**

- Write a Casbin model.conf with request (sub, obj, act) and a deny-by-default effect.
- Write a policy.csv with 2 departments and a cross-department deny case.
- authorize returns Casbin's enforce result; default is deny.
- explain returns which rule matched (or 'no matching allow rule → deny').
- Test: same Skill, allowed for finance, denied for marketing.

**Proves.** Permission-aware rules are expressible and explainable. (Resolves ADR-006 direction, R5.)

#### A5. Event plane prototype (NATS)

**File.** `prototypes/events.py (scratch)`

**Signature.**

```python
async def publish(subject: str, payload: dict) -> None
async def subscribe(subject: str, handler) -> None
# compare Core NATS vs JetStream durability
```

**Does.** Decide which events are fire-and-forget (Core) and which must survive a consumer restart (JetStream).

**Steps.**

- Connect to a local NATS server (docker run nats).
- Publish 10 events on Core NATS while the subscriber is DOWN; start it; confirm they are LOST.
- Repeat with a JetStream durable consumer; confirm the events are REPLAYED on restart.
- Write down which CoNET events need JetStream (task, approval, audit) vs Core (health pings).

**Proves.** You know the durability split. (Resolves ADR-010, R8.)

#### A6. One traced request

**File.** `prototypes/tracing.py (scratch)`

**Signature.**

```python
# instrument FastAPI → discovery call → gRPC with one trace_id
```

**Does.** Prove a single trace_id follows a request across HTTP, an internal call, and gRPC.

**Steps.**

- Set up OpenTelemetry with a console exporter.
- Instrument a tiny FastAPI endpoint that calls a function that calls the A2 gRPC server.
- Confirm all three spans share one trace_id in the console output.

**Proves.** End-to-end tracing works across boundaries. (Feeds FR-021, R11.)

#### A7. One MCP tool → Skill

**File.** `prototypes/mcp_probe.py (scratch)`

**Signature.**

```python
# connect one MCP server, map one tool into a temporary SkillDef
```

**Does.** Prove an external MCP tool can be represented as a CoNET Skill without leaking credentials.

**Steps.**

- Use the official MCP SDK to connect to one simple MCP server.
- List its tools; pick one; build a SkillDef whose input/output schema mirrors the tool.
- Confirm the MCP credential stays in your gateway code and never enters the SkillDef.

**Proves.** The gateway concept holds; external tools fit the same Skill contract. (Feeds R12.)

Stage A exit criteria:  all seven prototypes run, and you have written down the resolved decisions (ADR-003, 006, 010, and the durability + identity directions). Only then start Stage B. Delete the prototype code; keep the decisions.

## Stage B — v0.1 vertical slice (the real code)

Now build the real package, in this order. Each unit lives in its proper src/conet/ home and is production code, not throwaway. The target is the SRS §10 acceptance test.

#### B1. Persistence layer

**File.** `src/conet/persistence/store.py`  
**Depends on:** A1, A3

**Signature.**

```python
class Store:
    def __init__(self, mongo_uri: str)
    async def upsert_agent(self, manifest: AgentManifest) -> None
    async def get_agent(self, agent_id: str) -> AgentManifest | None
    async def list_active_providers(self, skill_id: str) -> list[AgentManifest]
    async def save_task(self, task: Task) -> None
    async def append_audit(self, event: AuditEvent) -> None
```

**Does.** The durable source of truth: agents, skills, tasks, audit — in MongoDB.

**Steps.**

- Create collections: agents, tasks, audit. Index agents on (skill_ids, status, lease_expires_at).
- upsert_agent writes the manifest; list_active_providers filters by skill_id AND lease not expired AND status active.
- append_audit is append-only — never update or delete an audit doc.
- Wrap all calls in try/except that logs and re-raises; no silent failures.

#### B2. Manifest models (final)

**File.** `src/conet/sdk/manifests.py`  
**Depends on:** A1

**Signature.**

```python
# Promote the A1 prototype models to real, versioned code
class SkillDef(BaseModel): ...   class AgentManifest(BaseModel): ...
```

**Does.** The permanent manifest models, now with the schema-validation helpers the SDK relies on.

**Steps.**

- Move the A1 models here; add manifest_version field, default '0.1'.
- Add validate_input(payload) / validate_output(payload) using jsonschema against the SkillDef.
- These validators are what the SDK calls around invoke() (see B7).

#### B3. Registry (control plane)

**File.** `src/conet/control/registry.py`  
**Depends on:** B1, B2

**Signature.**

```python
class Registry:
    async def register_agent(self, manifest: AgentManifest) -> str   # agent_id
    async def renew_lease(self, agent_id: str) -> bool
    async def unregister_agent(self, agent_id: str) -> None
    async def publish_skills(self, agent_id, skills: list[SkillDef]) -> None
```

**Does.** Agents join, renew, publish Skills, and leave. Enforces unique identity.

**Steps.**

- register_agent rejects a duplicate active name (FR-002); else upserts via Store and sets lease_expires_at.
- renew_lease pushes lease_expires_at forward; returns False if already expired.
- On register/renew/unregister, emit an event (B8) and an audit record (B1).

#### B4. Discovery

**File.** `src/conet/control/discovery.py`  
**Depends on:** B3, B6

**Signature.**

```python
class Discovery:
    async def find_skill(self, requester, skill_id, version=None) -> list[AgentManifest]
    async def describe_provider(self, agent_id) -> AgentManifest | None
```

**Does.** Find providers by Skill name — permission-aware, so unauthorized ones are excluded.

**Steps.**

- find_skill loads active providers from Store, filters by version/tags.
- For each candidate, call PolicyEngine.authorize(requester, skill_id, 'invoke'); drop the ones denied (FR-006).
- Return the surviving list; empty list is a valid answer (0-provider case).

#### B5. gRPC runtime

**File.** `src/conet/runtime/server.py + src/conet/protocols/grpc/`  
**Depends on:** A2, B2

**Signature.**

```python
class SkillServer(SkillRuntimeServicer):
    async def Execute(self, request, context) -> SkillResponse
    async def ExecuteStream(self, request, context)
    async def Cancel(self, request, context) -> CancelAck
```

**Does.** The real Skill-execution server: validates, runs the adapter, returns typed results.

**Steps.**

- Promote the A2 proto to protocols/grpc/; regenerate stubs.
- Execute: verify auth_context (B6), validate input against schema (B2), call the adapter's invoke, validate output, return.
- On any validation failure, return status DENIED or FAILED — never let bad data reach the adapter.
- Track running tasks by task_id so Cancel can signal them.

#### B6. Policy engine

**File.** `src/conet/control/policy.py`  
**Depends on:** A4, B1

**Signature.**

```python
class PolicyEngine:
    async def authorize(self, subject, skill_id, action) -> bool
    async def explain_decision(self, subject, skill_id, action) -> str
    def mint_auth_context(self, subject, skill_id) -> str   # signed, short-TTL
    def verify_auth_context(self, token) -> dict | None
```

**Does.** Deny-by-default authorization + the signed token the runtime verifies.

**Steps.**

- Load Casbin model + policies from Store; authorize returns enforce(), default deny (FR-014).
- mint_auth_context issues a short-TTL JWT (python-jose) binding subject+skill_id.
- verify_auth_context checks signature and expiry; the runtime (B5) calls this before executing.

#### B7. Agent SDK

**File.** `src/conet/sdk/agent.py`  
**Depends on:** B2, B3, B5

**Signature.**

```python
class Agent:
    @staticmethod
    def manifest(name, framework, department, skills, **kw) -> AgentManifest

def run(adapter: CoNETAdapter) -> None:   # the one call an agent author makes
```

**Does.** The framework-neutral SDK. Handles identity, registration, lease renewal, the gRPC server, and validation — so an adapter author writes ~20 lines.

**Steps.**

- run() calls adapter.describe() to get the manifest, then registers it via the control plane.
- Starts a background task that renews the lease every ttl/2 seconds.
- Starts the SkillServer (B5), wiring incoming calls to adapter.invoke with schema validation around it.
- On shutdown, calls unregister_agent for a clean drain.

#### B8. Event plane

**File.** `src/conet/protocols/events/bus.py`  
**Depends on:** A5

**Signature.**

```python
class EventBus:
    async def publish(self, subject: str, payload: dict) -> None
    async def subscribe(self, subject: str, handler) -> None
```

**Does.** Publish lifecycle/task/audit events; durable (JetStream) where the A5 decision said so.

**Steps.**

- Wrap nats-py; publish to Core or JetStream based on subject (per your A5 durability table).
- Registry (B3), runtime (B5), and policy (B6) call publish on significant actions.

#### B9. Observability & audit

**File.** `src/conet/observability/tracing.py`  
**Depends on:** A6, B1

**Signature.**

```python
def setup_tracing(app) -> None
def audit(actor, action, resource, outcome, trace_id) -> AuditEvent
```

**Does.** Attach trace context across the request path; write append-only audit records.

**Steps.**

- Promote A6 setup; instrument FastAPI and gRPC with OpenTelemetry.
- audit() builds an AuditEvent and calls Store.append_audit; redact secrets/prompts (NFR-012).
- Ensure task_id and trace_id travel together through B5 and B8.

#### B10. CLI

**File.** `src/conet/cli/main.py`  
**Depends on:** B3, B4, B5

**Signature.**

```python
def main() -> None   # entry point wired in pyproject
# commands: status, agents, skills, cancel <task_id>
```

**Does.** The operator's first interface — see the network and cancel a task from the terminal.

**Steps.**

- status: print control-plane health + active agent count.
- agents / skills: list from the registry.
- cancel <task_id>: call the runtime's Cancel; print the outcome.

Stage B done when:  two agents on separate processes run the SRS §10 acceptance test — Agent A discovers math.add from Agent B without a hard-coded endpoint, is authorized, executes over gRPC, a denial is shown, B disappears on lease expiry, an admin cancels a task, and one trace + audit record explains it all. That is v0.1.

### The very first thing to type

Build order for your first coding session: A1 (manifest models) → A2 (gRPC prototype) → A3 (lease). Those three, as throwaway prototypes, de-risk the whole project. Everything else attaches to what they teach you.

## Stage C — enterprise control (outline)

Build after v0.1 passes. Lighter outline; each becomes its own detailed unit when you reach it.

| Area | Key classes/functions to add |
|---|---|
| Full policy | Policy zones (org/dept/network); explain_decision surfaced in the dashboard. |
| Approvals | ApprovalWorkflow: request_approval, approve, reject, expire; pauses task at WAITING_APPROVAL. |
| Agent control | AdminService: pause_agent, disable_agent, drain_agent. |
| Failover | Router upgrade: health-aware selection, capacity, provider failover. |
| Human accounts | AuthService (FastAPI-Users): signup, login, JWT; first user = admin. |
| Teams & roles | TeamService: invite, assign_role (Owner/Admin/Operator/Approver/Auditor/Viewer). |
| Dashboard | FastAPI + Jinja2 + HTMX panels: network map, live traffic, policy editor, approvals, audit, team. |

## Stage D — managed external boundary (outline)

| Area | Key classes/functions to add |
|---|---|
| MCP gateway | MCPGateway: connect_server, disconnect_server, import_capabilities, invoke_external_capability. |
| Namespacing | Map many servers' tools into collision-free CoNET Skill names. |
| Secret isolation | Gateway-only secret store; credentials referenced by handle, never in manifests/traces. |
| Policy mapping | External invocations pass the same authorize + audit path as internal tasks. |

Post-v1: the A2A edge adapter slots in here, behind the same CoNETAdapter boundary from LLD-01 — no core changes.

---

*Document 5 of the CoNET set. Build top to bottom. Signatures and steps are the design; the code is yours to write. When a single unit's internals get hard, that unit is the right place to go deep.*
