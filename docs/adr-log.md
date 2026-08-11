# ADR log — Stage A resolutions

Decisions resolved by running the seven Stage A prototypes (`docs/05-build-sequence.md`, A1–A7). The prototype code was throwaway and has been deleted; these are the decisions it proved.

## ADR-003 — Mandatory fields in AgentManifest and SkillManifest

**Resolved.** The field set in LLD-01 §2/§3 is expressible as Pydantic models and validates correctly, including the rule that a `worker` agent must declare at least one skill. Confirmed by A1.

## R7 — gRPC runtime contract

**Resolved.** One generic `SkillRuntime` service (`Execute` / `ExecuteStream` / `Cancel`) built on `grpc.aio` gives deadlines (`DEADLINE_EXCEEDED` on a 1s deadline vs. a 3s handler), server-streaming, cooperative cancellation (the server observes a client-side `call.cancel()` mid-stream), and standard gRPC health checking, all through `google.protobuf.Struct` as the generic payload. Confirmed by A2.

## R2 / FR-004 — Lease lifecycle

**Resolved.** An in-memory `{agent_id: expires_at}` TTL registry correctly drops an agent from `active_agents()` after a missed renewal, and rejects `renew()` on an already-expired lease. The real implementation (B1/B3) swaps the dict for MongoDB; the state-machine logic itself is proven. Confirmed by A3.

## ADR-006 — Policy model

**Resolved, direction confirmed.** Deny-by-default RBAC via Casbin (`some(where (p.eft == allow))` with no default allow) correctly denies any `(sub, obj, act)` with no matching rule, including the cross-department case (finance allowed on a skill, marketing denied on the same skill with no policy line at all). `enforce_ex` gives an explainable "which rule matched" / "no matching allow rule → deny" result. Confirmed by A4.

## ADR-010 — Event durability split

**Resolved.**

- **Core NATS** (fire-and-forget): a subscriber that did not exist at publish time never receives those messages — confirmed 0/10 delivered. Use for health pings and ephemeral status.
- **JetStream** (durable): a durable pull consumer that reconnects after a "restart" (fresh connection, same durable name) replays everything not yet acked — confirmed 10/10 delivered. Use for task, approval, and audit events.

Confirmed by A5.

## R11 — Distributed tracing

**Resolved.** A single `trace_id` propagates automatically across an HTTP request (FastAPI, auto-instrumented), a manually-created internal span (`discovery_call`), and a gRPC client call (`grpc.Execute`) — 5 spans, 1 shared trace_id, no manual trace_id plumbing required beyond starting child spans inside the ambient OpenTelemetry context. Confirmed by A6.

