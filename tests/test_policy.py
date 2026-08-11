import os
import shutil

import pytest

from conet.control.policy import PolicyEngine

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy.csv')


@pytest.fixture
def engine():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
def mutable_engine(tmp_path):
    # add_policy_rule/remove_policy_rule now auto-save -- never point this at
    # the shared fixture file, or one test's edits leak into every other test
    policy_path = str(tmp_path / 'policy.csv')
    shutil.copy(_FIXTURE_POLICY, policy_path)
    return PolicyEngine(secret_key='test-secret', policy_path=policy_path), policy_path


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


def test_list_policy_rules_returns_the_loaded_rules(engine):
    rules = engine.list_policy_rules()
    assert ('finance', 'invoice.verify', 'invoke') in rules


def test_add_policy_rule_makes_it_immediately_effective(mutable_engine):
    engine, _ = mutable_engine
    assert engine.add_policy_rule('marketing', 'invoice.verify', 'invoke') is True
    assert ('marketing', 'invoice.verify', 'invoke') in engine.list_policy_rules()


async def test_add_policy_rule_takes_effect_in_authorize(mutable_engine):
    engine, _ = mutable_engine
    assert await engine.authorize('marketing', 'invoice.verify', 'invoke') is False
    engine.add_policy_rule('marketing', 'invoice.verify', 'invoke')
    assert await engine.authorize('marketing', 'invoice.verify', 'invoke') is True


def test_add_policy_rule_returns_false_for_a_duplicate(mutable_engine):
    engine, _ = mutable_engine
    assert engine.add_policy_rule('finance', 'invoice.verify', 'invoke') is False  # already in the fixture


def test_remove_policy_rule_makes_it_immediately_ineffective(mutable_engine):
    engine, _ = mutable_engine
    assert engine.remove_policy_rule('finance', 'invoice.verify', 'invoke') is True
    assert ('finance', 'invoice.verify', 'invoke') not in engine.list_policy_rules()


def test_remove_policy_rule_returns_false_for_a_nonexistent_rule(mutable_engine):
    engine, _ = mutable_engine
    assert engine.remove_policy_rule('nobody', 'nothing', 'invoke') is False


def test_add_policy_rule_persists_to_the_policy_file(mutable_engine):
    engine, policy_path = mutable_engine
    engine.add_policy_rule('marketing', 'invoice.verify', 'invoke')
    with open(policy_path, encoding='utf-8') as f:
        contents = f.read()
    assert 'marketing' in contents


def test_mutable_engine_edits_never_touch_the_shared_fixture_file():
    with open(_FIXTURE_POLICY, encoding='utf-8') as f:
        original = f.read()
    assert 'marketing, invoice.verify, invoke' not in original
