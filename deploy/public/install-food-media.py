#!/usr/bin/env python3
"""Run as root on OVH; preserve other domains and the existing food TLS config."""
from datetime import datetime, timezone
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import sys

stage=Path(__file__).resolve().parent
subprocess.run(['id','food-media'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False).returncode == 0 or subprocess.run(['useradd','--system','--home','/var/lib/food-checking-media','--shell','/usr/sbin/nologin','food-media'],check=True)
root=Path('/opt/food-checking-media');root.mkdir(exist_ok=True)
shutil.copy2(stage/'food-media-server.py',root/'server.py')
subprocess.run(['install','-d','-o','food-media','-g','food-media','-m','700','/var/lib/food-checking-media'],check=True)
secret=Path('/etc/food-checking-media.env')
if not secret.exists():
    secret.write_text('FOOD_MEDIA_TOKEN='+secrets.token_urlsafe(48)+'\nFOOD_MEDIA_ROOT=/var/lib/food-checking-media\n')
    secret.chmod(0o600)
for file in ['food-media.service','food-media-cleanup.service','food-media-cleanup.timer']:
    shutil.copy2(stage/file,Path('/etc/systemd/system')/file)
site=Path('/etc/nginx/sites-enabled/food-consumption-https')
original=site.read_text()
backup=Path('/var/backups/food-media-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700);shutil.copy2(site,backup/'nginx.conf')
if 'location /media/food/' not in original:
    template=(stage/'ovh-food-https.conf').read_text()
    start=template.index('    # Expiring links')
    end=template.index('    location /api/mobile/',start)
    marker='    location /api/mobile/ {'
    if original.count(marker)!=1:raise SystemExit('Food nginx route does not match expected configuration')
    site.write_text(original.replace(marker,template[start:end]+marker))
try:
    subprocess.run(['nginx','-t'],check=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','food-media','food-media-cleanup.timer'],check=True)
    subprocess.run(['systemctl','restart','food-media'],check=True)
    subprocess.run(['systemctl','reload','nginx'],check=True)
except Exception:
    site.write_text(original)
    raise
print('OVH temporary photo hosting enabled; lifetime 2 hours, cleanup every minute')
