# Third plain, pre-existing "internal bot" -- no CoNET awareness, no LLM.
# Fakes a stock-check deterministically (same sku always returns the same
# numbers) so it feels like a real backend without needing a database.
#
# Run:  python examples/bots/inventory_bot.py
# Try:  curl -X POST http://localhost:9003/check_stock \
#         -H "Content-Type: application/json" \
#         -d "{\"sku\": \"SKU-1001\"}"

import hashlib

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Inventory Bot (plain, not a CoNET agent)")


class StockRequest(BaseModel):
    sku: str


class StockResponse(BaseModel):
    sku: str
    in_stock: bool
    quantity: int


def _fake_quantity(sku: str) -> int:
    digest = hashlib.sha256(sku.encode()).hexdigest()
    return int(digest[:8], 16) % 200  # 0 .. 199 units


@app.get("/health")
def health():
    return {"status": "ok", "bot": "inventory-bot"}


@app.post("/check_stock", response_model=StockResponse)
def check_stock(req: StockRequest):
    quantity = _fake_quantity(req.sku)
    return StockResponse(sku=req.sku, in_stock=quantity > 0, quantity=quantity)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9003)
