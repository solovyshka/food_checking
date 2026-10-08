"""Local seed catalogue and personal recipes; never recalculate past diary rows."""
import json
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.mobile_models import MobileLibraryItem, MobileSubmission
from app.mobile_accounts import authorize, mobile_db
from app.services.grok_analysis import digest_json

NUTRIENTS = ("kcal_per_100g", "protein_per_100g", "fat_per_100g", "carbs_per_100g")


class LibraryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["catalog", "product", "recipe"]
    id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=1, strict=True)


class Values(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    kcal_per_100g: Decimal = Field(ge=0, le=2000, max_digits=6, decimal_places=2)
    protein_per_100g: Decimal = Field(ge=0, le=200, max_digits=5, decimal_places=2)
    fat_per_100g: Decimal = Field(ge=0, le=200, max_digits=5, decimal_places=2)
    carbs_per_100g: Decimal = Field(ge=0, le=200, max_digits=5, decimal_places=2)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v):
        if not v.strip():
            raise ValueError("Укажите название")
        return v.strip()

    @field_validator(*NUTRIENTS, mode="before")
    @classmethod
    def no_boolean(cls, v):
        if isinstance(v, bool):
            raise ValueError("Укажите число")
        return v


class Ingredient(Values):
    quantity: Decimal = Field(gt=0, le=100000, max_digits=9, decimal_places=3)
    unit: Literal["г"] = "г"
    library_ref: LibraryReference | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def no_boolean_quantity(cls, v):
        return cls.no_boolean(v)


class LibraryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    revision: int = Field(ge=0, strict=True)
    kind: Literal["product", "recipe"]
    name: str = Field(min_length=1, max_length=255)
    product: Values | None = None
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=60)
    yield_g: Decimal | None = Field(default=None, gt=0, le=100000, max_digits=9, decimal_places=3)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v):
        return Values.clean_name(v)

    @field_validator("yield_g", mode="before")
    @classmethod
    def no_boolean_weight(cls, v):
        return Values.no_boolean(v)

    @model_validator(mode="after")
    def valid_content(self):
        if self.kind == "product":
            if self.product is None or self.ingredients or self.yield_g is not None:
                raise ValueError("Укажите калории и БЖУ продукта на 100 г")
        elif self.product is not None or not self.ingredients or self.yield_g is None:
            raise ValueError("Добавьте ингредиенты и вес готового блюда")
        return self


class LibraryDelete(BaseModel):
    request_id: UUID
    revision: int = Field(ge=1, strict=True)


@lru_cache(maxsize=1)
def catalog():
    path = Path(__file__).resolve().parent.parent / "mobile/assets/food_catalog.json"
    return json.loads(path.read_text())


def calculate(body: LibraryWrite):
    if body.kind == "product":
        return {**body.product.model_dump(mode="json"), "name": body.name, "unit": "г"}
    nutrients = {}
    for field in NUTRIENTS:
        total = sum((getattr(i, field) * i.quantity / 100 for i in body.ingredients), Decimal(0))
        value = (total * 100 / body.yield_g).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if value > (2000 if field == "kcal_per_100g" else 200):
            raise HTTPException(422, "Проверьте ингредиенты и вес готового блюда: значения на 100 г слишком велики")
        nutrients[field] = str(value)
    return {"name": body.name, "unit": "г", **nutrients,
            "yield_g": str(body.yield_g), "ingredients": [i.model_dump(mode="json", exclude_none=True) for i in body.ingredients]}


def serialize(item):
    return {"id": item.id, "kind": item.kind, "revision": item.revision, "data": item.data}


def prior(db, request_id, digest):
    item = db.get(MobileSubmission, str(request_id))
    if item:
        if item.payload_hash != digest:
            raise HTTPException(409, "Этот запрос уже сохранён с другими данными")
        return item.response


def register_routes(app):
    Db = Annotated[Session, Depends(mobile_db)]
    Auth = Depends(authorize)

    @app.get("/api/mobile/catalog", dependencies=[Auth])
    def seed_catalog():
        return catalog()

    @app.get("/api/mobile/library", dependencies=[Auth])
    def library(db: Db):
        rows = db.scalars(select(MobileLibraryItem).where(MobileLibraryItem.active.is_(True))
                          .order_by(MobileLibraryItem.name, MobileLibraryItem.id)).all()
        return {"items": [serialize(i) for i in rows], "catalog_version": catalog()["version"]}

    @app.put("/api/mobile/library/{item_id}", dependencies=[Auth])
    def save_library(item_id: UUID, body: LibraryWrite, db: Db):
        digest = digest_json({"kind": "library_save", "id": str(item_id), **body.model_dump(mode="json")})
        if (response := prior(db, body.request_id, digest)) is not None:
            return response
        item = db.scalar(select(MobileLibraryItem).where(MobileLibraryItem.id == str(item_id)).with_for_update())
        if item is None:
            if body.revision != 0:
                raise HTTPException(409, "Продукт или рецепт изменился. Обновите список")
            if db.scalar(select(func.count()).select_from(MobileLibraryItem).where(MobileLibraryItem.active.is_(True))) >= 2000:
                raise HTTPException(422, "В личном справочнике уже 2000 записей")
            item = MobileLibraryItem(id=str(item_id), revision=0, active=True)
            db.add(item)
        elif not item.active or item.revision != body.revision or item.kind != body.kind:
            raise HTTPException(409, "Продукт или рецепт изменился. Обновите список")
        item.data = calculate(body)
        item.name, item.kind = body.name, body.kind
        item.revision += 1
        response = serialize(item)
        db.add(MobileSubmission(id=str(body.request_id), payload_hash=digest, response=response))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if (response := prior(db, body.request_id, digest)) is not None:
                return response
            raise HTTPException(409, "Справочник обновлялся одновременно. Обновите список") from None
        return response

    @app.delete("/api/mobile/library/{item_id}", dependencies=[Auth])
    def delete_library(item_id: UUID, body: LibraryDelete, db: Db):
        digest = digest_json({"kind": "library_delete", "id": str(item_id), **body.model_dump(mode="json")})
        if (response := prior(db, body.request_id, digest)) is not None:
            return response
        item = db.scalar(select(MobileLibraryItem).where(MobileLibraryItem.id == str(item_id)).with_for_update())
        if item is None or not item.active:
            raise HTTPException(404, "Продукт или рецепт не найден")
        if item.revision != body.revision:
            raise HTTPException(409, "Продукт или рецепт изменился. Обновите список")
        item.active = False
        item.revision += 1
        response = {"deleted": True}
        db.add(MobileSubmission(id=str(body.request_id), payload_hash=digest, response=response))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if (response := prior(db, body.request_id, digest)) is not None:
                return response
            raise HTTPException(409, "Повторите удаление") from None
        return response
