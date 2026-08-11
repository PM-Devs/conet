# CoNET — Software Requirements Specification (SRS)

**Document 2 of the CoNET specification set.**

*The formal contract of what CoNET v0.1 must do. Reference material — check it; do not read it as narrative. Rationale lives in the Project Overview; build detail lives in the LLDs.*

---

## 1. Scope

This SRS defines functional and non-functional requirements for an initial open-source CoNET release targeting private organizational networks. Public federation and marketplace features are explicitly deferred (§9). Rationale and positioning are in Document 1 (Project Overview); this document states requirements only.

> **Foundational assumption (ADR-001):** CoNET v0.1 is single-organization, single-trust-domain. All requirements below are written for one company per deployment.

---

## 2. Actors

| Actor | Description |
|---|---|
| Network Administrator | Creates/configures the network, policies, integrations, and operational controls. |
| Agent | Independent AI/software worker that registers itself, advertises Skills, and requests Skills from others. |
| Skill Provider | An agent that exposes one or more executable Skills. |
| Human Approver | Authorized person who approves or rejects protected tasks. |
| MCP Gateway | Managed CoNET service that connects external MCP servers and maps their capabilities into CoNET. |
| Observer / Auditor | Reads traces, logs, and activity history without necessarily controlling execution. |

---

## 3. Core domain objects

| Object | Minimum fields |
|---|---|
| Network | network_id, name, organization, trust_domain, status, created_at |
| Node | node_id, network_id, endpoint, location, version, health, last_seen |
| Agent | agent_id, network_id, node_id, department, role, status, version, public_identity, lease_expires_at |
| Skill | skill_id, name, version, description, input_schema, output_schema, execution_modes, tags |
| AgentSkill | agent_id, skill_id, enabled, priority, capacity, constraints |
| Policy | policy_id, subject, action, resource, conditions, effect, priority |
| Task | task_id, requester, provider, skill_id, state, deadline, trace_id, idempotency_key, timestamps |
| Approval | approval_id, task_id, policy_id, approvers, state, expires_at, decision_metadata |
| Integration | integration_id, gateway_id, type=MCP, server_name, status, allowed_skills, secret_reference |
| AuditEvent | event_id, actor, action, resource, outcome, timestamp, trace_id, metadata |

> **Contract note:** the Skill object's `input_schema`/`output_schema` are JSON Schema (see LLD-01). Skill and Agent manifest field-level detail is specified in LLD-01, not repeated here.

---

## 4. Functional requirements

