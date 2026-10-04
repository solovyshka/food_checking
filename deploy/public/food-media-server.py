#!/usr/bin/env python3
"""OVH-only temporary JPEG links. Authenticated uploads, expiring public reads."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.environ.get('FOOD_MEDIA_ROOT', '/var/lib/food-checking-media'))
TOKEN = os.environ.get('FOOD_MEDIA_TOKEN', '')
TTL = 7200
MAX_SIZE = 4 * 1024 * 1024
LINK = re.compile(r'^/media/food/([a-f0-9]{64})\.jpg$')


def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ROOT / 'links.sqlite', timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS links (id TEXT PRIMARY KEY, expires INTEGER NOT NULL)')
    return db


def cleanup():
    now = int(time.time())
    with connect() as db:
        for (key,) in db.execute('SELECT id FROM links WHERE expires <= ?', (now,)).fetchall():
            if re.fullmatch('[a-f0-9]{64}', key): (ROOT / (key + '.jpg')).unlink(missing_ok=True)
        db.execute('DELETE FROM links WHERE expires <= ?', (now,))
        live = {key for (key,) in db.execute('SELECT id FROM links')}
    # Recover files left by a crash between writing the JPEG and committing its row.
    for path in ROOT.iterdir():
        if (re.fullmatch(r'[a-f0-9]{64}\.jpg', path.name) and path.stem not in live or path.name.endswith('.tmp')):
            if path.stat().st_mtime < now - TTL: path.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args): pass  # Link addresses and credentials never enter logs.

    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != '/internal/food-media/upload': return self.reply(404, {'error': 'not found'})
        if len(TOKEN) < 32 or not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + TOKEN).encode()):
            return self.reply(401, {'error': 'unauthorized'})
        try: size = int(self.headers.get('Content-Length', '0'))
        except ValueError: size = 0
        if not 0 < size <= MAX_SIZE: return self.reply(413, {'error': 'invalid image size'})
        data = self.rfile.read(size)
        if len(data) != size or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
            return self.reply(422, {'error': 'JPEG required'})
        key, expiry = secrets.token_hex(32), int(time.time()) + TTL
        tmp, final = ROOT / (key + '.tmp'), ROOT / (key + '.jpg')
        try:
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(final)
            with connect() as db: db.execute('INSERT INTO links VALUES (?, ?)', (key, expiry))
        except Exception:
            tmp.unlink(missing_ok=True); final.unlink(missing_ok=True)
            return self.reply(503, {'error': 'storage unavailable'})
        self.reply(200, {'path': '/media/food/' + key + '.jpg', 'expires_at': expiry,
                         'sha256': hashlib.sha256(data).hexdigest()})

    def do_GET(self): self.photo(False)
    def do_HEAD(self): self.photo(True)

    def photo(self, head):
        match = LINK.fullmatch(urlsplit(self.path).path)
        if not match: return self.reply(404, {'error': 'not found'})
        key = match[1]
        with connect() as db: row = db.execute('SELECT expires FROM links WHERE id = ?', (key,)).fetchone()
        if row is None: return self.reply(404, {'error': 'not found'})
        if row[0] <= time.time(): return self.reply(410, {'error': 'link expired'})
        try: data = (ROOT / (key + '.jpg')).read_bytes()
        except OSError: return self.reply(404, {'error': 'not found'})
        self.send_response(200)
        for name, value in [('Content-Type','image/jpeg'), ('Content-Length',str(len(data))),
            ('Cache-Control','no-store, max-age=0'), ('Referrer-Policy','no-referrer'),
            ('X-Robots-Tag','noindex, nofollow, noarchive'), ('X-Content-Type-Options','nosniff')]:
            self.send_header(name, value)
        self.end_headers()
        if not head: self.wfile.write(data)


if __name__ == '__main__':
    with connect(): pass
    if '--cleanup' in sys.argv: cleanup()
    else:
        if len(TOKEN) < 32: raise SystemExit('FOOD_MEDIA_TOKEN not configured')
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('FOOD_MEDIA_PORT', '8093'))), Handler)
        server.daemon_threads = True
        server.serve_forever()
