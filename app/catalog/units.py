"""Closed set of storage units. Voice never chooses these."""

STORAGE_UNITS = frozenset(
    {
        "бутылка",
        "пачка",
        "банка",
        "шт",
        "кг",
        "десяток",
        "батон",
        "упаковка",
        "пучок",
        "палка",
    }
)

# Consumption ("съел"): grams or milliliters only.
CONSUMPTION_UNITS = frozenset({"г", "мл"})
