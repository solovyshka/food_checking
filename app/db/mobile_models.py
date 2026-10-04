from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class MobileEntryDetails(Base):
    __tablename__ = "mobile_entry_details"
    entry_id: Mapped[int] = mapped_column(ForeignKey("consumption_entries.id"), primary_key=True)
    meal: Mapped[str] = mapped_column(String(16), nullable=False)
    nutrition_source: Mapped[str] = mapped_column(String(16), nullable=False)


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
