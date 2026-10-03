from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
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
