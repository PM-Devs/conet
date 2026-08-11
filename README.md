# CoNET — Colony Network

**An open-source orchestration and governance layer for private, distributed AI-agent networks.**

CoNET is the network layer *around* your AI agents. It lets independently built agents — from any framework — join one private network, discover each other, call each other under policy, pause for human approval, reach external tools through a governed boundary, and leave a complete audit trail an administrator can actually read.

> Think of it this way: CoNET is to AI agents what an enterprise network — with identity, DNS, routing, firewall rules, and audit logs — is to servers. It makes a fleet of agents governable.

> ⚠️ **Status: early development (pre-v0.1).** The architecture and specifications are complete and public; the reference implementation is being built in the open. Star and watch the repo to follow along. This README describes what CoNET *is* and *will do* — see the [roadmap](#roadmap) for what runs today.

---

## The problem

A company has three teams shipping AI agents. Finance built one that reconciles invoices. HR built one that answers policy questions. Support built one that triages tickets. Each used a different framework, a different model, a different deployment.

Today, none of this is safe or observable at the company level:

- The finance agent can't ask the support agent for a record without someone hard-coding an endpoint.
- There's no shared way to say *"the HR agent may read the directory but may never move money."*
- When an agent does something unexpected, no admin can see what it did, why, or on whose authority — and no human was asked before it acted.
- Every external tool each agent touches carries its own credentials, scattered across three codebases.

Industry reporting in 2026 estimates only **11–14% of enterprise agentic-AI pilots reach production** — most stall on exactly these identity, audit, and access-control gaps. CoNET is built for that gap, not the model-capability frontier.

---

## Why CoNET is a new layer, not another framework

CoNET doesn't replace the tools you already use — it governs them.

| Layer | Owns | Example |
|---|---|---|
| **Reasoning framework** | How one agent thinks and uses its own tools | LangGraph, CrewAI, AutoGen |
| **Agent-to-tool** | How an agent reaches an external tool | MCP |
| **Agent-to-agent** | How two agents exchange a task | A2A |
| **CoNET** | In one organization: which agents exist, what they may do, who may call whom, which actions need a human, and what happened — provably | *this project* |

Agent-to-tool (MCP) and agent-to-agent (A2A) delegation are largely solved and standardized. **Organization-level governance of an agent fleet is not** — it's a missing architectural layer, not a missing feature. CoNET occupies that layer, and imports the solved pieces rather than rebuilding them.

---

## Key ideas

- **Private-network first.** Run CoNET entirely inside your own environment. No third-party dependency to operate it.
- **Framework-neutral.** LangChain, CrewAI, AutoGen, or plain Python — agents join through a thin adapter (the network-interface-card model). CoNET can't tell them apart except by the Skills they declare.
- **Permission-aware discovery.** Knowing a Skill exists doesn't grant permission to use it.
- **Deny-by-default policy.** Least privilege across organization → department → agent → Skill → action.
- **Human control.** High-risk tasks can wait for approval; admins can cancel a task or pause an agent without stopping the network.
- **One managed boundary for external tools.** External MCP servers connect through a central gateway — credentials never touch ordinary agents, logs, or traces.
- **Observable and auditable.** Every task is traceable end to end; every significant action writes an audit record.

---

## How an agent joins (the adapter model)

An agent doesn't *become* a CoNET agent any more than a laptop *becomes* the network it joins. It plugs in through a thin adapter that gives it a network identity and translates its capabilities into Skills. Inside the adapter, the agent stays exactly what it was.

```
┌──────────────────────────────────────────────┐
│  Your LangChain / CrewAI agent (unchanged)   │
└───────────────────────┬──────────────────────┘
      framework-specific │  (the adapter — ~150 lines)
┌───────────────────────▼──────────────────────┐
│  CoNET Adapter  (maps capabilities → Skills) │
└───────────────────────┬──────────────────────┘
  ===== everything below is framework-neutral =====
┌───────────────────────▼──────────────────────┐
│  CoNET Agent SDK  (identity · register ·      │
│  gRPC skill server · trace + audit)           │
└───────────────────────┬──────────────────────┘
                   gRPC / NATS
┌───────────────────────▼──────────────────────┐
│  CoNET Control Plane                          │
└──────────────────────────────────────────────┘
```

A LangChain agent joining the colony, in about 20 lines:

```python
from conet.sdk import Agent, SkillDef, run

class InvoiceAdapter:
    def describe(self):
        return Agent.manifest(
            name="invoice-checker", framework="langchain",
            department="finance",
            skills=[SkillDef(
                skill_id="invoice.verify",
                side_effects="read_only",
                input_schema={"type": "object",
                    "properties": {"invoice_id": {"type": "string"}},
                    "required": ["invoice_id"]},
                output_schema={"type": "object",
                    "properties": {"valid": {"type": "boolean"}}},
            )],
        )

    async def invoke(self, skill_id, task):        # the only framework-aware line
        return my_invoice_chain.invoke(task.input)

run(InvoiceAdapter())   # SDK handles identity, registration, gRPC, trace, audit
```

> This is illustrative of the target API. See [`docs/`](docs/) for the full manifest and adapter specification (LLD-01).

---

## Architecture at a glance

```
Organization (CoNET network)
├── Department A ── Agent A1, Agent A2
├── Department B ── Agent B1, Agent B2
└── Control Plane
      ├── Agent Registry     ├── Task Control
      ├── Skill Registry     ├── Human Approval
      ├── Discovery          ├── Observability / Audit
      ├── Policy             └── MCP Gateway
      └── Router / Runtime
```

**Built on:** Python · FastAPI · gRPC · NATS · MongoDB · Casbin (policy) · OpenTelemetry (observability) · the official MCP SDK (external tools). Server-rendered operator dashboard (Jinja2 + HTMX). Optional MLflow for offline agent/model evaluation.

---

## Roadmap

CoNET is built in four stages. Detailed specs for each live in [`docs/`](docs/).

- [ ] **Stage A — Architecture laboratory.** Throwaway prototypes to settle the foundational contracts (manifests, gRPC, lease, policy, tracing).
- [ ] **Stage B — v0.1 vertical slice.** Two agents register, discover each other without hard-coded endpoints, pass a permission check, execute over gRPC, and produce a trace + audit record. *This is the first milestone that runs end to end.*
- [ ] **Stage C — Enterprise control.** Full policy model, human approval, agent pause/drain, teams & roles, and the operator dashboard.
- [ ] **Stage D — Managed external boundary.** The MCP gateway: many external tool servers behind one governed, credential-isolated boundary.

**v0.1 done means:** Agent A discovers `math.add` from Agent B and executes it without knowing B's endpoint beforehand — with policy enforcement, lease expiry, cancellation, tracing, and audit all working.

---

## Documentation

The full specification set is public in [`docs/`](docs/):

- **Project Overview** — problem, positioning, principles, scope.
- **Software Requirements Specification (SRS)** — functional & non-functional requirements.
- **Architecture & Implementation Plan** — research spikes, decisions (ADRs), stage plan, benchmarks.
- **LLD-01 — Manifest & Adapter Contract** — how any framework's agent plugs in.
- **Feature & Package Plan** — every feature, its packages, and build order.

---

## Contributing

CoNET is being built in the open and contributions are welcome once the Stage B foundation lands. Until then, the most useful things you can do are:

- **Open an issue** with a use case, a design question, or a challenge to an architecture decision — early feedback shapes the contracts.
- **Star and watch** to follow progress.

A `CONTRIBUTING.md` and contributor guidelines will accompany the first runnable release. See [`CONTRIBUTING.md`](CONTRIBUTING.md) when available.

---

## License

CoNET is released under the [Apache License 2.0](LICENSE) — free to use, modify, and build on, including commercially, with attribution. See [`LICENSE`](LICENSE) for the full text.

---

## Author

Built by **Prince Mawuko Dzorkpe** — software engineer specializing in agentic AI systems, RAG, and backend infrastructure.

- Portfolio: https://www.kobbyprime.online/
- GitHub: https://github.com/PM-Devs

---

<sub>CoNET — governing colonies of agents, framework-neutral, inside your own network.</sub>