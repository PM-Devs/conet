# CoNET — LLD-01: Manifest Contract & Adapter Interface

**Low-Level Design 01.** The foundational contract — all other subsystems depend on this.

*How any agent — including LangChain and CrewAI agents — plugs into the colony without being rewritten.*

---

## 0. The one idea this document commits to

A LangChain agent does not *become* a CoNET agent, any more than a laptop becomes the network it joins. It plugs in through a thin boundary — an **adapter** — that gives it a network identity and translates its capabilities into the one language the colony speaks. Inside that boundary the agent stays exactly what it was: its own framework, its own model, its own logic. This is the network-interface-card (NIC) model, and it is the single most important decision in CoNET.

> **The contract in one sentence:** every agent, whatever it is inside, must (1) hold a CoNET identity, (2) publish an Agent Manifest describing itself and its Skills, (3) keep a live registration lease, and (4) answer routed Skill calls over gRPC. Nothing else is required, and nothing about its internals is dictated.

Everything below the boundary is framework-neutral and written **once**, in the Agent SDK. Everything above the boundary is a small, framework-specific adapter.

### Where the line sits
```
┌───────────────────────────────────────────────┐
│  Your LangChain / CrewAI agent (UNCHANGED)     │
│  chains, crews, tools, memory, its own LLM     │
└──────────────────────┬────────────────────────┘
        framework-specific │  (the adapter you write, ~150 lines)
┌──────────────────────▼────────────────────────┐
│  CoNET Adapter  (maps capabilities → Skills,   │
│                  bridges invoke())             │
└──────────────────────┬────────────────────────┘
  ===== BOUNDARY: everything below is framework-NEUTRAL =====
┌──────────────────────▼────────────────────────┐
│  CoNET Agent SDK  (written ONCE, same for all) │
│  identity/mTLS · register+lease · gRPC server  │
│  trace + audit hooks                           │
└──────────────────────┬────────────────────────┘
                  gRPC / NATS
┌──────────────────────▼────────────────────────┐
│  CoNET Control Plane                           │
└────────────────────────────────────────────────┘
```

---

## 1. Format decision: JSON Schema for describing, Protobuf for doing

A Skill is described in two places, and they have different jobs, so they use different formats. This is a committed decision (it resolves the open R3 question).

| Job | Format | Why this and not the other |
|---|---|---|
| **Describe** (in the manifest, for discovery) | JSON Schema | Self-describing and human-readable at query time; any adapter author in any language can read/generate it without your toolchain. An agent searching discovery can understand a Skill it has never seen. |
| **Do** (on the wire, for execution) | Protobuf over gRPC | Typed, compact, fast; native to the gRPC transport that already gives us deadlines, streaming, cancellation, and health checks. This is the byte-level contract. |

> **The cost, stated honestly:** two representations must stay in sync. §3 defines the exact mapping rule (JSON Schema ↔ Protobuf) so the sync is mechanical, not a judgement call. This is the price of using each tool for its strength — the same pattern gRPC server-reflection and Kubernetes CRDs use.

---

## 2. The Agent Manifest

The Agent Manifest is what an agent presents when it joins. It is the input to `register_agent`. It is JSON, validated against a published JSON Schema, and versioned.

```json
{
  "manifest_version": "0.1",
  "agent": {
    "name": "invoice-checker",
    "framework": "langchain",           // free-form label, informational only
    "department": "finance",            // used by policy (deny-by-default RBAC)
    "role": "worker",
    "version": "1.4.0",
    "endpoint": "grpc://10.0.3.5:7443", // where CoNET routes calls
    "identity_ref": "cert-fingerprint",
    "health_mode": "grpc"               // grpc | heartbeat | both
  },
  "lease": {
    "ttl_seconds": 30,                  // must renew within this window
    "drain_grace_seconds": 20
  },
  "skills": [
    { "$ref": "#/skill_definitions/invoice.verify" }
  ],
  "skill_definitions": { "...": "see Skill Manifest, §3" }
}
```

**Field rules that matter for the build:**

- `name` is unique within the network; a duplicate active identity is rejected (FR-002).
- `department` is not cosmetic — it is a policy subject. Discovery and authorization read it.
- `framework` is informational only. CoNET must never branch on it for routing or policy — that would break framework-neutrality. It exists for humans and dashboards.
- `endpoint` is where the adapter's gRPC Skill-server listens. The control plane never calls the framework directly — only this endpoint.

---

## 3. The Skill Manifest (the "IP packet")

This is the most-depended-on contract in the whole system. Discovery, policy, routing, and every adapter read it. **Freeze it carefully; changing it later ripples everywhere.**

### 3.1 Description side (JSON Schema, lives in the manifest)

