#!/usr/bin/env python3
"""Enable food-consumption HTTPS after DNS is ready. Run as root on OVH."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

stage = Path(__file__).resolve().parent
domain = 'food-consumption.solovyshka.com'
certificate = Path('/etc/letsencrypt/live') / domain / 'fullchain.pem'
if not certificate.exists():
    raise SystemExit('Certificate not found: issue it with certbot --webroot first')
stream = Path('/etc/nginx/stream.d/audio-guide.conf')
site = Path('/etc/nginx/sites-enabled/food-consumption-https')
original_stream = stream.read_bytes()
original_site = site.read_bytes() if site.exists() else None
backup = Path('/var/backups/food-consumption-https-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
shutil.copy2(stream,backup/'stream.conf')
if site.exists(): shutil.copy2(site,backup/'site.conf')
try:
    text = original_stream.decode()
    line = f'    {domain} 127.0.0.1:8443;'
    if line not in text:
        marker = '    audio.solovyshka.com 127.0.0.1:8443;'
        if marker not in text: raise RuntimeError('Existing audio-guide TLS map does not match expected configuration')
        stream.write_text(text.replace(marker,marker+'\n'+line,1))
    site.write_bytes((stage/'ovh-food-https.conf').read_bytes())
    site.chmod(0o644)
    subprocess.run(['nginx','-t'],check=True)
    subprocess.run(['systemctl','reload','nginx'],check=True)
except Exception:
    stream.write_bytes(original_stream)
    if original_site is None: site.unlink(missing_ok=True)
    else: site.write_bytes(original_site)
    raise
hooks = Path('/etc/letsencrypt/renewal-hooks/deploy')
hooks.mkdir(parents=True,exist_ok=True)
hook = hooks / 'food-consumption-nginx'
hook.write_text('#!/bin/sh\nset -e\n/usr/sbin/nginx -t\n/bin/systemctl reload nginx\n')
hook.chmod(0o755)
print('HTTPS enabled for',domain,'with automatic nginx reload on certificate renewal')
