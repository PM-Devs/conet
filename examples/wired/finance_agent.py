# Wires the plain finance_bot.py (examples/bots/finance_bot.py, must
# already be running on :9002) into the colony as a governed CoNET agent --
# finance_bot's own code is never touched or made aware of any of this.
#
# Run (after starting finance_bot.py and a local NATS server):
#   python examples/wired/finance_agent.py

from _shared import DB_PATH, NATS_URL, POLICY_PATH, POLICY_SECRET

from conet.gateway.webhook import WebhookAdapter, WebhookSkill
from conet.sdk import SkillDef, run

adapter = WebhookAdapter(
    [WebhookSkill(
        skill=SkillDef(
            skill_id="finance.get_balance", version="1.0.0", side_effects="read_only",
            input_schema={"type": "object",
                "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
            output_schema={"type": "object",
                "properties": {
                    "customer_id": {"type": "string"}, "balance": {"type": "number"}, "currency": {"type": "string"},
                }},
        ),
        url="http://localhost:9002/get_balance",
    )],
    endpoint="grpc://localhost:50302", name="finance-agent", department="finance",
)

if __name__ == "__main__":
    run(adapter, db_path=DB_PATH, nats_url=NATS_URL, policy_secret=POLICY_SECRET, policy_path=POLICY_PATH)
