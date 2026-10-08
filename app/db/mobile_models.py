from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class MobileDailyEnergy(Base):
    __tablename__ = "mobile_daily_energy"
    entry_date: Mapped[date] = mapped_column(Date, primary_key=True)
    spent_kcal: Mapped[Decimal | None] = mapped_column(Numeric(9, 2), nullable=True)
    training_kcal: Mapped[Decimal | None] = mapped_column(Numeric(9, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MobileEnergyProfile(Base):
    __tablename__ = "mobile_energy_profiles"
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    height_cm: Mapped[Decimal] = mapped_column(Numeric(5, 2))


class MobilePerson(Base):
    __tablename__ = "mobile_person"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)


class MobileEntryDetails(Base):
    __tablename__ = "mobile_entry_details"
    entry_id: Mapped[int] = mapped_column(ForeignKey("consumption_entries.id"), primary_key=True)
    meal: Mapped[str] = mapped_column(String(16), nullable=False)
    nutrition_source: Mapped[str] = mapped_column(String(16), nullable=False)
    protein_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fat_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    carbs_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    macros_source: Mapped[str] = mapped_column(String(16), default="estimate", server_default="estimate")
    barcode: Mapped[str | None] = mapped_column(String(14), nullable=True)
    library_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MobileLibraryItem(Base):
    __tablename__ = "mobile_library_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MobileSubmission(Base):
    __tablename__ = "mobile_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MobileImage(Base):
    __tablename__ = "mobile_images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MobileTranscriptDetails(Base):
    __tablename__ = "mobile_transcript_details"
    transcript_id: Mapped[int] = mapped_column(ForeignKey("consumption_transcripts.id"), primary_key=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("mobile_images.id"))


class MobileParseJob(Base):
    __tablename__ = "mobile_parse_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), default="primary", server_default="primary")
    request_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24))
    inputs: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MobileParsedFood(Base):
    __tablename__ = "mobile_parsed_foods"
    __table_args__ = (UniqueConstraint("job_id", "transcript_id", "local_id", name="uq_mobile_food_job_local"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("mobile_parse_jobs.id"), index=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("consumption_transcripts.id"))
    local_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str] = mapped_column(String(32))
    amount_is_estimate: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MobileParsedNutrition(Base):
    __tablename__ = "mobile_parsed_nutrition"
    food_id: Mapped[int] = mapped_column(ForeignKey("mobile_parsed_foods.id"), primary_key=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str] = mapped_column(String(2))
    kcal_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    nutrition_source: Mapped[str] = mapped_column(String(16))
    portion_is_estimate: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(Text, default="")
    protein_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fat_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    carbs_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    macros_source: Mapped[str] = mapped_column(String(16), default="estimate", server_default="estimate")


class MobileBarcodeProduct(Base):
    __tablename__ = "mobile_barcode_products"
    barcode: Mapped[str] = mapped_column(String(14), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(2))
    kcal_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    protein_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fat_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    carbs_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MobileBarcodeLabelJob(Base):
    __tablename__ = "mobile_barcode_label_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), default="primary", server_default="primary")
    barcode: Mapped[str] = mapped_column(String(14))
    image_id: Mapped[str] = mapped_column(ForeignKey("mobile_images.id"))
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
