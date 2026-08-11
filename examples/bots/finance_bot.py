# Another plain, pre-existing "internal bot" -- no CoNET awareness, no LLM.
# Fakes a customer-balance lookup deterministically (same customer_id
# always returns the same numbers) so it feels like a real backend without
# needing a database.
#
# Run:  python examples/bots/finance_bot.py
# Try:  curl -X POST http://localhost:9002/get_balance \
#         -H "Content-Type: application/json" \
#         -d "{\"customer_id\": \"C-4471\"}"

import hashlib

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Finance Bot (plain, not a CoNET agent)")


class BalanceRequest(BaseModel):
    customer_id: str


class BalanceResponse(BaseModel):
    customer_id: str
    balance: float
    currency: str = "USD"


def _fake_balance(customer_id: str) -> float:
    digest = hashlib.sha256(customer_id.encode()).hexdigest()
    cents = int(digest[:8], 16) % 500_00  # 0.00 .. 4999.99
    return round(cents / 100, 2)


@app.get("/health")
def health():
    return {"status": "ok", "bot": "finance-bot"}


@app.post("/get_balance", response_model=BalanceResponse)
def get_balance(req: BalanceRequest):
    return BalanceResponse(customer_id=req.customer_id, balance=_fake_balance(req.customer_id))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9002)
