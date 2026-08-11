import logging
import os
import time

import casbin
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), 'policy_model.conf')
_ALGORITHM = 'HS256'
_AUTH_CONTEXT_TTL_SECONDS = 30


class PolicyEngine:
    """Deny-by-default authorization + the signed auth context the runtime verifies.

    Policy is loaded from a Casbin model + a deployer-provided policy CSV
    (Stage B). Sourcing policy from the Store instead is Stage C work, once
    a policy editor/API exists to write it there.
    """

    def __init__(self, secret_key: str, policy_path: str | None = None) -> None:
        self._secret_key = secret_key
        self._enforcer = casbin.Enforcer(_MODEL_PATH, policy_path) if policy_path else casbin.Enforcer(_MODEL_PATH)
        self._enforcer.enable_auto_save(True)  # dashboard Policy editor writes persist back to policy_path

    async def authorize(self, subject: str, skill_id: str, action: str) -> bool:
        try:
            return self._enforcer.enforce(subject, skill_id, action)
        except Exception:
            logger.exception('authorize failed for (%s, %s, %s)', subject, skill_id, action)
            raise

    async def explain_decision(self, subject: str, skill_id: str, action: str) -> str:
        try:
            allowed, matched_rules = self._enforcer.enforce_ex(subject, skill_id, action)
            if allowed and matched_rules:
                return f'allowed by rule: {matched_rules[0]}'
            return 'no matching allow rule → deny'
        except Exception:
            logger.exception('explain_decision failed for (%s, %s, %s)', subject, skill_id, action)
            raise

    def mint_auth_context(self, subject: str, skill_id: str) -> str:
        now = time.time()
        payload = {'sub': subject, 'skill_id': skill_id, 'iat': now, 'exp': now + _AUTH_CONTEXT_TTL_SECONDS}
        return jwt.encode(payload, self._secret_key, algorithm=_ALGORITHM)

    def verify_auth_context(self, token: str) -> dict | None:
        try:
            return jwt.decode(token, self._secret_key, algorithms=[_ALGORITHM])
        except JWTError:
            return None

    def list_policy_rules(self) -> list[tuple[str, str, str]]:
        """Not part of B6's original 4-method contract; added for the
        dashboard's Policy editor panel."""
        return [tuple(rule) for rule in self._enforcer.get_policy()]

    def add_policy_rule(self, subject: str, skill_id: str, action: str) -> bool:
        """Returns False if the rule already existed."""
        return self._enforcer.add_policy(subject, skill_id, action)

    def remove_policy_rule(self, subject: str, skill_id: str, action: str) -> bool:
        """Returns False if no such rule existed."""
        return self._enforcer.remove_policy(subject, skill_id, action)
