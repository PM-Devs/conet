# Wires the plain support_bot.py (examples/bots/support_bot.py, must
# already be running on :9001) into the colony as a governed CoNET agent --
# support_bot's own code is never touched or made aware of any of this.
#
# Run (after starting support_bot.py and a local NATS server):
#   python examples/wired/support_agent.py

from _shared import DB_PATH, NATS_URL, POLICY_PATH, POLICY_SECRET

from conet.gateway.webhook import WebhookAdapter, WebhookSkill
from conet.sdk import SkillDef, run

adapter = WebhookAdapter(
    [WebhookSkill(
        skill=SkillDef(
            skill_id="support.triage_ticket", version="1.0.0", side_effects="read_only",
            input_schema={"type": "object",
                "properties": {"ticket_text": {"type": "string"}}, "required": ["ticket_text"]},
            output_schema={"type": "object",
                "properties": {"category": {"type": "string"}, "priority": {"type": "string"}}},
        ),
        url="http://localhost:9001/triage_ticket",
    )],
    endpoint="grpc://localhost:50301", name="support-agent", department="support",
)

if __name__ == "__main__":
    run(adapter, db_path=DB_PATH, nats_url=NATS_URL, policy_secret=POLICY_SECRET, policy_path=POLICY_PATH)
