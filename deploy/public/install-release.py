#!/usr/bin/env python3
"""Verify an uploaded APK and atomically switch the public release."""
import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('stage',type=Path)
args = parser.parse_args()
stage = args.stage
metadata = json.loads((stage/'version.json').read_text())
apk = stage/'food_consumption.apk'
sha = hashlib.sha256(apk.read_bytes()).hexdigest()
code = metadata['versionCode']
if not isinstance(code,int) or code <= 0 or metadata['sha256'] != sha or metadata['sizeBytes'] != apk.stat().st_size:
    raise SystemExit('Release size or checksum validation failed')
identifier = f'{code}-{sha[:16]}'
if metadata['apkPath'] != f'/app/releases/{identifier}/update.bin':
    raise SystemExit('Unexpected immutable release URL')
base = Path('/var/www/food-consumption-releases')
base.mkdir(mode=0o755,exist_ok=True)
release = base/identifier
release.mkdir(mode=0o755,exist_ok=True)
if (release/'food_consumption.apk').exists() and hashlib.sha256((release/'food_consumption.apk').read_bytes()).hexdigest() != sha:
    raise SystemExit('Immutable release already exists with different content')
for name in ('food_consumption.apk','version.json','index.html'):
    shutil.copyfile(stage/name,release/name)
    (release/name).chmod(0o644)
for name,target in [('update.bin','food_consumption.apk'),('releases','..')]:
    link = release/name
    if not link.exists(): link.symlink_to(target)
current = Path('/var/www/food-consumption-app')
if current.exists() and not current.is_symlink():
    legacy = base/('legacy-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    current.rename(legacy)
temporary = current.with_name('.food-consumption-next')
temporary.unlink(missing_ok=True)
temporary.symlink_to(release,target_is_directory=True)
os.replace(temporary,current)
print('Published',metadata['versionName'],f'({code})','SHA256',sha)
