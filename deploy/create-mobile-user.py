#!/usr/bin/env python3
"""Create an isolated diary on the box; credentials stay in its private config."""
import argparse,fcntl,json,os,pwd,secrets,subprocess,sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine,text
from sqlalchemy.schema import CreateSchema

parser=argparse.ArgumentParser()
parser.add_argument('--name',required=True)
args=parser.parse_args()
name=args.name.strip()
if not name or len(name)>64:raise SystemExit('Name must contain 1–64 characters')
root=Path('/opt/food_checking')
sys.path.insert(0,str(root))
for path in (root/'.env',Path('/opt/secrets/food_checking/mobile.env')):
    os.environ.update({key:value for key,value in dotenv_values(path).items() if value is not None})
service_user=pwd.getpwnam('solovyshka')
path=Path(os.environ.get('FOOD_MOBILE_ACCOUNTS_FILE', str(Path(service_user.pw_dir)/'.config/food_checking/mobile-accounts.json')))
os.environ['FOOD_MOBILE_ACCOUNTS_FILE']=str(path)
from app.mobile_accounts import accounts
from app.config import get_settings
from app.db import mobile_models
from app.db.mobile_models import MobilePerson
if not path.parent.exists():
    path.parent.mkdir(mode=0o700,parents=True)
    os.chown(path.parent,service_user.pw_uid,service_user.pw_gid)
lock=os.open(path.parent/'.mobile-users.lock',os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(lock,fcntl.LOCK_EX)
existing=accounts()
raw=json.loads(path.read_text()) if path.exists() else {'version':1,'accounts':[]}
matched=next((entry for entry in raw['accounts'] if entry['name']==name),None)
if matched:
    print(json.dumps({'id':matched['id'],'name':matched['name'],'code':matched['code'],'created':False},ensure_ascii=False))
    raise SystemExit(0)
used_codes={value.code for value in existing}
code=''
while not code or code in used_codes:code=f'{secrets.randbelow(100000000):08d}'
id=str(uuid4())
schema='food_user_'+id.replace('-','')
entry={'id':id,'name':name,'schema':schema,'token':secrets.token_urlsafe(48),'code':code}
engine=create_engine(get_settings().database_url)
with engine.begin() as conn:
    revision=conn.scalar(text('SELECT version_num FROM public.alembic_version'))
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    migration_config=Config(str(root/'alembic.ini'))
    migration_config.set_main_option('script_location',str(root/'alembic'))
    assert revision==ScriptDirectory.from_config(migration_config).get_current_head(), 'Upgrade server before adding users'
    conn.execute(CreateSchema(schema))
# Apply the complete migration chain so new diaries have exactly the same schema.
subprocess.run([str(root/'venv/bin/alembic'),'-x','schema='+schema,'upgrade','head'],cwd=root,check=True)
with engine.begin() as conn:
    mapped=conn.execution_options(schema_translate_map={None:schema})
    mapped.execute(MobilePerson.__table__.insert().values(id=1,name=name))
raw['accounts'].append(entry)
path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
tmp=path.with_suffix('.new')
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as output:json.dump(raw,output,ensure_ascii=False,indent=2);output.write('\n')
os.chown(tmp,service_user.pw_uid,service_user.pw_gid)
os.replace(tmp,path)
# Register an explicit location so systemd's app and admin tools use the same file.
mobile_env=Path('/opt/secrets/food_checking/mobile.env')
lines=mobile_env.read_text().splitlines()
lines=[line for line in lines if not line.startswith('FOOD_MOBILE_ACCOUNTS_FILE=')]
lines.append('FOOD_MOBILE_ACCOUNTS_FILE='+str(path))
env_stat=mobile_env.stat()
env_tmp=mobile_env.with_name(mobile_env.name+'.'+uuid4().hex+'.tmp')
fd=os.open(env_tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,env_stat.st_mode & 0o777)
with os.fdopen(fd,'w') as output:output.write('\n'.join(lines)+'\n')
os.chown(env_tmp,env_stat.st_uid,env_stat.st_gid)
os.replace(env_tmp,mobile_env)
print(json.dumps({'id':id,'name':name,'code':code,'created':True},ensure_ascii=False))
