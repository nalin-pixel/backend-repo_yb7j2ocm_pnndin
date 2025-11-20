"""
Database Schemas

Hotel POS system schemas using Pydantic models.
Each Pydantic model corresponds to a MongoDB collection with the
collection name equal to the lowercase class name.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Item(BaseModel):
    """
    Menu/Inventory items
    Collection: "item"
    """
    name: str = Field(..., description="Item name")
    price: float = Field(..., ge=0, description="Unit price")
    sku: Optional[str] = Field(None, description="Stock keeping unit / code")
    stock: int = Field(0, ge=0, description="Units in stock")
    category: Optional[str] = Field(None, description="Category e.g., Drinks, Mains")
    is_active: bool = Field(True, description="Whether available for sale")

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    sku: Optional[str] = None
    stock: Optional[int] = Field(None, ge=0)
    category: Optional[str] = None
    is_active: Optional[bool] = None

class SaleItem(BaseModel):
    item_id: str = Field(..., description="Item ObjectId as string")
    quantity: int = Field(..., ge=1, description="Quantity sold")

class Sale(BaseModel):
    """
    Sales records
    Collection: "sale"
    """
    items: List[SaleItem]
    cashier: Optional[str] = Field(None, description="Cashier name or ID")
    note: Optional[str] = None
    subtotal: float = Field(..., ge=0)
    tax: float = Field(0, ge=0)
    total: float = Field(..., ge=0)
    paid: float = Field(..., ge=0)
    change: float = Field(..., ge=0)
    receipt_no: str = Field(..., description="Human-friendly receipt number")
    created_at: Optional[datetime] = None

# These schemas are used by the database viewer and for validation in endpoints.
