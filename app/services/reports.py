"""Static HTML reports for Telegram links and document attachments."""

from __future__ import annotations

import html
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.config import get_settings
from app.services.inventory import EntryRow, KIND_LABELS, PendingBatch

_CSS = """
:root { color-scheme: light dark; }
body {
  font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  margin: 0;
  padding: 24px 16px 48px;
  line-height: 1.45;
  background: #f6f5f2;
  color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
  body { background: #121212; color: #eee; }
  table { background: #1c1c1c; }
  th { background: #2a2a2a; }
  td, th { border-color: #333; }
  .meta { color: #aaa; }
}
main { max-width: 720px; margin: 0 auto; }
h1 { font-size: 1.35rem; font-weight: 650; margin: 0 0 8px; }
.meta { color: #555; font-size: 0.92rem; margin-bottom: 20px; }
table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  border: 1px solid #ddd;
  padding: 10px 12px;
  text-align: left;
  font-size: 0.95rem;
}
th { background: #eceae4; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.note {
  margin-top: 16px;
  padding: 12px 14px;
  border-left: 3px solid #888;
  background: rgba(0,0,0,0.04);
  white-space: pre-wrap;
  word-break: break-word;
}
"""


@dataclass(frozen=True)
class ReportFile:
    path: Path
    url: str
    filename: str


def _fmt_qty(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _fmt_kcal(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format(value.normalize(), "f")


def _reports_dir() -> Path:
    settings = get_settings()
    path = Path(settings.reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _page(title: str, meta: str, table_html: str, notes: list[str] | None = None) -> str:
    notes_html = ""
    if notes:
        blocks = "".join(
            f'<div class="note">{html.escape(n)}</div>' for n in notes if n
        )
        notes_html = blocks
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)}</title>
  <style>{_CSS}</style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <div class="meta">{html.escape(meta)}</div>
  {table_html}
  {notes_html}
</main>
</body>
</html>
"""


def _consumption_table(rows: list[EntryRow], *, with_date: bool) -> str:
    if with_date:
        head = (
            "<tr><th>Дата</th><th>Продукт</th>"
            '<th class="num">Кол-во</th><th>Ед.</th>'
            '<th class="num">ккал/100г</th></tr>'
        )
    else:
        head = (
            "<tr><th>Продукт</th>"
            '<th class="num">Кол-во</th><th>Ед.</th>'
            '<th class="num">ккал/100г</th></tr>'
        )
    body = []
    for row in rows:
        cells = []
        if with_date:
            cells.append(f"<td>{html.escape(row.entry_date.strftime('%d.%m.%Y'))}</td>")
        cells.append(f"<td>{html.escape(row.product_name)}</td>")
        cells.append(f'<td class="num">{html.escape(_fmt_qty(row.quantity))}</td>')
        cells.append(f"<td>{html.escape(row.unit)}</td>")
        cells.append(f'<td class="num">{html.escape(_fmt_kcal(row.kcal_per_100g))}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def _inventory_table(rows: list[EntryRow], *, with_date: bool) -> str:
    if with_date:
        head = (
            "<tr><th>Дата</th><th>Продукт</th>"
            '<th class="num">Кол-во</th><th>Ед.</th></tr>'
        )
    else:
        head = (
            "<tr><th>Продукт</th>"
            '<th class="num">Кол-во</th><th>Ед.</th></tr>'
        )
    body = []
    for row in rows:
        cells = []
        if with_date:
            cells.append(f"<td>{html.escape(row.entry_date.strftime('%d.%m.%Y'))}</td>")
        cells.append(f"<td>{html.escape(row.product_name)}</td>")
        cells.append(f'<td class="num">{html.escape(_fmt_qty(row.quantity))}</td>')
        cells.append(f"<td>{html.escape(row.unit)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def write_report_html(content: str, *, filename: str) -> ReportFile:
    """Write HTML file; return path + public URL + download name."""
    settings = get_settings()
    token = secrets.token_urlsafe(18)
    path = _reports_dir() / f"{token}.html"
    path.write_text(content, encoding="utf-8")
    base = settings.public_base_url.rstrip("/")
    safe_name = filename if filename.endswith(".html") else f"{filename}.html"
    return ReportFile(
        path=path,
        url=f"{base}/r/{token}.html",
        filename=safe_name,
    )


def report_entries_list(rows: list[EntryRow], kind: str) -> ReportFile | None:
    if not rows:
        return None
    label = KIND_LABELS[kind]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    if kind == "consumption":
        table = _consumption_table(rows[:200], with_date=True)
        title = label
        meta = f"Сформировано {now} · записей: {min(len(rows), 200)}"
    else:
        day = rows[0].entry_date.strftime("%d.%m.%Y")
        table = _inventory_table(rows[:200], with_date=False)
        title = f"{label} на {day}"
        meta = f"Сформировано {now} · записей: {min(len(rows), 200)}"
    notes = []
    if len(rows) > 200:
        notes.append(f"Показаны первые 200 из {len(rows)} записей.")
    html_doc = _page(
        title=title,
        meta=meta,
        table_html=table,
        notes=notes,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = "syel" if kind == "consumption" else "nalichie"
    return write_report_html(html_doc, filename=f"{name}_{stamp}.html")


def report_pending_batch(batch: PendingBatch) -> ReportFile | None:
    if not batch.rows:
        return None
    label = KIND_LABELS[batch.kind]
    recorded = batch.recorded_at.strftime("%d.%m.%Y %H:%M")
    table = (
        _consumption_table(batch.rows, with_date=False)
        if batch.kind == "consumption"
        else _inventory_table(batch.rows, with_date=False)
    )
    notes: list[str] = []
    if batch.transcript:
        notes.append(f"Транскрипт: «{batch.transcript}»")
    if batch.unknown_names:
        notes.append("Нет в справочнике: " + ", ".join(batch.unknown_names))
    if batch.missing_quantity:
        notes.append("Не понял количество: " + ", ".join(batch.missing_quantity))
    if batch.skipped:
        notes.append("Не разобрал: " + ", ".join(batch.skipped))
    if batch.timing_note:
        notes.append(batch.timing_note)
    html_doc = _page(
        title=f"{label} — проверка перед записью",
        meta=f"Дата записи: {recorded} · позиций: {len(batch.rows)}",
        table_html=table,
        notes=notes,
    )
    stamp = batch.recorded_at.strftime("%Y%m%d_%H%M")
    name = "syel_check" if batch.kind == "consumption" else "nalichie_check"
    return write_report_html(html_doc, filename=f"{name}_{stamp}.html")
