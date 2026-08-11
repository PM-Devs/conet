import logging
import time

import aiosqlite

from conet.sdk.manifests import AgentManifest, Approval, AuditEvent, Task

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    lease_expires_at REAL NOT NULL,
    registered_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_skills (
    agent_name TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    skill_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_skill_id ON agent_skills(skill_id);
CREATE INDEX IF NOT EXISTS idx_agents_status_lease ON agents(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    approval_json TEXT NOT NULL,
    state TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals(state);
"""


class Store:
    """Durable source of truth: agents, skills, tasks, audit.

    Backed by SQLite (via aiosqlite) rather than MongoDB so the control
    plane ships with zero external services (see docs/adr-log.md).
    """

    def __init__(self, db_path: str = 'conet.db') -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute('PRAGMA foreign_keys = ON')
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def upsert_agent(self, manifest: AgentManifest) -> None:
        try:
            db = await self._connection()
            now = time.time()
            lease_expires_at = now + manifest.lease_ttl_seconds
            await db.execute(
                """INSERT INTO agents (name, manifest_json, status, lease_expires_at, registered_at)
                   VALUES (?, ?, 'active', ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       manifest_json = excluded.manifest_json,
                       status = 'active',
                       lease_expires_at = excluded.lease_expires_at""",
                (manifest.name, manifest.model_dump_json(), lease_expires_at, now),
            )
            await db.execute('DELETE FROM agent_skills WHERE agent_name = ?', (manifest.name,))
            await db.executemany(
                'INSERT INTO agent_skills (agent_name, skill_id) VALUES (?, ?)',
                [(manifest.name, skill.skill_id) for skill in manifest.skills],
            )
            await db.commit()
        except Exception:
            logger.exception('upsert_agent failed for %s', manifest.name)
            raise

    async def get_agent(self, agent_id: str) -> AgentManifest | None:
        try:
            db = await self._connection()
            async with db.execute('SELECT manifest_json FROM agents WHERE name = ?', (agent_id,)) as cursor:
                row = await cursor.fetchone()
            return AgentManifest.model_validate_json(row[0]) if row else None
        except Exception:
            logger.exception('get_agent failed for %s', agent_id)
            raise

    async def list_active_providers(self, skill_id: str) -> list[AgentManifest]:
        try:
            db = await self._connection()
            now = time.time()
            query = """
                SELECT a.manifest_json FROM agents a
                JOIN agent_skills s ON s.agent_name = a.name
                WHERE s.skill_id = ? AND a.status = 'active' AND a.lease_expires_at > ?
            """
            async with db.execute(query, (skill_id, now)) as cursor:
                rows = await cursor.fetchall()
            return [AgentManifest.model_validate_json(row[0]) for row in rows]
        except Exception:
            logger.exception('list_active_providers failed for %s', skill_id)
            raise

    async def list_all_agents(self) -> list[AgentManifest]:
        """Not part of B1's original 5-method contract; added for the CLI's
        `status`/`agents` commands, which need every active agent, not just
        the providers of one skill."""
        try:
            db = await self._connection()
            now = time.time()
            query = "SELECT manifest_json FROM agents WHERE status = 'active' AND lease_expires_at > ?"
            async with db.execute(query, (now,)) as cursor:
                rows = await cursor.fetchall()
            return [AgentManifest.model_validate_json(row[0]) for row in rows]
        except Exception:
            logger.exception('list_all_agents failed')
            raise

    async def save_task(self, task: Task) -> None:
        try:
            db = await self._connection()
            await db.execute(
                """INSERT INTO tasks (task_id, task_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                       task_json = excluded.task_json,
                       updated_at = excluded.updated_at""",
                (task.task_id, task.model_dump_json(), time.time()),
            )
            await db.commit()
        except Exception:
            logger.exception('save_task failed for %s', task.task_id)
            raise

    async def get_task(self, task_id: str) -> Task | None:
        """Not part of B1's original 5-method contract; added so the CLI's
        `cancel <task_id>` command can look up which agent owns a task."""
        try:
            db = await self._connection()
            async with db.execute('SELECT task_json FROM tasks WHERE task_id = ?', (task_id,)) as cursor:
                row = await cursor.fetchone()
            return Task.model_validate_json(row[0]) if row else None
        except Exception:
            logger.exception('get_task failed for %s', task_id)
            raise

    async def list_recent_tasks(self, limit: int = 50) -> list[Task]:
        """Not part of B1's original 5-method contract; added for the
        dashboard's Live Traffic panel."""
        try:
            db = await self._connection()
            async with db.execute(
                'SELECT task_json FROM tasks ORDER BY updated_at DESC LIMIT ?', (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
            return [Task.model_validate_json(row[0]) for row in rows]
        except Exception:
            logger.exception('list_recent_tasks failed')
            raise

    async def append_audit(self, event: AuditEvent) -> None:
        try:
            db = await self._connection()
            await db.execute(
                'INSERT INTO audit (event_id, event_json, timestamp) VALUES (?, ?, ?)',
                (event.event_id, event.model_dump_json(), event.timestamp.timestamp()),
            )
            await db.commit()
        except Exception:
            logger.exception('append_audit failed for %s', event.event_id)
            raise

    async def list_audit_events(self, trace_id: str | None = None) -> list[AuditEvent]:
        """Not part of B1's original 5-method contract; the ledger is
        append-only by design (append_audit), but something has to be able
        to read it back — for an operator's audit search (Feature Plan §B)
        and for verifying "an audit record explains what happened" (SRS §10)."""
        try:
            db = await self._connection()
            query = 'SELECT event_json FROM audit'
            params: tuple[str, ...] = ()
            if trace_id is not None:
                query += ' WHERE json_extract(event_json, "$.trace_id") = ?'
                params = (trace_id,)
            query += ' ORDER BY timestamp'
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
            return [AuditEvent.model_validate_json(row[0]) for row in rows]
        except Exception:
            logger.exception('list_audit_events failed')
            raise

    async def save_approval(self, approval: Approval) -> None:
        """Not part of B1's original 5-method contract; added for F7 (human
        approval workflow) and the dashboard's Approvals queue panel."""
        try:
            db = await self._connection()
            await db.execute(
                """INSERT INTO approvals (approval_id, approval_json, state, expires_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(approval_id) DO UPDATE SET
                       approval_json = excluded.approval_json,
                       state = excluded.state,
                       expires_at = excluded.expires_at""",
                (approval.approval_id, approval.model_dump_json(), approval.state, approval.expires_at.timestamp()),
            )
            await db.commit()
        except Exception:
            logger.exception('save_approval failed for %s', approval.approval_id)
            raise

    async def get_approval(self, approval_id: str) -> Approval | None:
        try:
            db = await self._connection()
            async with db.execute(
                'SELECT approval_json FROM approvals WHERE approval_id = ?', (approval_id,),
            ) as cursor:
                row = await cursor.fetchone()
            return Approval.model_validate_json(row[0]) if row else None
        except Exception:
            logger.exception('get_approval failed for %s', approval_id)
            raise

    async def list_pending_approvals(self) -> list[Approval]:
        try:
            db = await self._connection()
            async with db.execute(
                "SELECT approval_json FROM approvals WHERE state = 'PENDING' ORDER BY expires_at",
            ) as cursor:
                rows = await cursor.fetchall()
            return [Approval.model_validate_json(row[0]) for row in rows]
        except Exception:
            logger.exception('list_pending_approvals failed')
            raise

    async def is_agent_active(self, agent_id: str) -> bool:
        """Not part of B1's original 5-method contract; added because the
        registry (B3) needs to distinguish a live agent from a stale,
        lease-expired record with the same name, and get_agent() only
        returns the manifest, not the lease state (see docs/adr-log.md)."""
        try:
            db = await self._connection()
            now = time.time()
            query = "SELECT 1 FROM agents WHERE name = ? AND status = 'active' AND lease_expires_at > ?"
            async with db.execute(query, (agent_id, now)) as cursor:
                row = await cursor.fetchone()
            return row is not None
        except Exception:
            logger.exception('is_agent_active failed for %s', agent_id)
            raise

    async def deactivate_agent(self, agent_id: str) -> None:
        """Not part of B1's original 5-method contract; added so the
        registry (B3) has a way to remove an agent on unregister."""
        try:
            db = await self._connection()
            await db.execute('DELETE FROM agent_skills WHERE agent_name = ?', (agent_id,))
            await db.execute('DELETE FROM agents WHERE name = ?', (agent_id,))
            await db.commit()
        except Exception:
            logger.exception('deactivate_agent failed for %s', agent_id)
            raise