| ID | Requirement | Definition |
|---|---|---|
| FR-001 | Agent self-registration | An authenticated agent shall be able to register or renew its manifest and lease. |
| FR-002 | Unique identity | The system shall reject duplicate or conflicting active agent identities unless an authorized replacement flow is used. |
| FR-003 | Skill advertisement | An agent shall be able to advertise multiple versioned Skills with typed input/output contracts. |
| FR-004 | Lease expiration | An agent whose lease expires shall no longer be returned as an active provider. |
| FR-005 | Discovery | A requester shall be able to discover providers by Skill name, version, tags, and permitted metadata. |
| FR-006 | Permission-aware discovery | The discovery response shall exclude or mark providers the requester is not authorized to invoke, per configured policy. |
| FR-007 | Many-to-many providers | Multiple agents may provide the same Skill, and one agent may provide multiple Skills. |
| FR-008 | Provider routing | The runtime shall choose an eligible provider using configurable routing criteria. |
| FR-009 | Direct execution | The runtime shall execute a selected provider through the configured internal transport. |
| FR-010 | Deadlines | Each remote task shall carry a bounded deadline; indefinite waiting shall not be the default. |
| FR-011 | Cancellation | A requester or authorized administrator shall be able to cancel a running task; cancellation shall propagate to the provider where supported. |
| FR-012 | Retry / failover | The runtime shall support bounded retries and provider failover only when Skill/task semantics permit it. |
| FR-013 | Idempotency | The system shall support idempotency keys where duplicate execution could cause inconsistent results. |
| FR-014 | Policy enforcement | Every protected discovery and execution operation shall pass policy evaluation before execution. |
| FR-015 | Human approval | Policies may place tasks into a waiting-for-approval state before execution. |
| FR-016 | Approval decision | Authorized approvers shall be able to approve or reject pending tasks with an auditable decision. |
| FR-017 | Task control | Administrators shall be able to cancel one task without stopping unrelated tasks. |
| FR-018 | Agent control | Administrators shall be able to pause, disable, or drain one agent without stopping the rest of the network. |
| FR-019 | Health | Agents/providers shall expose health status the runtime can use to avoid unhealthy providers. |
| FR-020 | Event publication | Registration, lease, task, approval, health, and policy events shall be published to the internal event plane. |
| FR-021 | Distributed trace | Task execution shall propagate a trace context across control-plane, event-plane, and RPC boundaries. |
| FR-022 | Audit ledger | Security- and business-significant activity shall create an audit record with actor, action, resource, outcome, and trace reference. |
| FR-023 | MCP gateway | The network shall support one managed gateway (instance or logical service) maintaining connections to multiple MCP servers. |
| FR-024 | MCP capability import | The gateway shall map approved external MCP capabilities into CoNET-visible capabilities without exposing credentials to ordinary agents. |
| FR-025 | MCP policy enforcement | Requests through the MCP gateway shall be subject to the same policy, approval, trace, and audit controls as internal tasks. |
| FR-026 | Dashboard visibility | The admin interface shall show agents, Skills, health, active tasks, approvals, integrations, and recent activity. |
| FR-027 | Configuration history | Policy and integration configuration changes shall be auditable. |
| FR-028 | Framework neutrality | The CoNET wire/runtime contracts shall not require agents to use a particular framework or model provider. |

---

## 5. Non-functional requirements

| ID | Quality | Requirement |
|---|---|---|
| NFR-001 | Security | Deny by default for protected capabilities; authenticate network participants; never place raw external credentials in ordinary agent manifests. |
| NFR-002 | Isolation | A compromised or disabled agent shall not automatically gain access to unrelated departments, Skills, or external integrations. |
| NFR-003 | Availability | Failure of one agent or provider shall not stop unrelated agents or tasks. |
| NFR-004 | Scalability | Registry, event, and routing components shall scale horizontally without manually rewiring agents. |
| NFR-005 | Latency | Control-plane lookups and policy checks shall not dominate normal task latency; exact targets set by benchmark. |
| NFR-006 | Observability | Operators shall be able to trace a task from requester through discovery, authorization, provider selection, execution, and result. |
| NFR-007 | Auditability | Sensitive actions, approvals, permission changes, and external integration usage shall be reconstructable from retained records. |
| NFR-008 | Extensibility | New internal transports, discovery strategies, and policy engines shall be pluggable behind stable interfaces. |
| NFR-009 | Versioning | Agent, Skill, and protocol contracts shall have explicit versions and compatibility rules. |
| NFR-010 | Graceful degradation | If observability or non-critical analytics are unavailable, core task execution shall continue when security policy allows. |
| NFR-011 | Open-source usability | A developer shall be able to run a minimal CoNET network locally with clear configuration and examples. |
| NFR-012 | Data minimization | Logs and traces shall avoid retaining unnecessary prompts, secrets, or sensitive payloads by default. |

---

## 6. Required state machines

### 6.1 Agent lifecycle
```
REGISTERING → ACTIVE → DRAINING → OFFLINE
            ↘ DISABLED
ACTIVE → DEGRADED → ACTIVE / OFFLINE
```

### 6.2 Task lifecycle
```
CREATED → AUTHORIZING → (WAITING_APPROVAL) → ROUTING → RUNNING → COMPLETED
        ↘ REJECTED          ↘ FAILED
RUNNING → CANCELLING → CANCELLED
RUNNING → TIMED_OUT
```