**Operational note:** `grpc.aio` should be treated as tied to a single event loop per process. Running a `grpc.aio` server and a `grpc.aio` client channel on two different event loops (e.g. server on a background thread's loop, client on the main loop via a thread-portal test client) produced `UNAVAILABLE: connection refused` even though the server had started successfully. Keep server + client on one event loop (or use a real subprocess/separate process, not a separate thread+loop) for anything built on `grpc.aio`.

## R12 — MCP gateway / credential isolation

**Resolved.** Connecting to a real MCP server (stdio transport), listing its tools, and mapping one tool's `input_schema`/`output_schema` into a `SkillDef` works directly — the shapes are structurally compatible. The credential the tool needs (`WEATHER_API_KEY`) was passed only via the subprocess environment the gateway process controls; it is provably absent (asserted) from the serialized `SkillDef`, and the invoked tool call still succeeded. Confirmed by A7.

**SDK note:** the installed `mcp` SDK (v2.0.0) uses `mcp.server.MCPServer` (the `FastMCP` name from older SDK versions) and exposes `Tool.input_schema` / `Tool.output_schema` in snake_case. Verify current SDK naming again before pinning a version in `pyproject.toml` — this surface has moved before and will likely move again.

## ADR-015 (new) — Persistence backend: SQLite, not MongoDB

**Supersedes docs/03-architecture-and-implementation-plan.md §5's "MongoDB + PyMongo Async" baseline.** That baseline was explicitly marked provisional ("starting hypotheses, not irreversible decisions") and its own stated justification was preference, not a technical requirement — the only real technical driver, NFR-004 ("scale horizontally"), doesn't bite at v0.1 (ADR-001: single-org, single-process).

Decision: `B1 Store` (`src/conet/persistence/store.py`) is built on `aiosqlite`, not `pymongo`. Reasons:

- Zero external services to install/run — directly serves NFR-011 ("a developer shall be able to run a minimal CoNET network locally with clear configuration").
- The `Store` public contract (5 async methods) is unchanged from the spec; only its `__init__` parameter changed from `mongo_uri: str` to `db_path: str`. Swapping the backend later means rewriting `store.py`'s internals behind that same interface, not touching any caller.
- `fastapi-users[beanie]` (Mongo-only) was swapped to `fastapi-users[sqlalchemy]` in `pyproject.toml` for consistency; `pymongo` was dropped in favor of `aiosqlite`.

Revisit before Stage C if the network needs a genuinely multi-node control plane — SQLite's single-writer model won't scale horizontally.

## Stage B finding — Execute must re-check authorize(), not just the token signature

While building the SRS §10 acceptance flow, `B5 SkillServer.Execute`/`ExecuteStream` only verified that `auth_context` was a validly-signed, unexpired JWT (`PolicyEngine.verify_auth_context`). Nothing enforced that the token had actually been minted for an authorized `(subject, skill_id)` pair — `mint_auth_context` signs whatever it's given, with no authorize() check of its own. Since v0.1 has no separate unit that gates minting on a prior `authorize()` call, this meant a validly-signed token for an unauthorized subject would have been accepted at execution time.

**FR-014 already requires this**: "Every protected discovery **and execution** operation shall pass policy evaluation before execution" — execution-time enforcement isn't optional. Fixed by having `SkillServer._authorize_request` also call `policy.authorize(subject, skill_id, 'invoke')` before running the adapter (`src/conet/runtime/server.py`). Covered by `test_execute_denied_for_a_validly_signed_token_of_an_unauthorized_subject` in `tests/test_runtime_server.py`.

Also added: `Discovery.find_skill` now writes an audit record when it excludes a candidate for lack of authorization (FR-022 — denials are security-significant and must be audited; this wasn't happening before).

## ADR-016 (new) — grpcio family and protobuf must be pinned together, narrowly

Installing `mlflow` (F13's optional `eval` extra) pulled in `databricks-sdk`, which caps `protobuf<7.0` (with several 5.x/6.x point releases excluded). That silently downgraded the environment's `protobuf` package — but `grpcio-tools`' `protoc` embeds a *fixed* gencode target per release (1.83.0 always targets protobuf gencode 7.35.1, regardless of what `protobuf` package is separately installed), and protobuf's runtime check requires `runtime >= gencode`. The result: every `*_pb2.py` file generated with grpcio-tools 1.83.0 refused to import once mlflow's install downgraded the runtime below 7.35.1 — `google.protobuf.runtime_version.VersionError`.

This is a real footgun: installing an *optional* extra broke the *core* gRPC data plane, silently, with no signal until something tried to import a generated stub.

**Fix**: pin the whole `grpcio`/`grpcio-tools`/`grpcio-health-checking`/`grpcio-status` family to the same narrow range (`>=1.71,<1.72`) together with `protobuf>=5.29.6,<6.0` — a combination verified compatible with both grpcio-tools 1.71.0's gencode target *and* databricks-sdk's ceiling. The `skillruntime_pb2*.py` files were regenerated against this combination (`python -m grpc_tools.protoc ...` — see `src/conet/protocols/grpc/skillruntime.proto`); remember to reapply the `from . import skillruntime_pb2 as skillruntime__pb2` relative-import fix in `skillruntime_pb2_grpc.py` after any regeneration (grpc_tools.protoc always emits a bare `import`, which breaks once the generated files live inside a package rather than a flat script directory).

If `conet[eval]` (mlflow) is ever dropped, or a way to isolate its dependency graph from the core install is found, these caps can likely be relaxed again — check whether `databricks-sdk`'s protobuf ceiling has moved before doing so.

---

Not covered by a Stage A prototype (left as-is from the Architecture plan, still open): ADR-002 (identity/mTLS), ADR-004/005/007/008/009/011/012/013/014. These are owned by later stages/LLDs, not blockers for the Stage B vertical slice.
