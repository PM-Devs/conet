import logging
import os

import casbin

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), 'human_roles_model.conf')
_DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(__file__), 'human_roles_policy.csv')

VALID_ROLES = ('Owner', 'Admin', 'Operator', 'Approver', 'Auditor', 'Viewer')


class UnknownRoleError(ValueError):
    """Raised when assign_role/invite is given a role outside VALID_ROLES."""


class TeamService:
    """Human roles (Owner/Admin/Operator/Approver/Auditor/Viewer), gating
    dashboard actions.

    Deliberately a *separate* Casbin enforcer and policy set from
    PolicyEngine's agent RBAC (Feature Plan §A: "a human role... governs
    what a PERSON can do in the dashboard, not what an AGENT may call") —
    the two must never collide. The role -> allowed-actions map
    (human_roles_policy.csv) is a fixed capability set shipped with the
    package, not deployer-editable business data like agent policy is;
    only the per-user role *assignments* (g rules) are runtime state.
    """

    def __init__(self, role_policy_path: str | None = None) -> None:
        self._enforcer = casbin.Enforcer(_MODEL_PATH, role_policy_path or _DEFAULT_POLICY_PATH)

    async def assign_role(self, user_id: str, role: str) -> None:
        if role not in VALID_ROLES:
            raise UnknownRoleError(f'unknown role {role!r}; must be one of {VALID_ROLES}')
        self._enforcer.delete_roles_for_user(user_id)
        self._enforcer.add_role_for_user(user_id, role)
        try:
            # Persist role assignment so it survives restarts when using a
            # writable policy file adapter. If the configured policy path is
            # inside site-packages this may fail; callers should set
            # CONET_HUMAN_ROLES_POLICY_PATH to a writable path.
            self._enforcer.save_policy()
        except Exception:
            logger.warning('team: assigned role %s to %s but failed to persist policy (check CONET_HUMAN_ROLES_POLICY_PATH)', role, user_id)
        logger.info('team: %s assigned role %s', user_id, role)

    async def invite(self, user_id: str, role: str) -> None:
        """An admin granting a member a role. Account creation / invite-email
        delivery belongs to SA1's auth flow; this grants the dashboard-facing
        role once the invitee has an identity."""
        await self.assign_role(user_id, role)

    async def get_role(self, user_id: str) -> str | None:
        roles = self._enforcer.get_roles_for_user(user_id)
        return roles[0] if roles else None

    async def revoke(self, user_id: str) -> None:
        self._enforcer.delete_roles_for_user(user_id)
        logger.info('team: %s role revoked', user_id)

    async def can(self, user_id: str, action: str) -> bool:
        return self._enforcer.enforce(user_id, action)
