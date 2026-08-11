# Wires the plain inventory_bot.py (examples/bots/inventory_bot.py, must
# already be running on :9003) into the colony as a governed CoNET agent --
# inventory_bot's own code is never touched or made aware of any of this.
#
# Run (after starting inventory_bot.py and a local NATS server):
#   python examples/wired/inventory_agent.py

from _shared import DB_PATH, NATS_URL, POLICY_PATH, POLICY_SECRET

from conet.gateway.webhook import WebhookAdapter, WebhookSkill
from conet.sdk import SkillDef, run

adapter = WebhookAdapter(
    [WebhookSkill(
        skill=SkillDef(
            skill_id="inventory.check_stock", version="1.0.0", side_effects="read_only",
            input_schema={"type": "object",
                "properties": {"sku": {"type": "string"}}, "required": ["sku"]},
            output_schema={"type": "object",
                "properties": {
                    "sku": {"type": "string"}, "in_stock": {"type": "boolean"}, "quantity": {"type": "integer"},
                }},
        ),
        url="http://localhost:9003/check_stock",
    )],
    endpoint="grpc://localhost:50303", name="inventory-agent", department="operations",
)

if __name__ == "__main__":
    run(adapter, db_path=DB_PATH, nats_url=NATS_URL, policy_secret=POLICY_SECRET, policy_path=POLICY_PATH)
