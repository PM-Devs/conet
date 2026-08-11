from typing import TYPE_CHECKING

from conet.sdk.manifests import (
    AgentManifest,
    Approval,
    ApprovalState,
    AuditEvent,
    AuditOutcome,
    SkillDef,
    Task,
    TaskState,
)

if TYPE_CHECKING:
    from conet.sdk.agent import Agent, CoNETAdapter, RunningAgent, run, run_async, start

__all__ = [
    'Agent',
    'AgentManifest',
    'Approval',
    'ApprovalState',
    'AuditEvent',
    'AuditOutcome',
    'CoNETAdapter',
    'RunningAgent',
    'SkillDef',
    'Task',
    'TaskState',
    'run',
    'run_async',
    'start',
]

# conet.sdk.agent pulls in the control/runtime/observability stack, which
# imports back into conet.persistence.store -> conet.sdk.manifests -- eagerly
# importing it here at package-init time would make that a circular import.
# Loading it lazily on first attribute access keeps `from conet.sdk import
# Agent, run` working without forcing that chain to resolve upfront.
_AGENT_EXPORTS = {'Agent', 'CoNETAdapter', 'RunningAgent', 'run', 'run_async', 'start'}


def __getattr__(name: str):
    if name in _AGENT_EXPORTS:
        from conet.sdk import agent
        return getattr(agent, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
