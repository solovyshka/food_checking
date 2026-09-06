"""Google Sheets sync for inventory: Текущее + Предложение."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from app.config import get_settings
from app.services.inventory import EntryRow

logger = logging.getLogger(__name__)

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


@dataclass
class ProposalItem:
    product_name: str
    quantity: Decimal
    unit: str


class GoogleSheetsError(RuntimeError):
    pass


def _require_configured() -> None:
    settings = get_settings()
    if not settings.has_google_sheets:
        raise GoogleSheetsError(
            "Google Sheets не настроен: задайте GOOGLE_SHEETS_SPREADSHEET_ID "
            "(см. docs/google-sheets.md)."
        )
    path = Path(settings.google_service_account_file)
    if not path.is_file():
        raise GoogleSheetsError(
            f"Нет файла service account: {path}. См. docs/google-sheets.md."
        )


def _client() -> gspread.Spreadsheet:
    _require_configured()
    settings = get_settings()
    creds = Credentials.from_service_account_file(
        settings.google_service_account_file,
        scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(settings.google_sheets_spreadsheet_id)


def _worksheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound as exc:
        raise GoogleSheetsError(
            f"Нет вкладки «{title}» в таблице. Создайте её или поправьте "
            "GOOGLE_SHEETS_TAB_*."
        ) from exc


def _clear_and_write(ws: gspread.Worksheet, rows: list[list[object]]) -> None:
    ws.clear()
    if not rows:
        return
    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")


def export_current_inventory(rows: list[EntryRow]) -> None:
    """Overwrite вкладку Текущее from confirmed DB snapshot."""
    settings = get_settings()
    ss = _client()
    ws = _worksheet(ss, settings.google_sheets_tab_current)
    data: list[list[object]] = [["Продукт", "Кол-во", "Ед.", "Дата"]]
    for row in rows:
        data.append(
            [
                row.product_name,
                format(row.quantity.normalize(), "f"),
                row.unit,
                row.entry_date.strftime("%d.%m.%Y"),
            ]
        )
    _clear_and_write(ws, data)
    logger.info("Sheets Текущее: %s rows", len(rows))


def export_proposal(
    rows: list[EntryRow],
    *,
    transcript: str,
    unknown_names: list[str] | None = None,
    missing_quantity: list[str] | None = None,
    skipped: list[str] | None = None,
) -> None:
    """Overwrite вкладку Предложение with Qwen table + transcript block."""
    settings = get_settings()
    ss = _client()
    ws = _worksheet(ss, settings.google_sheets_tab_proposal)
    data: list[list[object]] = [["Продукт", "Кол-во", "Ед.", "Транскрипт"]]
    first = True
    for row in rows:
        qty = format(row.quantity.normalize(), "f")
        data.append(
            [
                row.product_name,
                qty,
                row.unit,
                transcript if first else "",
            ]
        )
        first = False
    if first and transcript:
        data.append(["", "", "", transcript])

    notes: list[str] = []
    if unknown_names:
        notes.append("Нет в справочнике: " + ", ".join(unknown_names))
    if missing_quantity:
        notes.append("Без количества: " + ", ".join(missing_quantity))
    if skipped:
        notes.append("Не разобрал: " + ", ".join(skipped))
    if notes:
        data.append([])
        data.append(["Заметки"])
        for note in notes:
            data.append([note])

    _clear_and_write(ws, data)
    logger.info("Sheets Предложение: %s product rows", len(rows))


def read_proposal() -> list[ProposalItem]:
    """Read product rows from Предложение (skips notes / empty)."""
    settings = get_settings()
    ss = _client()
    ws = _worksheet(ss, settings.google_sheets_tab_proposal)
    values = ws.get_all_values()
    if not values:
        return []

    items: list[ProposalItem] = []
    # Detect header
    start = 0
    if values and str(values[0][0]).strip().lower() in {"продукт", "product"}:
        start = 1

    for raw in values[start:]:
        if not raw or not str(raw[0]).strip():
            # blank or notes section
            cell0 = str(raw[0]).strip().lower() if raw else ""
            if cell0 in {"заметки", "notes"}:
                break
            if not any(str(c).strip() for c in raw):
                break
            continue
        name = str(raw[0]).strip()
        if name.lower() in {"заметки", "notes"}:
            break
        qty_raw = raw[1].strip() if len(raw) > 1 else ""
        unit_raw = raw[2].strip() if len(raw) > 2 else ""
        if not qty_raw or not unit_raw:
            logger.warning("Sheets skip incomplete row: %r", raw[:3])
            continue
        try:
            quantity = Decimal(qty_raw.replace(",", "."))
        except (InvalidOperation, ValueError):
            logger.warning("Sheets skip bad qty %r for %r", qty_raw, name)
            continue
        items.append(ProposalItem(product_name=name, quantity=quantity, unit=unit_raw))
    return items


def spreadsheet_url() -> str:
    settings = get_settings()
    sid = settings.google_sheets_spreadsheet_id.strip()
    if not sid:
        return ""
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def spreadsheet_tab_url(tab_title: str) -> str:
    """Deep link to a specific worksheet tab (by title)."""
    base = spreadsheet_url()
    if not base:
        return ""
    ss = _client()
    ws = _worksheet(ss, tab_title)
    return f"{base}#gid={ws.id}"


def current_sheet_url() -> str:
    settings = get_settings()
    return spreadsheet_tab_url(settings.google_sheets_tab_current)


def proposal_sheet_url() -> str:
    settings = get_settings()
    return spreadsheet_tab_url(settings.google_sheets_tab_proposal)
