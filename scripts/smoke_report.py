from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import urllib.request

from app.services.inventory import EntryRow
from app.services.reports import report_entries_list

r = EntryRow(
    id=1,
    product_name="tvorog",
    quantity=Decimal("150"),
    unit="g",
    status="confirmed",
    entry_date=date(2026, 9, 5),
    recorded_at=datetime.now(ZoneInfo("Europe/Moscow")),
    kcal_per_100g=Decimal("100"),
)
url = report_entries_list([r], "consumption")
print(url)
print("http", urllib.request.urlopen(url, timeout=5).getcode())
