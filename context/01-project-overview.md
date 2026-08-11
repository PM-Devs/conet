# CoNET — Project Overview

**Document 1 of the CoNET specification set.** Read this first.

*An open-source orchestration and governance layer for private, distributed AI-agent networks. Explains what CoNET is, the problem it solves, how it differs from adjacent systems, and what v0.1 will and will not do.*

> Status: v0.1 scope · early development.

---

## 1. The problem, in plain terms

A mid-sized company has three teams shipping AI agents. Finance built an agent that reconciles invoices. HR built one that answers policy questions and files leave requests. Support built one that triages tickets. Each team used a different framework, a different model, and a different deployment. **Today, none of this is safe or observable at the company level.**

The finance agent cannot ask the support agent for a customer record without someone hard-coding an endpoint. There is no shared way to say "the HR agent may read the employee directory but may never move money." When an agent takes an action nobody expected, no administrator can see what it did, why, or on whose authority — and no human was asked to approve it before money moved. Every external tool each agent touches carries its own credentials, scattered across three codebases.

> **The one-sentence version:** CoNET is to AI agents what an enterprise network — with identity, DNS, routing, firewall rules, and audit logs — is to servers. It makes a fleet of independently built agents governable.

> **Why this matters commercially.** Industry reporting in 2026 estimates that only 11–14% of enterprise agentic-AI pilots reach production; the rest stall on identity, audit, and access-control gaps — exactly the concerns CoNET addresses. CoNET is positioned at the productionization gap, not the model-capability frontier.

---

## 2. What CoNET is (and is not)

CoNET provides the network layer around AI agents. Its job is not to define how an agent reasons internally. It gives independently built agents a shared way to join a private network, advertise capabilities, discover permitted peers, request work, enforce rules, observe activity, and connect to external applications through a controlled MCP boundary.

**CoNET is:**

- A private network for agents: identity, discovery, routing, policy, approval, audit.
- Framework-neutral: LangChain, CrewAI, AutoGen, or plain code all join through a thin adapter.
- A governance layer that sits above interoperability transports (MCP, A2A), not a replacement for them.
- Open-source at the core: the network, protocols, and runtime are inspectable and extensible.

**CoNET is not:**

- An agent-reasoning framework. It does not tell an agent how to think.
- A public agent marketplace or cross-company federation (explicitly deferred).
- A replacement for MCP or A2A. It governs them.
- A model provider or a hosting platform.

---

## 3. Prior art and positioning

The short version, supported by the 2026 literature on agent interoperability: **agent-to-tool access (MCP) and agent-to-agent delegation (A2A) are largely solved and standardized. Organization-level governance of an agent fleet is not — it is a missing architectural layer, not a missing feature.** CoNET occupies that layer, and imports the solved pieces rather than rebuilding them.

| System | What it does well | What it does NOT give you (CoNET's gap) |
|---|---|---|
| **MCP** | Standardizes how one agent connects to external tools/data. The de-facto agent-to-tool standard. | No org-level policy across many agents, no cross-agent discovery, no human approval, no unified audit. A tool boundary, not a network. |
| **A2A** (v1.0, Linux Foundation, 2026) | Standardizes agent-to-agent discovery and delegation via Agent Cards. Answers "which agent handles this task?" | Delegation-centric and governance-neutral: no policy, no permission-aware discovery, no approval gates, no governance-grade audit. |
| **Service mesh** (Istio, Linkerd) | Identity, mTLS, routing, health, observability — for microservices. CoNET borrows this model. | Operates on packets/services, not agent capabilities or Skills. No notion of a Skill or human approval of a semantic action. |
| **Frameworks** (LangGraph, CrewAI) | Orchestrate steps/tools inside one app or one team's agent. | Scoped to one app/team. No shared cross-team registry, no org-wide policy, no framework-neutral network. |
| **Message bus** (NATS, Kafka) | Reliable transport, events, streaming, durability. CoNET uses this as its event plane. | Transport only. No agent identity, capability model, authorization, discovery, or audit semantics. |

> **The differentiator to keep visible:** others move messages between agents. CoNET governs a colony of them — discovery, permission, human control, and audit, framework-neutral, inside your own network. A2A may later be adopted as an external/edge adapter, not reimplemented.

---

## 4. Product principles

- **Private-network first:** a company can operate CoNET entirely inside its own environment.
- **Agents self-register:** an agent announces identity, role, location, health, and Skills when it joins.
- **Skills are discoverable:** requesting agents search for capabilities, not hard-coded addresses.
- **Discovery is permission-aware:** knowing a Skill exists does not grant permission to use it.
- **Internal agents do not need MCP:** CoNET handles internal discovery, routing, and execution natively.
- **One managed MCP boundary:** external MCP servers connect through a central gateway, not per-agent.
- **Least privilege:** permissions apply at organization, branch, department, agent, Skill, resource, and action level.
- **Human control:** high-risk tasks can wait for approval; admins can cancel a task or pause an agent without stopping the network.
- **Framework neutrality:** agents may use different frameworks, languages, and models.
- **Open-source core:** the network, protocols, and core runtime are inspectable and extensible.

---

## 5. Committed decisions (v0.1)

These are settled for v0.1 and shared across all documents so they never drift:

| Decision | Committed position |
|---|---|
| **Tenancy (ADR-001)** | Single-organization, single-trust-domain. One deployment = one company. Multi-tenancy deferred. |
| **Skill contract (ADR-003 / R3)** | JSON Schema describes a Skill (discovery); Protobuf carries it (execution). One generic gRPC service serves all Skills. |
| **Framework support** | Agents join through a thin adapter (the NIC model). Adapter author chooses Skill granularity. |
| **Identity (R1)** | mTLS per agent, single internal CA, operator-issued bootstrap. Automated attestation deferred. |
| **Authorization (R5)** | Deny-by-default RBAC scoped org → department → agent → Skill → action; Casbin prototype. |
| **External tools** | Reach CoNET only through the managed MCP gateway; credentials never enter agents/logs. |

---

## 6. v0.1 scope and success

CoNET v0.1 is successful when two independently running agents, on separate processes or machines, can securely join, self-register, advertise Skills, discover one another without hard-coded endpoints, pass a permission check, execute a task over gRPC, propagate cancellation, record a trace and an audit entry, and disappear cleanly from discovery when their lease expires.

**Explicitly deferred:** public/internet discovery; cross-company reputation or marketplace; billing/settlement between organizations; a CoNET-specific public PKI; semantic (embedding-based) Skill discovery; federation between separate CoNET networks; any universal agent-reasoning framework.

---

## 7. How the document set fits together

| Document | Purpose |
|---|---|
| **1 · Project Overview** (this) | Orientation: problem, positioning, principles, scope, committed decisions. |
| **2 · Software Requirements (SRS)** | Formal FR/NFR requirements, domain objects, state machines, interfaces, acceptance criteria. |
| **3 · Architecture & Implementation Plan** | How it is built: topology, research spikes, ADRs, stage plan, tech baseline, benchmarks. |
| **4 · Feature & Package Plan** | Every feature, its packages, and build order. |
| **5 · Build Sequence** | Ordered classes & functions to code, one by one. |
| **LLD-01 · Manifest & Adapter** | How any framework's agent plugs in. |

**Reading order:** this Overview → SRS → Architecture plan → the LLD for the subsystem you are about to build. Each fact lives in one document and is referenced, not repeated, by the others.
