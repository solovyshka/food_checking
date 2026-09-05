"""Backward-compatible entrypoint: inventory bot.

Prefer:
  python -m app.bot.inventory   # наличие
  python -m app.bot.consumption # калории / съел
"""

from app.bot.inventory import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
