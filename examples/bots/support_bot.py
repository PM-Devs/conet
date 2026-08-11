# A plain, pre-existing "internal bot" -- no CoNET awareness at all, no LLM.
# Just a normal little HTTP service someone on the Support team already
# built and runs. The point is to wire THIS, as-is, into a CoNET colony
# afterward (e.g. with conet.gateway.webhook.WebhookAdapter) rather than
# rewriting it.
#
# Run:  python examples/bots/support_bot.py
# Try:  curl -X POST http://localhost:9001/triage_ticket \
#         -H "Content-Type: application/json" \
#         -d "{\"ticket_text\": \"I was double-charged on my last invoice\"}"

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Support Bot (plain, not a CoNET agent)")

_BILLING_WORDS = ("invoice", "charge", "charged", "refund", "billing", "payment")
_URGENT_WORDS = ("urgent", "asap", "immediately", "down", "outage")


class TriageRequest(BaseModel):
    ticket_text: str


class TriageResponse(BaseModel):
    category: str
    priority: str


@app.get("/health")
def health():
    return {"status": "ok", "bot": "support-bot"}


@app.post("/triage_ticket", response_model=TriageResponse)
def triage_ticket(req: TriageRequest):
    text = req.ticket_text.lower()
    category = "billing" if any(w in text for w in _BILLING_WORDS) else "general"
    priority = "high" if any(w in text for w in _URGENT_WORDS) else "low"
    return TriageResponse(category=category, priority=priority)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
