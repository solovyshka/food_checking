"""Isolated checks; never write sample meals into the production diary."""
import os
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['FOOD_MOBILE_TOKEN'] = 'test-token-' + 'x' * 40
os.environ['FOOD_MOBILE_PAIR_CODE'] = '12345678'

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, ConsumptionEntry
from app.db.session import get_db
from app.mobile_api import app, attempts
from app.services.parser import ParsedInventory, ParsedItem


class MobileApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        def sessions():
            with self.factory() as session:
                yield session
        app.dependency_overrides[get_db] = sessions
        attempts.clear()
        self.client = TestClient(app)
        self.headers = {'Authorization': 'Bearer ' + os.environ['FOOD_MOBILE_TOKEN']}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def body(self):
        return {'request_id': str(uuid4()), 'entry_date': '2026-10-03', 'meal':'breakfast',
                'items':[{'name':'Молоко 2.5%', 'quantity':'200', 'unit':'г',
                          'kcal_per_100g':'52', 'nutrition_source':'label'}]}

    def post(self, body):
        return self.client.post('/api/mobile/entries', json=body, headers=self.headers)

    def diary(self, day='2026-10-03'):
        return self.client.get('/api/mobile/diary', params={'entry_date':day}, headers=self.headers).json()

    def test_auth_pair_rate_limit(self):
        self.assertEqual(self.client.get('/api/mobile/diary?entry_date=2026-10-03').status_code,401)
        result = self.client.post('/api/mobile/pair', json={'code':'12345678'})
        self.assertEqual(result.json()['token'],os.environ['FOOD_MOBILE_TOKEN'])
        for _ in range(4):
            self.assertEqual(self.client.post('/api/mobile/pair',json={'code':'00000000'}).status_code,401)
        self.assertEqual(self.client.post('/api/mobile/pair',json={'code':'12345678'}).status_code,429)

    def test_math_idempotency_conflict_and_date(self):
        body = self.body()
        first = self.post(body)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.post(body).json(),first.json())
        self.assertEqual(self.diary()['total_kcal'],'104.0')
        self.assertEqual(len(self.diary()['items']),1)
        self.assertEqual(self.diary('2026-10-02')['items'],[])
        body['items'][0]['quantity'] = '300'
        self.assertEqual(self.post(body).status_code,409)
        self.assertEqual(self.diary()['total_kcal'],'104.0')

    def test_invalid_batch_is_not_partially_saved(self):
        body = self.body()
        body['items'].append({'name':'Хлеб', 'quantity':'NaN', 'unit':'г'})
        self.assertEqual(self.post(body).status_code,422)
        self.assertEqual(self.diary()['items'],[])
        body['items'][1]['quantity'] = '-1'
        self.assertEqual(self.post(body).status_code,422)

    def test_preview_does_not_save_and_reuses_label(self):
        self.post(self.body())
        parsed = ParsedInventory(entry_date=None,items=[ParsedItem(name='Молоко 2.5%',quantity=Decimal(100),unit='г',kcal_per_100g=Decimal(999)),ParsedItem(name='Котлета',quantity=Decimal(150),unit='г',kcal_per_100g=Decimal(280))],skipped=[])
        with patch('app.mobile_api.parse_consumption_text',AsyncMock(return_value=(parsed,'mock'))):
            result = self.client.post('/api/mobile/preview',json={'text':'Молоко и котлета'},headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(Decimal(result.json()['items'][0]['kcal_per_100g']),Decimal(52))
        self.assertEqual(result.json()['items'][0]['nutrition_source'],'label')
        self.assertEqual(result.json()['items'][1]['nutrition_source'],'estimate')
        self.assertEqual(len(self.diary()['items']),1)

    def test_edit_zero_unknown_and_soft_delete(self):
        entry_id = self.post(self.body()).json()['ids'][0]
        item = {'name':'Вода', 'quantity':'200', 'unit':'мл','kcal_per_100g':'0','nutrition_source':'manual','meal':'lunch'}
        result = self.client.patch(f'/api/mobile/entries/{entry_id}',json=item,headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.diary()['total_kcal'],'0.0')
        self.assertEqual(self.diary()['items'][0]['meal'],'lunch')
        item['kcal_per_100g'] = None
        self.client.patch(f'/api/mobile/entries/{entry_id}',json=item,headers=self.headers)
        self.assertEqual(self.diary()['missing_kcal'],1)
        self.assertEqual(self.client.delete(f'/api/mobile/entries/{entry_id}',headers=self.headers).status_code,200)
        self.assertEqual(self.diary()['items'],[])
        self.assertEqual(self.client.delete(f'/api/mobile/entries/{entry_id}',headers=self.headers).status_code,404)

    def test_voice_uses_local_pipeline_without_saving(self):
        with patch('app.mobile_api.transcribe_for_pipeline',AsyncMock(return_value=('молоко 200 грамм','gigaam',''))):
            result = self.client.post('/api/mobile/transcribe',files={'file':('voice.m4a',b'audio','audio/mp4')},headers=self.headers)
        self.assertEqual(result.json()['text'],'молоко 200 грамм')
        self.assertEqual(self.diary()['items'],[])

if __name__ == '__main__':
    unittest.main()
