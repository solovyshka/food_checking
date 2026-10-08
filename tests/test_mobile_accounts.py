"""Two account isolation, including numeric IDs, UUID retries, photos and callbacks."""
import io,json,os,tempfile,unittest
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock,patch
from uuid import uuid4

from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests import test_mobile_api as api_tests
from app.db.models import Base,ConsumptionEntry
from app.db.mobile_models import MobileParseJob,MobileDailyEnergy,MobileEnergyProfile,MobileBarcodeLabelJob,MobileImage
from app.db.session import get_db
from app.mobile_api import app,attempts
from app.mobile_accounts import callback_scope
from app.services.grok_analysis import callback_token,now_utc,webhook_payload
from app.mobile_barcodes import label_token


class MobileAccountsTest(unittest.TestCase):
    def setUp(self):
        self.id=str(uuid4())
        self.second_token='second-test-token-'+'y'*40
        self.config=tempfile.TemporaryDirectory()
        path=Path(self.config.name)/'accounts.json'
        path.write_text(json.dumps({'version':1,'accounts':[{'id':self.id,'name':'Пользователь 2',
            'schema':'food_user_'+self.id.replace('-',''),'token':self.second_token,'code':'87654321'}]}))
        path.chmod(0o600)
        self.env=patch.dict(os.environ,{'FOOD_MOBILE_ACCOUNTS_FILE':str(path)})
        self.env.start()
        self.engines={key:create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool) for key in ('primary',self.id)}
        for engine in self.engines.values():Base.metadata.create_all(engine)
        def session():
            with Session(self.engines['primary']) as db:yield db
        app.dependency_overrides[get_db]=session
        self.bind_patch=patch('app.mobile_accounts.bind_for_account',side_effect=lambda bind,account:self.engines[account.id])
        self.bind_patch.start()
        self.client=TestClient(app)
        self.headers={'Authorization':'Bearer '+os.environ['FOOD_MOBILE_TOKEN']}
        self.second={'Authorization':'Bearer '+self.second_token}
        attempts.clear()

    def tearDown(self):
        self.client.close();app.dependency_overrides.clear();self.bind_patch.stop();self.env.stop();self.config.cleanup()
        for engine in self.engines.values():engine.dispose()

    def diary(self,headers):
        response=self.client.get('/api/mobile/diary?entry_date=2026-10-03',headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_pair_diary_numeric_ids_retry_profile_and_training(self):
        first=self.client.post('/api/mobile/pair',json={'code':'12345678'}).json()
        second=self.client.post('/api/mobile/pair',json={'code':'87654321'}).json()
        self.assertEqual(first['token'],os.environ['FOOD_MOBILE_TOKEN'])
        self.assertEqual(second['token'],self.second_token)
        self.assertEqual(second['user']['id'],self.id)
        self.assertEqual(self.diary(self.second)['items'],[])
        self.assertEqual(self.diary(self.second)['user']['name'],'Пользователь 2')
        body=api_tests.MobileApiTest.body(self)
        first_id=self.client.post('/api/mobile/entries',json=body,headers=self.headers).json()['ids'][0]
        self.assertEqual(self.client.patch(f'/api/mobile/entries/{first_id}',json={**body['items'][0],'meal':'breakfast'},headers=self.second).status_code,404)
        self.assertEqual(self.client.delete(f'/api/mobile/entries/{first_id}',headers=self.second).status_code,404)
        # Same UUID and same numeric row IDs are independent in the two diaries.
        body['items'][0]['name']='Другой продукт'
        body['items'][0]['kcal_per_100g']='100'
        response=self.client.post('/api/mobile/entries',json=body,headers=self.second)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['ids'][0],first_id)
        self.assertEqual(self.diary(self.headers)['total_kcal'],'104.0')
        self.assertEqual(self.diary(self.second)['total_kcal'],'200.0')
        self.assertEqual(self.client.post('/api/mobile/entries',json=body,headers=self.second).json(),response.json())
        profile=self.client.get('/api/mobile/energy/profile',headers=self.second).json()
        self.assertIsNone(profile['birth_date']);self.assertIsNone(profile['sex']);self.assertIsNone(profile['height_cm'])
        self.assertIsNone(profile['weight_kg']);self.assertIsNone(profile['age'])
        self.assertEqual(self.client.put('/api/mobile/energy/profile',json={'weight_kg':65,'height_cm':165},headers=self.second).status_code,422)
        with patch('app.mobile_api.profile_today',return_value=date(2026,10,3)):
            profile=self.client.put('/api/mobile/energy/profile',json={'name':'Анна','birth_date':'1996-10-03','sex':'female','weight_kg':65,'height_cm':165},headers=self.second)
            self.assertEqual(profile.status_code,200,profile.text)
            self.assertEqual(profile.json()['resting_kcal'],'1370.3')
            self.assertEqual(profile.json()['age'],30)
        path='/api/mobile/days/2026-10-03/activity'
        self.client.put(path,json={'training_kcal':300},headers=self.second)
        self.assertEqual(self.diary(self.second)['energy_delta'],'-1470.3')
        self.assertIsNone(self.diary(self.headers)['training_kcal'])
        self.assertIsNone(self.diary(self.headers)['energy_profile']['weight_kg'])
        self.assertEqual(self.diary(self.second)['user']['name'],'Анна')
        self.assertEqual(self.client.get('/api/mobile/energy/profile',headers=self.headers).json()['birth_date'],'1994-08-27')
        self.client.delete(f'/api/mobile/entries/{first_id}',headers=self.second)
        self.assertEqual(self.diary(self.headers)['total_kcal'],'104.0')
        # A client-supplied scope cannot select another user's ordinary API session.
        scoped=self.client.get('/api/mobile/diary?entry_date=2026-10-03&account='+self.id,headers=self.headers)
        self.assertEqual(scoped.json()['total_kcal'],'104.0')

    def test_images_queue_jobs_catalog_and_callback_capabilities(self):
        image=io.BytesIO();Image.new('RGB',(10,10),'white').save(image,format='JPEG')
        uploaded=self.client.post('/api/mobile/images',files={'file':('photo.jpg',image.getvalue(),'image/jpeg')},headers=self.headers)
        self.assertEqual(uploaded.status_code,200,uploaded.text)
        foreign=uploaded.json()['id']
        queued={'request_id':str(uuid4()),'entry_date':'2026-10-03','meal':'lunch','text':'Обед','image_id':foreign}
        self.assertEqual(self.client.post('/api/mobile/queue',json=queued,headers=self.second).status_code,422)
        self.assertEqual(self.client.post('/api/mobile/barcodes/labels',json={'request_id':str(uuid4()),'barcode':'3017620422003','image_id':foreign},headers=self.second).status_code,422)
        own=self.client.post('/api/mobile/images',files={'file':('photo.jpg',image.getvalue(),'image/jpeg')},headers=self.second).json()['id']
        self.assertNotEqual(own,foreign)
        code='3017620422003'
        product={'name':'Мой продукт','unit':'г','kcal_per_100g':123,'verified':True}
        self.assertEqual(self.client.put('/api/mobile/barcodes/products/'+code,json=product,headers=self.headers).status_code,200)
        with patch('app.mobile_barcodes.lookup_openfoodfacts',new=AsyncMock(return_value=None)):
            self.assertFalse(self.client.get('/api/mobile/barcodes/products/'+code,headers=self.second).json()['found'])
        job_id=str(uuid4())
        for owner in ('primary',self.id):
            with Session(self.engines[owner]) as db:
                job=MobileParseJob(id=job_id,owner_id=owner,request_hash='hash',nonce=str(uuid4()),status='running',inputs=[],expires_at=now_utc()+timedelta(hours=1))
                db.add(job);db.commit()
                if owner==self.id:
                    payload=webhook_payload(job)
                    access={'Authorization':'Bearer '+callback_token(job)}
        self.assertIn('?account='+self.id,payload['input_url'])
        self.assertNotIn(self.second_token,json.dumps(payload))
        self.assertEqual(self.client.get('/api/mobile/grok/jobs/'+job_id+'/input?account='+self.id,headers=access).status_code,200)
        self.assertEqual(self.client.get('/api/mobile/grok/jobs/'+job_id+'/input',headers=access).status_code,401)
        self.assertEqual(self.client.get('/api/mobile/grok/jobs/'+job_id+'/input?account='+self.id,headers=self.headers).status_code,401)
        result={'error':'Отмена теста'}
        self.assertEqual(self.client.post('/api/mobile/grok/jobs/'+job_id+'/result?account='+self.id,json=result,headers=access).status_code,200)
        self.assertEqual(self.client.get('/api/mobile/queue/jobs/'+job_id,headers=self.second).json()['status'],'failed')
        self.assertEqual(self.client.get('/api/mobile/queue/jobs/'+job_id,headers=self.headers).json()['status'],'running')
        label_id=str(uuid4())
        with Session(self.engines[self.id]) as db:
            job=MobileBarcodeLabelJob(id=label_id,owner_id=self.id,barcode='03017620422003',image_id=own,image_url='https://example.com/photo.jpg',request_hash='hash',nonce=str(uuid4()),status='running',expires_at=now_utc()+timedelta(hours=1))
            db.add(job);db.commit()
            label_access={'Authorization':'Bearer '+label_token(job)}
        path='/api/mobile/barcodes/labels/'+label_id
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)
        self.assertEqual(self.client.get(path+'/input?account='+self.id,headers=label_access).status_code,200)
        self.assertEqual(self.client.get(path+'/input',headers=label_access).status_code,401)
        self.assertEqual(self.client.post(path+'/result?account='+self.id,json={'error':'Отмена теста'},headers=label_access).status_code,200)
        self.assertEqual(self.diary(self.headers)['items'],[])

    def test_failed_credentials_and_profile_validation(self):
        self.assertEqual(self.client.get('/api/mobile/diary?entry_date=2026-10-03',headers={'Authorization':'Bearer invalid'}).status_code,401)
        self.assertEqual(self.client.post('/api/mobile/pair',json={'code':'11111111'}).status_code,401)
        self.assertEqual(self.client.get('/api/mobile/grok/jobs/'+str(uuid4())+'/input?account='+str(uuid4())).status_code,401)
        for birth,sex in (('2999-01-01','male'),('1899-01-01','male'),('1994-01-01','invalid')):
            self.assertEqual(self.client.put('/api/mobile/energy/profile',json={'name':'Имя','birth_date':birth,'sex':sex,'weight_kg':65,'height_cm':165},headers=self.second).status_code,422)
