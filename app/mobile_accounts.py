"""Per-phone credentials and isolated SQLAlchemy sessions for mobile diaries."""
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    schema: str | None
    token: str = field(repr=False)
    code: str = field(repr=False)


def accounts_file():
    return Path(os.environ.get("FOOD_MOBILE_ACCOUNTS_FILE", str(Path.home()/".config/food_checking/mobile-accounts.json")))


@lru_cache(maxsize=4)
def _read_accounts(path, mtime, size):
    return json.loads(Path(path).read_text())


def accounts():
    primary_token = os.environ.get("FOOD_MOBILE_TOKEN", "")
    if len(primary_token) < 32:
        raise HTTPException(503, "Подключение приложения пока не настроено")
    values = [Account("primary", "Мой дневник", None, primary_token, os.environ.get("FOOD_MOBILE_PAIR_CODE", ""))]
    path = accounts_file()
    try:
        if path.exists():
            stat = path.stat()
            if stat.st_mode & 0o077:
                raise ValueError("Private configuration permissions")
            raw = _read_accounts(str(path), stat.st_mtime_ns, stat.st_size)
            if raw.get("version") != 1 or len(raw["accounts"]) > 100:
                raise ValueError("Invalid account configuration")
            for entry in raw["accounts"]:
                account_id = str(UUID(entry["id"]))
                schema = entry["schema"]
                if schema != "food_user_" + UUID(account_id).hex or not re.fullmatch(r"food_user_[a-f0-9]{32}", schema):
                    raise ValueError("Invalid diary schema")
                if len(entry["token"]) < 32 or not re.fullmatch(r"[0-9]{8,32}", entry["code"]):
                    raise ValueError("Invalid credentials")
                name = entry["name"].strip()
                if not name or len(name) > 64:
                    raise ValueError("Invalid name")
                values.append(Account(account_id, name, schema, entry["token"], entry["code"]))
            for key in ("id", "schema", "token", "code"):
                items = [getattr(value, key) for value in values]
                if len(items) != len(set(items)):
                    raise ValueError("Duplicate account credentials")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(503, "Настройки пользователей недоступны") from None
    return values


def authorize(authorization: Annotated[str | None, Header()] = None):
    matched = None
    for account in accounts():
        if secrets.compare_digest((authorization or "").encode(), ("Bearer " + account.token).encode()):
            matched = account
    if matched is None:
        raise HTTPException(401, "Откройте настройки и подключитесь к коробке")
    return matched


def account_by_code(code):
    matched = None
    for account in accounts():
        if account.code and secrets.compare_digest(code.encode(), account.code.encode()):
            matched = account
    if matched is None:
        raise HTTPException(401, "Неверный код подключения")
    return matched


def bind_for_account(bind, account):
    return bind.execution_options(schema_translate_map={None: account.schema}) if account.schema else bind


def mobile_db(request: Request, db: Annotated[Session, Depends(get_db)]):
    path = request.url.path
    callback = (re.fullmatch(r"/api/mobile/grok/jobs/[^/]+/(input|result|images/[^/]+)", path)
        or re.fullmatch(r"/api/mobile/barcodes/labels/[^/]+/(input|result)", path))
    if callback:
        account_id = request.query_params.get("account", "primary")
        account = next((value for value in accounts() if value.id == account_id), None)
        if account is None:
            raise HTTPException(401, "Нет доступа к этой задаче")
        # The handler checks the job capability after selecting its isolated schema.
    else:
        account = authorize(request.headers.get("Authorization"))
    db.bind = bind_for_account(db.get_bind(), account)
    db.info["mobile_account"] = account
    yield db


def account_id(db):
    account = db.info.get("mobile_account")
    return account.id if account else "primary"


def callback_scope(job):
    owner = job.owner_id or "primary"
    return "" if owner == "primary" else "?account=" + str(UUID(owner))
