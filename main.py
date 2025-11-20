import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.params import Query
from typing import List, Optional
from datetime import datetime
from bson import ObjectId

from database import db, create_document
from pydantic import BaseModel

app = FastAPI(title="Hotel POS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Helpers
# -----------------------------

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id format")


def serialize(doc):
    if not doc:
        return doc
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    # convert nested lists with item_id
    if "items" in doc and isinstance(doc["items"], list):
        for it in doc["items"]:
            if isinstance(it, dict) and "item_id" in it and isinstance(it["item_id"], ObjectId):
                it["item_id"] = str(it["item_id"])
    return doc


# -----------------------------
# Root & health
# -----------------------------
@app.get("/")
def read_root():
    return {"message": "Hotel POS Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    return response


# -----------------------------
# Schemas for requests
# -----------------------------
class ItemIn(BaseModel):
    name: str
    price: float
    sku: Optional[str] = None
    stock: int = 0
    category: Optional[str] = None
    is_active: bool = True


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    sku: Optional[str] = None
    stock: Optional[int] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class SaleItemIn(BaseModel):
    item_id: str
    quantity: int


class SaleIn(BaseModel):
    items: List[SaleItemIn]
    cashier: Optional[str] = None
    note: Optional[str] = None
    paid: float


# -----------------------------
# Inventory Endpoints
# -----------------------------
@app.get("/api/items")
def list_items(q: Optional[str] = None, active: Optional[bool] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    filter_q = {}
    if q:
        filter_q["name"] = {"$regex": q, "$options": "i"}
    if active is not None:
        filter_q["is_active"] = active
    items = list(db["item"].find(filter_q).sort("name", 1))
    return [serialize(x) for x in items]


@app.post("/api/items", status_code=201)
def create_item(payload: ItemIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    doc = payload.model_dump()
    new_id = db["item"].insert_one({**doc, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}).inserted_id
    return serialize(db["item"].find_one({"_id": new_id}))


@app.put("/api/items/{item_id}")
def update_item(item_id: str, payload: ItemUpdate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.utcnow()
    result = db["item"].update_one({"_id": oid(item_id)}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return serialize(db["item"].find_one({"_id": oid(item_id)}))


@app.delete("/api/items/{item_id}")
def delete_item(item_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    result = db["item"].delete_one({"_id": oid(item_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}


# -----------------------------
# Sales Endpoints
# -----------------------------

def generate_receipt_no() -> str:
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    seq = db["counters"].find_one_and_update(
        {"_id": "receipt"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    num = seq.get("seq", 1)
    return f"R{ts}-{num:04d}"


@app.post("/api/sales", status_code=201)
def create_sale(payload: SaleIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items in sale")

    # Fetch items and compute totals
    line_items = []
    subtotal = 0.0
    for it in payload.items:
        item_doc = db["item"].find_one({"_id": oid(it.item_id), "is_active": True})
        if not item_doc:
            raise HTTPException(status_code=400, detail=f"Item not available: {it.item_id}")
        if item_doc.get("stock", 0) < it.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {item_doc['name']}")
        price = float(item_doc.get("price", 0))
        line_total = price * it.quantity
        subtotal += line_total
        line_items.append({
            "item_id": item_doc["_id"],
            "name": item_doc.get("name"),
            "price": price,
            "quantity": it.quantity,
            "line_total": line_total,
        })

    tax = round(subtotal * 0.0, 2)  # tax 0% for simplicity; adjust if needed
    total = round(subtotal + tax, 2)
    if payload.paid < total:
        raise HTTPException(status_code=400, detail="Paid amount is less than total")
    change = round(payload.paid - total, 2)

    # Generate receipt no
    receipt_no = generate_receipt_no()

    sale_doc = {
        "items": line_items,
        "cashier": payload.cashier,
        "note": payload.note,
        "subtotal": round(subtotal, 2),
        "tax": tax,
        "total": total,
        "paid": float(payload.paid),
        "change": change,
        "receipt_no": receipt_no,
        "created_at": datetime.utcnow(),
    }

    # Write sale and decrement stock atomically per item (best-effort simple)
    session = None
    try:
        # Insert sale
        sale_id = db["sale"].insert_one(sale_doc).inserted_id
        # Update stock for each item
        for li in line_items:
            db["item"].update_one({"_id": li["item_id"]}, {"$inc": {"stock": -li["quantity"]}, "$set": {"updated_at": datetime.utcnow()}})
        saved = db["sale"].find_one({"_id": sale_id})
        return serialize(saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sales")
def list_sales(limit: int = Query(50, ge=1, le=500), start: Optional[str] = None, end: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    filt = {}
    if start or end:
        dr = {}
        if start:
            dr["$gte"] = datetime.fromisoformat(start)
        if end:
            dr["$lte"] = datetime.fromisoformat(end)
        filt["created_at"] = dr
    sales = list(db["sale"].find(filt).sort("created_at", -1).limit(limit))
    return [serialize(s) for s in sales]


@app.get("/api/receipt/{receipt_no}")
def get_receipt(receipt_no: str):
    sale = db["sale"].find_one({"receipt_no": receipt_no})
    if not sale:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return serialize(sale)


# -----------------------------
# Stats Endpoints
# -----------------------------
@app.get("/api/stats/top")
def stats_top(start: Optional[str] = None, end: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    match_stage = {}
    if start or end:
        dr = {}
        if start:
            dr["$gte"] = datetime.fromisoformat(start)
        if end:
            dr["$lte"] = datetime.fromisoformat(end)
        match_stage["created_at"] = dr

    pipeline = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    pipeline.extend([
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.item_id", "quantity": {"$sum": "$items.quantity"}}},
        {"$sort": {"quantity": -1}},
    ])
    agg = list(db["sale"].aggregate(pipeline))

    if not agg:
        return {"most_selling": None, "least_selling": None}

    # Fetch item details
    ids = [x["_id"] for x in agg]
    items_map = {doc["_id"]: doc for doc in db["item"].find({"_id": {"$in": ids}})}

    top = agg[0]
    bottom = agg[-1]

    def build(entry):
        it = items_map.get(entry["_id"]) or {}
        return {
            "item_id": str(entry["_id"]),
            "name": it.get("name"),
            "quantity": entry["quantity"],
            "price": float(it.get("price", 0)),
            "category": it.get("category"),
        }

    return {"most_selling": build(top), "least_selling": build(bottom)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
