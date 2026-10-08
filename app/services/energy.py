"""Single-owner resting expenditure estimate, with measurements effective by day."""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.mobile_models import MobileEnergyProfile, MobilePerson

BIRTH_DATE = date(1994, 8, 27)


def profile_today():
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def age_on(day, birth_date=BIRTH_DATE):
    if birth_date is None:
        return None
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


def person_values(db):
    account = db.info.get("mobile_account")
    primary = account is None or account.id == "primary"
    person = db.get(MobilePerson, 1)
    return {"name": person.name if person else account.name if account else "Мой дневник",
        "birth_date": person.birth_date if person else BIRTH_DATE if primary else None,
        "sex": person.sex if person else "male" if primary else None,
        "account_id": account.id if account else "primary"}


def profile_on(db, day):
    return db.scalars(select(MobileEnergyProfile).where(MobileEnergyProfile.effective_date <= day)
        .order_by(MobileEnergyProfile.effective_date.desc()).limit(1)).first()


def resting_kcal(profile, day, person=None):
    person = person if person is not None else {"birth_date":BIRTH_DATE,"sex":"male"}
    age = age_on(day, person["birth_date"])
    if profile is None or age is None or person["sex"] not in ("male","female"):
        return None
    return (10 * profile.weight_kg + Decimal("6.25") * profile.height_cm - 5 * age + (5 if person["sex"] == "male" else -161)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def serialize_profile(profile, day, person=None):
    person = person if person is not None else {"birth_date":BIRTH_DATE,"sex":"male","name":"Мой дневник","account_id":"primary"}
    rest = resting_kcal(profile, day, person)
    return {"birth_date": person["birth_date"].isoformat() if person["birth_date"] else None,
        "sex":person["sex"], "age":age_on(day, person["birth_date"]), "name":person["name"], "account_id":person["account_id"],
        "weight_kg": str(profile.weight_kg) if profile else None,
        "height_cm": str(profile.height_cm) if profile else "170.00" if person["account_id"] == "primary" else None,
        "effective_date": profile.effective_date.isoformat() if profile else None,
        "resting_kcal": str(rest) if rest is not None else None}


def daily_energy(db, day, energy, total, estimated, incomplete):
    profile = profile_on(db, day)
    person = person_values(db)
    rest = resting_kcal(profile, day, person)
    legacy = energy is not None and energy.source == "manual"
    rest_only = energy is not None and energy.source == "rest_only"
    training = energy.training_kcal if energy is not None and not legacy else None
    spent = energy.spent_kcal if legacy else rest if rest_only else (
        rest + training if rest is not None and training is not None else None)
    delta = (total - spent).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if spent is not None else None
    return {"spent_kcal": str(spent) if spent is not None else None,
        "training_kcal": str(training) if training is not None else None,
        "resting_kcal": str(rest) if rest is not None else None,
        "energy_mode": "legacy_total" if legacy else "rest_only" if rest_only else "resting_training",
        "training_kcal_missing": bool(rest_only),
        "energy_profile": serialize_profile(profile, day, person),
        "energy_delta": str(delta) if delta is not None else None,
        "energy_delta_estimated": bool(spent is not None and (estimated or not legacy)),
        "energy_delta_incomplete": bool(spent is not None and incomplete)}
