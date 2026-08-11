# The piece that actually "wires together" the three independent bots: it
# never talks to support_bot/finance_bot/inventory_bot directly, only to
# the colony -- discovering and calling each one's Skill through CoNET's
# real Discovery + Router, exactly like any CoNET-aware caller would.
#
# Run after: a local NATS server, all three plain bots (examples/bots/),
# and all three *_agent.py wrappers in this folder are running.
#
#   python examples/wired/orchestrator.py

import asyncio

from _shared import DB_PATH, POLICY_PATH, POLICY_SECRET

from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.runtime.router import Router

REQUESTER = "orchestrator"


async def main() -> None:
    store = Store(DB_PATH)
    policy = PolicyEngine(secret_key=POLICY_SECRET, policy_path=POLICY_PATH)
    discovery = Discovery(store, policy)
    router = Router(discovery, policy)

    ticket_text = (
        "I was double-charged on invoice for customer C-4471, "
        "also please check if SKU-1001 is in stock"
    )
    print(f"Incoming ticket: {ticket_text!r}\n")

    triage = await router.execute(REQUESTER, "support.triage_ticket", {"ticket_text": ticket_text})
    triage_output = dict(triage.output)
    print("support-agent  ->", triage_output)

    if triage_output.get("category") == "billing":
        balance = await router.execute(REQUESTER, "finance.get_balance", {"customer_id": "C-4471"})
        print("finance-agent  ->", dict(balance.output))

    stock = await router.execute(REQUESTER, "inventory.check_stock", {"sku": "SKU-1001"})
    print("inventory-agent ->", dict(stock.output))

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
