#!/usr/bin/env python3
"""Write Grok Bot webhook settings into mobile.env.

Run from a terminal: ssh -t firebat 'sudo python3 /opt/food_checking/deploy/set-grok-webhook.py'
Reads the keyboard via /dev/tty. Do not pipe a script into this process.
"""

from __future__ import annotations

import os
import sys
import termios
from pathlib import Path

PATH = Path("/opt/secrets/food_checking/mobile.env")
PROXY = "http://127.0.0.1:10809"


def ask(prompt: str, secret: bool = False) -> str:
    fd = os.open("/dev/tty", os.O_RDWR)
    try:
        os.write(fd, prompt.encode())
        old = termios.tcgetattr(fd)
        if secret:
            hidden = termios.tcgetattr(fd)
            hidden[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        try:
            data = b""
            while b"\n" not in data:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                data += chunk
        finally:
            if secret:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                os.write(fd, b"\n")
        return data.split(b"\n", 1)[0].decode().strip()
    finally:
        os.close(fd)


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("Запусти через sudo: файл секретов доступен только root.")
    if not PATH.is_file():
        sys.exit(f"Нет файла {PATH}")
    url = ask("GROK_BOT_WEBHOOK_URL: ")
    key = ask("GROK_BOT_WEBHOOK_KEY: ", secret=True)
    if not url.startswith("https://") or not key:
        sys.exit("Нужны https-адрес и непустой ключ. Файл не изменён.")
    updates = {
        "GROK_BOT_WEBHOOK_URL": url,
        "GROK_BOT_WEBHOOK_KEY": key,
        "GROK_BOT_PROXY_URL": PROXY,
    }
    lines = PATH.read_text().splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0]
        if name in updates:
            out.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            out.append(line)
    for name, value in updates.items():
        if name not in seen:
            out.append(f"{name}={value}")
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text("\n".join(out).rstrip() + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)
    os.chmod(PATH, 0o600)
    print("Записано:", ", ".join(updates))


if __name__ == "__main__":
    main()
