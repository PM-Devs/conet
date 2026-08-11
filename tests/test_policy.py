import os

import pytest

from conet.control.policy import PolicyEngine

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy.csv')


@pytest.fixture
def engine():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
def empty_engine():
    return PolicyEngine(secret_key='test-secret')


async def test_authorize_allows_matching_rule(engine):
    assert await engine.authorize('finance', 'invoice.verify', 'invoke') is True


async def test_authorize_denies_cross_department(engine):
    assert await engine.authorize('marketing', 'invoice.verify', 'invoke') is False


async def test_authorize_denies_by_default_with_no_policy_loaded(empty_engine):
    assert await empty_engine.authorize('anyone', 'anything', 'invoke') is False


async def test_explain_decision_allow(engine):
    explanation = await engine.explain_decision('finance', 'invoice.verify', 'invoke')
    assert 'allowed by rule' in explanation


async def test_explain_decision_deny(engine):
    explanation = await engine.explain_decision('marketing', 'invoice.verify', 'invoke')
    assert explanation == 'no matching allow rule → deny'


def test_mint_and_verify_auth_context_round_trip(engine):
    token = engine.mint_auth_context('finance', 'invoice.verify')
    claims = engine.verify_auth_context(token)
    assert claims is not None
    assert claims['sub'] == 'finance'
    assert claims['skill_id'] == 'invoice.verify'


def test_verify_auth_context_rejects_garbage_token(engine):
    assert engine.verify_auth_context('not-a-real-token') is None


def test_verify_auth_context_rejects_wrong_secret(engine):
    token = engine.mint_auth_context('finance', 'invoice.verify')
    other_engine = PolicyEngine(secret_key='different-secret')
    assert other_engine.verify_auth_context(token) is None


def test_verify_auth_context_rejects_expired_token(engine, monkeypatch):
    import conet.control.policy as policy_module
    monkeypatch.setattr(policy_module, '_AUTH_CONTEXT_TTL_SECONDS', -1)
    token = engine.mint_auth_context('finance', 'invoice.verify')
    assert engine.verify_auth_context(token) is None