```json
{
  "skill_id": "invoice.verify",         // namespaced name; the routing key
  "version": "1.0.0",
  "summary": "Verify an invoice against a purchase order",
  "execution_modes": ["unary", "stream"],
  "side_effects": "read_only",          // read_only | idempotent_write | unsafe_write
  "idempotency": "not_required",        // not_required | key_required
  "tags": ["finance", "verification"],
  "input_schema":  { "type": "object",
                     "properties": { "invoice_id": {"type":"string"} },
                     "required": ["invoice_id"] },
  "output_schema": { "type": "object",
                     "properties": { "valid": {"type":"boolean"},
                                     "reason": {"type":"string"} } }
}
```

> **Why `side_effects` and `idempotency` are first-class fields:** the router uses them to decide what is safe to retry or fail over. A `read_only` Skill can be retried freely; an `unsafe_write` must never be auto-retried; an `idempotent_write` may be retried only if an idempotency key is supplied. Putting this in the contract — not in a code comment — is what makes safe routing possible (FR-012, FR-013).

### 3.2 Execution side (Protobuf, on the wire)

Every Skill call travels through one generic gRPC service. The Skill-specific payload rides inside as a typed-but-opaque struct whose shape is governed by the JSON Schema above. You do **not** regenerate protobuf per Skill — one service serves all Skills.

```protobuf
service SkillRuntime {
  rpc Execute (SkillRequest) returns (SkillResponse);
  rpc ExecuteStream (SkillRequest) returns (stream SkillChunk);
  rpc Cancel (CancelRequest) returns (CancelAck);
}

message SkillRequest {
  string skill_id       = 1;
  string task_id        = 2;   // control-plane issued
  string trace_id       = 3;   // OpenTelemetry context
  string auth_context   = 4;   // signed, short-TTL token from control plane
  string idempotency_key= 5;   // present iff Skill requires it
  int64  deadline_unix_ms=6;
  google.protobuf.Struct input = 7;  // shape governed by input_schema
}

message SkillResponse {
  Status status         = 1;   // OK | DENIED | FAILED | TIMED_OUT | CANCELLED
  google.protobuf.Struct output = 2; // shape governed by output_schema
  string error_detail   = 3;
}
```

> **The design trick:** `google.protobuf.Struct` lets one protobuf service carry any Skill's payload while the JSON Schema enforces the actual shape at the adapter boundary. You get Protobuf's transport benefits (deadlines, streaming, cancellation) without compiling a new `.proto` for every Skill. Validation happens against `input_schema`/`output_schema` at the edge, before and after the call.

### 3.3 The sync rule (JSON Schema ↔ Struct)

Because the wire carries a generic Struct, the two representations stay in sync by one mechanical rule, enforced in the SDK, not by hand:

1. **On the way IN:** the adapter validates the incoming Struct against `input_schema` before invoking the framework. Invalid → reject with DENIED/FAILED, never reach the agent.
2. **On the way OUT:** the adapter validates the framework's result against `output_schema` before returning. Invalid → FAILED, never returned as OK.
3. **Discovery only ever serves the JSON Schema. The wire only ever carries the Struct.** Neither side hand-writes the other.

---

## 4. The Adapter Interface (the NIC contract)

An adapter is the only framework-aware code. To be a valid CoNET adapter, a class must implement four methods. Three are almost identical for every framework (the SDK provides defaults); only `invoke` is genuinely framework-specific.

```python
class CoNETAdapter(Protocol):

    def describe(self) -> AgentManifest:
        """Return this agent + its Skills as a manifest (§2, §3).
        The adapter author decides granularity: one Skill or many."""

    async def invoke(self, skill_id: str, task: SkillRequest) -> SkillResponse:
        """The ONLY framework-specific method. Translate a CoNET Skill
        call into a framework action (run a chain / kick a crew / call a
        function), then translate the result back. SDK validates I/O
        against the schemas around this call."""

    async def stream(self, skill_id, task) -> AsyncIterator[SkillChunk]:
        """Optional. Only if the Skill advertises 'stream'."""

    async def on_cancel(self, task_id: str) -> None:
        """Cooperative cancellation. Best-effort stop of in-flight work."""
```

> **Adapter-author freedom (committed):** the adapter author chooses granularity in `describe()`. A trivial function-agent may expose one Skill; a CrewAI crew may expose five Skills, one per crew capability. CoNET does not care — it only sees Skills. This is what keeps the network device-agnostic.

---

## 5. Worked example A — a LangChain agent joins the colony

```python
from conet.sdk import Agent, SkillDef, run          # framework-neutral SDK
from langchain.chains import my_invoice_chain       # the user's own agent

class LangChainInvoiceAdapter:

    def describe(self):
        return Agent.manifest(
            name="invoice-checker", framework="langchain",
            department="finance", version="1.4.0",
            skills=[
                SkillDef(
                    skill_id="invoice.verify", version="1.0.0",
                    side_effects="read_only",
                    input_schema={'type':'object',
                        'properties':{'invoice_id':{'type':'string'}},
                        'required':['invoice_id']},
                    output_schema={'type':'object',
                        'properties':{'valid':{'type':'boolean'},
                                      'reason':{'type':'string'}}},
                )
            ],
        )

    async def invoke(self, skill_id, task):           # <-- the only LC-aware line
        result = my_invoice_chain.invoke(task.input)  # call LangChain
        return {'valid': result['ok'],
                'reason': result.get('why','')}

# That's it. The SDK does identity, registration, lease renewal, the gRPC
# server, schema validation, trace propagation, and audit. You wrote ~20 lines.
run(LangChainInvoiceAdapter())
```

