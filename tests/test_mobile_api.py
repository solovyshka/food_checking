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

from app.db.models import Base, ConsumptionEntry, ConsumptionTranscript
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

    def test_queue_saves_text_without_parsing_or_changing_calories(self):
        self.post(self.body())
        body = {'request_id': str(uuid4()), 'entry_date': '2026-10-03', 'meal': 'lunch', 'text': '  котлета и рис  '}
        with patch('app.mobile_api.parse_consumption_text', AsyncMock()) as parser:
            first = self.client.post('/api/mobile/queue', json=body, headers=self.headers)
            retry = self.client.post('/api/mobile/queue', json=body, headers=self.headers)
            parser.assert_not_called()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), retry.json())
        rows = self.client.get('/api/mobile/queue', headers=self.headers).json()['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], 'котлета и рис')
        self.assertEqual(rows[0]['meal'], 'lunch')
        self.assertEqual(self.diary()['total_kcal'], '104.0')
        self.assertEqual(self.diary()['queued_count'], 1)
        self.assertEqual(self.diary('2026-10-02')['queued_count'], 0)
        body['text'] = 'другая еда'
        self.assertEqual(self.client.post('/api/mobile/queue', json=body, headers=self.headers).status_code, 409)

    def test_queue_rejects_empty_text_and_protects_processing_records(self):
        body = {'request_id': str(uuid4()), 'entry_date': '2026-10-03', 'meal': 'breakfast', 'text': ' '}
        self.assertEqual(self.client.post('/api/mobile/queue', json=body).status_code, 401)
        self.assertEqual(self.client.get('/api/mobile/queue').status_code, 401)
        self.assertEqual(self.client.post('/api/mobile/queue', json=body, headers=self.headers).status_code, 422)
        body['text'] = 'молоко'
        entry_id = self.client.post('/api/mobile/queue', json=body, headers=self.headers).json()['id']
        with self.factory() as db:
            entry = db.get(ConsumptionTranscript, entry_id)
            entry.parse_batch_id = str(uuid4())
            db.commit()
        self.assertEqual(self.client.delete(f'/api/mobile/queue/{entry_id}', headers=self.headers).status_code, 409)
        with self.factory() as db:
            db.get(ConsumptionTranscript, entry_id).parse_batch_id = None
            db.commit()
        self.assertEqual(self.client.delete(f'/api/mobile/queue/{entry_id}', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete(f'/api/mobile/queue/{entry_id}', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/mobile/queue', headers=self.headers).json()['items'], [])
        self.assertEqual(self.diary()['queued_count'], 0)

    def _restore_grok_env(self):
        previous = {
            name: os.environ.get(name)
            for name in ('GROK_BOT_WEBHOOK_URL', 'GROK_BOT_WEBHOOK_KEY', 'GROK_BOT_PROXY_URL')
        }

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)
        return previous

    def test_grok_test_needs_auth_and_reports_missing_access(self):
        self.assertEqual(self.client.post('/api/mobile/grok/test', json={}).status_code, 401)
        self._restore_grok_env()
        os.environ.pop('GROK_BOT_WEBHOOK_URL', None)
        os.environ.pop('GROK_BOT_WEBHOOK_KEY', None)
        missing = self.client.post('/api/mobile/grok/test', json={'text': 'привет'}, headers=self.headers)
        self.assertEqual(missing.status_code, 503)
        self.assertIn('не настроен', missing.json()['detail'])
        os.environ['GROK_BOT_WEBHOOK_URL'] = 'http://example.test/hook'
        os.environ['GROK_BOT_WEBHOOK_KEY'] = 'secret-key'
        insecure = self.client.post('/api/mobile/grok/test', json={}, headers=self.headers)
        self.assertEqual(insecure.status_code, 503)

    def test_grok_test_posts_message_without_saving_food(self):
        self._restore_grok_env()
        os.environ['GROK_BOT_WEBHOOK_URL'] = 'https://example.test/hook'
        os.environ['GROK_BOT_WEBHOOK_KEY'] = 'secret-key'
        os.environ.pop('GROK_BOT_PROXY_URL', None)
        sent = {}

        class Response:
            status_code = 200

        class Client:
            def __init__(self, *args, **kwargs):
                sent['follow_redirects'] = kwargs.get('follow_redirects')
                sent['proxy'] = kwargs.get('proxy')

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, headers=None, json=None):
                sent['url'] = url
                sent['headers'] = headers
                sent['json'] = json
                return Response()

        with patch('app.services.grok_bot.httpx.AsyncClient', Client):
            result = self.client.post('/api/mobile/grok/test', json={'text': 'молоко', 'has_image': True}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['accepted'])
        self.assertNotIn('secret-key', result.text)
        self.assertEqual(sent['url'], 'https://example.test/hook')
        self.assertEqual(sent['headers']['Authorization'], 'Bearer secret-key')
        self.assertEqual(sent['json']['kind'], 'test')
        self.assertEqual(sent['json']['message'], 'молоко')
        self.assertTrue(sent['json']['has_image'])
        self.assertFalse(sent['follow_redirects'])
        self.assertEqual(sent['proxy'], 'http://127.0.0.1:10809')
        self.assertEqual(self.diary()['items'], [])

if __name__ == '__main__':
    unittest.main()
