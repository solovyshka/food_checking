#!/usr/bin/env python3
"""Build on the box, keep the signing key, publish the same APK to both doors."""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--skip-build',action='store_true')
parser.add_argument('--skip-publish',action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
app = root/'mobile'
flutter = Path.home()/'develop/flutter/bin/flutter'
sdk = Path.home()/'Android/Sdk'
secret_dir = Path.home()/'.config/food_checking'
secret_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
secret_dir.chmod(0o700)
properties = secret_dir/'android-release.properties'
keystore = secret_dir/'android-release.jks'
if not properties.exists():
    if keystore.exists(): raise SystemExit('Signing key exists; restore its properties rather than replacing the key')
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, FOOD_SIGN_PASSWORD=password)
    subprocess.run(['keytool','-genkeypair','-keystore',str(keystore),'-alias','food',
        '-storepass:env','FOOD_SIGN_PASSWORD','-keypass:env','FOOD_SIGN_PASSWORD',
        '-keyalg','RSA','-keysize','2048','-validity','10000','-dname','CN=Food Diary, O=Personal'],env=env,check=True)
    properties.write_text(f'storeFile={keystore}\nstorePassword={password}\nkeyPassword={password}\nkeyAlias=food\n')
    properties.chmod(0o600)
    keystore.chmod(0o600)
build_properties = app/'android/key.properties'
if not build_properties.exists(): build_properties.symlink_to(properties)
if not args.skip_build:
    for command in (['pub','get'],['analyze'],['test'],['build','apk','--release']):
        subprocess.run([str(flutter),*command],cwd=app,check=True)
apk = app/'build/app/outputs/flutter-apk/app-release.apk'
tools = sorted((sdk/'build-tools').iterdir())[-1]
subprocess.run([str(tools/'apksigner'),'verify','--verbose',str(apk)],check=True)
badging = subprocess.check_output([str(tools/'aapt'),'dump','badging',str(apk)],text=True)
package = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'",badging)
if not package or package[1] != 'ru.solovyshka.food_checking_mobile': raise SystemExit('Unexpected APK package')
code, version = int(package[2]),package[3]
sha = hashlib.sha256(apk.read_bytes()).hexdigest()
path = f'/app/releases/{code}-{sha[:16]}/update.bin'
release = {'versionCode':code,'versionName':version,'sizeBytes':apk.stat().st_size,
    'sha256':sha,'apkPath':path,'apkUrl':'https://food-consumption.solovyshka.com'+path}
out = root/'deploy/out'
out.mkdir(parents=True,exist_ok=True)
(out/'version.json').write_text(json.dumps(release,indent=2)+'\n')
if args.skip_publish:
    print('Built',version,code,apk)
    raise SystemExit(0)
ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
       '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
       '-o', 'ControlPath=none']
rsync = ['rsync', '-az', '--timeout=90', '-e', ' '.join(ssh)]
for host,remote_home in [('ubuntu@51.254.219.211','/home/ubuntu'),('root@135.106.218.22','/root')]:
    remote_stage = remote_home+'/food-consumption-publish'
    subprocess.run([*ssh,host,'mkdir -p '+remote_stage+'/release'],check=True)
    subprocess.run([*rsync,str(apk),host+':'+remote_stage+'/release/food_consumption.apk'],check=True)
    subprocess.run([*rsync,str(out/'version.json'),host+':'+remote_stage+'/release/version.json'],check=True)
    subprocess.run([*rsync,str(root/'deploy/public/index.html'),host+':'+remote_stage+'/release/index.html'],check=True)
    subprocess.run([*rsync,str(root/'deploy/public/install-release.py'),host+':'+remote_stage+'/install-release.py'],check=True)
    subprocess.run([*ssh,host,
        'sudo -n python3 '+remote_stage+'/install-release.py '+remote_stage+'/release'],check=True)
print('Published',version,'to both public doors')