> **Read what the SDK did for free:** identity/mTLS, `register_agent`, lease renewal, the SkillRuntime gRPC server on the endpoint, validating `task.input` against `input_schema` before your chain saw it, validating your dict against `output_schema` after, attaching `trace_id`, and writing the audit event. The adapter author only expressed "what are my Skills" and "how do I run one."

---

## 6. Worked example B — a CrewAI crew exposing many Skills

Here the adapter author chooses **finer granularity**: one CrewAI crew is mapped to three separate CoNET Skills, each routed and permissioned independently.

```python
class CrewResearchAdapter:

    def describe(self):
        return Agent.manifest(
            name="research-crew", framework="crewai",
            department="marketing", version="2.0.0",
            skills=[
                SkillDef(skill_id="research.find_leads",  version="1.0.0",
                         side_effects="read_only",  input_schema=..., output_schema=...),
                SkillDef(skill_id="research.competitor", version="1.0.0",
                         side_effects="read_only",  input_schema=..., output_schema=...),
                SkillDef(skill_id="research.summarize",  version="1.0.0",
                         side_effects="read_only",  input_schema=..., output_schema=...),
            ],
        )

    async def invoke(self, skill_id, task):
        # one crew, but route each Skill to the right entrypoint
        if skill_id == "research.find_leads":
            return self.crew.run_leads(task.input)
        if skill_id == "research.competitor":
            return self.crew.run_competitor(task.input)
        if skill_id == "research.summarize":
            return self.crew.run_summary(task.input)
```

> **Why this matters for governance:** because the three are separate Skills, policy can allow marketing agents to call `research.find_leads` but require human approval for `research.competitor`, and audit each independently. If the whole crew were one opaque Skill, you would lose that control. Granularity is a governance lever — handed to the adapter author, enforced by CoNET.

---

## 7. Contract guarantees and prohibitions

### 7.1 What the SDK guarantees to every adapter
- A valid CoNET identity and mTLS channel before any Skill call arrives.
- `register_agent`, lease renewal, and graceful drain are handled; the adapter never writes registration logic.
- Incoming input is validated against `input_schema` before `invoke()` is called.
- Outgoing output is validated against `output_schema` before it leaves.
- `trace_id` and `task_id` are attached and propagated; an audit event is written per call.
- A DENIED authorization is enforced before `invoke()` runs — unauthorized calls never reach the framework.

### 7.2 What an adapter must never do (breaks the network model)
- Never call another agent directly. Always go through CoNET discovery + routing, so policy and audit apply.
- Never hold external tool credentials. External tools come through the MCP gateway as Skills (LLD-07).
- Never trust `auth_context` blindly — the SDK verifies it; adapter code must not bypass the SDK to accept raw calls.
- Never branch CoNET behavior on the framework label. Inside the agent, do anything; at the boundary, be a generic Skill provider.
- Never emit secrets or raw payloads into logs/traces (NFR-012).

### 7.3 Deliberately handed to later LLDs

| Question | Owned by |
|---|---|
| How is `auth_context` minted, signed, and verified? | LLD-05 Policy & Authorization |
| Exact register/renew/expire wire sequence and failure handling | LLD-03 Registration & Lease |
| Provider selection when N agents offer the same `skill_id` | LLD-04 Discovery & Routing |
| Task state machine, cancellation guarantees in depth | LLD-06 Task Lifecycle |
| How MCP external tools become Skills through this same contract | LLD-07 MCP Gateway |
| Audit event schema and trace attribute standard | LLD-08 Audit & Observability |

---

## 8. Definition of done for this contract

- AgentManifest v0.1 JSON Schema is written and published in the repo.
- SkillManifest v0.1 JSON Schema is written and published.
- `SkillRuntime.proto` compiles and the generic Execute/ExecuteStream/Cancel round-trips a Struct.
- The SDK validates Struct ↔ JSON Schema in both directions with clear rejection on mismatch.
- The `CoNETAdapter` Protocol is defined, and the LangChain example adapter registers, is discovered, and executes `invoice.verify` end to end.
- A second framework (CrewAI or a plain function) plugs into the SAME SDK with only its own `invoke()` — proving neutrality.

> **The proof that the contract is right:** two agents on two different frameworks join the same network through the same SDK, and CoNET cannot tell them apart except by their declared Skills. That is the networking model working — devices differ, the network doesn't care.