### 6.3 Integration lifecycle
```
DISCONNECTED → CONNECTING → HEALTHY → DEGRADED → DISCONNECTED
                                    ↘ DISABLED
```

---

## 7. Security requirements (committed v0.1 positions)

The following were open research questions in earlier drafts. For v0.1 each is given a committed requirement with an explicit deferral. Deeper mechanism detail is in the Architecture plan (§8) and the Policy LLD.

| Concern | v0.1 requirement | Deferred |
|---|---|---|
| Initial credential | Operator-issued short-lived join token, exchanged at registration for an mTLS client cert scoped to one agent. | Automated attestation (SPIFFE/SPIRE-style). |
| Transport security | mTLS on all gRPC and control-plane APIs; single internal CA; cert carries agent identity. | Automated short-lived rotation (Stage C). |
| Authorization | Deny-by-default RBAC over org → department → agent → Skill → action (Casbin prototype). | ABAC / relationship-based rules. |
| Fake-agent registration | Credential bound to one identity; duplicate active identities rejected (FR-002). Blast radius = one agent. | Registration anomaly detection. |
| Skill-advertisement forgery | Advertisements accepted only over an authenticated, identity-bound channel. | Independently signed manifests. |
| Router bypass | Providers reject unrouted calls; each execution carries a signed, short-TTL auth context the provider verifies. | Full capability-token cryptographic verification. |
| Secret management | External credentials live only in the MCP gateway secret store, by reference; never in manifests, payloads, traces, events, or audit (NFR-001, NFR-012). | External secrets manager (Vault/KMS). |
| Cancellation side effects | Cooperative, propagated cancellation (FR-011); non-idempotent Skills are not auto-retried (FR-012, FR-013). | Forced kill with compensating transactions. |
| Audit integrity | Append-only audit ledger with actor/action/resource/outcome/trace (FR-022). | Tamper-evidence (hash-chaining), formal retention. |
| Admin access | Distinct operator identity; all admin actions audited (FR-027). | Separation-of-duties, approval-of-admin-actions. |

> **Blast-radius requirement (the security test to keep passing):** if any single agent is fully compromised, the attacker gains only that agent's own Skills and whatever it was already permitted to call — no lateral movement, no external credentials, no admin control (NFR-002).

---

## 8. Interface requirements

Exact API and protobuf definitions are specified per subsystem in the LLDs. The minimum conceptual interfaces are:

| Interface | Operations |
|---|---|
| Registry API | register_agent, renew_lease, unregister_agent, publish_skills, update_status |
| Discovery API | find_skill, list_providers, describe_provider |
| Runtime API | execute_skill, stream_skill, cancel_task, get_task |
| Policy API | authorize, explain_decision, list_effective_permissions |
| Approval API | request_approval, approve, reject, expire |
| Admin API | pause_agent, resume_agent, drain_agent, disable_skill, revoke_permission |
| MCP Gateway API | connect_server, disconnect_server, import_capabilities, health, invoke_external_capability |
| Observability hooks | start/end span, task events, policy events, approval events, audit events |

---

## 9. Deferred requirements

Public internet-wide discovery; cross-company reputation or marketplace; billing/settlement between organizations; a CoNET-specific global public PKI; semantic Skill discovery using embeddings; federation between separately governed CoNET networks; a universal agent-reasoning framework.

---

## 10. Acceptance test for v0.1

Run two agents on separate processes (separate machines for the final test):

1. Agent B registers Skill `math.add`.
2. Agent A discovers it without a hard-coded endpoint.
3. Agent A receives authorization and executes via gRPC, receiving a result.
4. A policy denial is demonstrated (unauthorized call refused and audited).
5. Agent B becomes undiscoverable after lease expiry.
6. An administrator cancels an active task without affecting unrelated tasks.
7. A single trace spans the request end to end, and an audit record explains what happened.
