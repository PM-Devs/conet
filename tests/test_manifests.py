import jsonschema
import pytest
from pydantic import ValidationError

from conet.sdk.manifests import AgentManifest, AuditEvent, SkillDef, Task


def make_skill(**overrides) -> SkillDef:
    defaults = {
        'skill_id': 'invoice.verify', 'version': '1.0.0', 'side_effects': 'read_only',
        'input_schema': {'type': 'object', 'properties': {'invoice_id': {'type': 'string'}}, 'required': ['invoice_id']},
        'output_schema': {'type': 'object', 'properties': {'valid': {'type': 'boolean'}}},
    }
    defaults.update(overrides)
    return SkillDef(**defaults)


def test_worker_requires_at_least_one_skill():
    with pytest.raises(ValidationError):
        AgentManifest(
            name='a', framework='f', department='d', version='1',
            endpoint='e', identity_ref='i', skills=[],
        )


def test_non_worker_role_allows_empty_skills():
    manifest = AgentManifest(
        name='a', framework='f', department='d', version='1',
        endpoint='e', identity_ref='i', role='router', skills=[],
    )
    assert manifest.manifest_version == '0.1'


def test_skill_validate_input_accepts_matching_payload():
    skill = make_skill()
    skill.validate_input({'invoice_id': 'INV-1'})


def test_skill_validate_input_rejects_mismatched_payload():
    skill = make_skill()
    with pytest.raises(jsonschema.exceptions.ValidationError):
        skill.validate_input({'invoice_id': 123})


def test_skill_validate_output_rejects_mismatched_payload():
    skill = make_skill()
    with pytest.raises(jsonschema.exceptions.ValidationError):
        skill.validate_output({'valid': 'not-a-bool'})


def test_task_defaults():
    task = Task(requester='agent-a', skill_id='invoice.verify')
    assert task.state == 'CREATED'
    assert task.task_id


def test_audit_event_defaults():
    event = AuditEvent(actor='agent-a', action='invoke', resource='invoice.verify', outcome='OK')
    assert event.event_id
    assert event.metadata == {}
