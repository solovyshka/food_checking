"""GTIN lookup, isolated label jobs and legacy diary retries."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from test_mobile_api import MobileApiTest
from app.db.mobile_models import MobileBarcodeProduct, MobileBarcodeLabelJob, MobileImage, MobileSubmission
from app.services.barcodes import normalize_barcode, lookup_openfoodfacts, BarcodeLookupUnavailable, OFF_REQUESTS
from app.services.grok_analysis import now_utc
from app.services.grok_bot import GrokDispatchUnknown
import httpx
import unittest

CODE = '3017620422003'
CANONICAL = '03017620422003'
VALUES = {'name': 'Factory food', 'unit': 'г', 'kcal_per_100g': '100',
          'protein_per_100g': '4', 'fat_per_100g': '0', 'carbs_per_100g': None}
LOOKUP = {**VALUES, 'source': 'openfoodfacts', 'source_url': 'https://world.openfoodfacts.org/product/'+CODE, 'verified': False}


class BarcodeApiTest(MobileApiTest):
    def test_catalog_auth_scope_cache_and_portion(self):
        path = '/api/mobile/barcodes/products/'+CODE
        self.assertEqual(self.client.get(path).status_code, 401)
        with patch('app.mobile_barcodes.lookup_openfoodfacts', AsyncMock(return_value=LOOKUP)) as external:
            first = self.client.get(path, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['product']['barcode'], CANONICAL)
            self.assertFalse(first.json()['product']['verified'])
            self.assertEqual(first.json()['product']['fat_per_100g'], '0.00')
            self.assertIsNone(first.json()['product']['carbs_per_100g'])
            self.client.get('/api/mobile/barcodes/products/'+CANONICAL, headers=self.headers)
            self.assertEqual(external.await_count, 1)
        result = self.client.put(path, json={**VALUES, 'verified': True}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['product']['macros_source'], 'label')
        self.assertEqual(self.diary()['items'], [])
        body = self.body()
        body['items'] = [{**VALUES, 'barcode': CODE, 'quantity': '250', 'nutrition_source': 'label', 'macros_source': 'label'}]
        entry_id = self.post(body).json()['ids'][0]
        day = self.diary()
        self.assertEqual(day['total_kcal'], '250.0')
        self.assertEqual(day['items'][0]['protein'], '10.0')
        self.assertEqual(day['items'][0]['barcode'], CANONICAL)
        item = {**body['items'][0], 'meal': 'lunch'}
        item.pop('barcode')
        self.client.patch(f'/api/mobile/entries/{entry_id}', json=item, headers=self.headers)
        self.assertEqual(self.diary()['items'][0]['barcode'], CANONICAL)
        item['name'] = 'Different food'
        self.client.patch(f'/api/mobile/entries/{entry_id}', json=item, headers=self.headers)
        self.assertIsNone(self.diary()['items'][0]['barcode'])

    def test_invalid_or_missing_catalog_does_not_guess(self):
        for code in ('3017620422004', '2100000000012', 'not-a-code'):
            self.assertEqual(self.client.get('/api/mobile/barcodes/products/'+code, headers=self.headers).status_code, 422)
        with patch('app.mobile_barcodes.lookup_openfoodfacts', AsyncMock(side_effect=BarcodeLookupUnavailable('offline'))):
            result = self.client.get('/api/mobile/barcodes/products/'+CODE, headers=self.headers).json()
            self.assertFalse(result['found'])
            self.assertTrue(result['unavailable'])
            self.assertEqual(result['barcode'], CANONICAL)
        with self.factory() as db:
            self.assertIsNone(db.get(MobileBarcodeProduct, CANONICAL))
        self.assertEqual(self.client.put('/api/mobile/barcodes/products/'+CODE, json={**VALUES, 'protein_per_100g': 'NaN'}, headers=self.headers).status_code, 422)

    def test_legacy_macro_submission_retry_is_not_duplicated(self):
        body = self.body()
        body['items'][0].update(protein_per_100g='4', macros_source='label')
        result = self.post(body)
        from app.mobile_api import Save
        old = Save(**body).model_dump(mode='json')
        for item in old['items']:
            item.pop('barcode')
            item.pop('library_ref')
        old_hash = hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.factory() as db:
            db.get(MobileSubmission, body['request_id']).payload_hash = old_hash
            db.commit()
        self.assertEqual(self.post(body).json(), result.json())
        self.assertEqual(len(self.diary()['items']), 1)
        body['items'][0]['barcode'] = CODE
        self.assertEqual(self.post(body).status_code, 409)

    def test_label_is_scoped_idempotent_and_never_logs_food(self):
        image_id = str(uuid4())
        with self.factory() as db:
            db.add(MobileImage(id=image_id, data=b'photo', sha256='a'*64))
            db.commit()
        request = {'request_id': str(uuid4()), 'image_id': image_id, 'barcode': CODE}
        with patch('app.mobile_barcodes.check_analysis_config'), patch('app.mobile_barcodes.callback_base', return_value='https://example.test'), patch('app.mobile_barcodes.publish_image', AsyncMock(return_value='https://example.test/photo.jpg')), patch('app.mobile_barcodes.send_analysis', AsyncMock(side_effect=GrokDispatchUnknown('uncertain'))) as send:
            r = self.client.post('/api/mobile/barcodes/labels', json=request, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['status'], 'dispatch_unknown')
            self.client.post('/api/mobile/barcodes/labels', json=request, headers=self.headers)
            self.assertEqual(send.await_count, 1)
            payload = send.call_args.args[0]
        scoped = {'Authorization': payload['access']['authorization']}
        path = '/api/mobile/barcodes/labels/'+request['request_id']
        self.assertEqual(self.client.get(path+'/input', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get('/api/mobile/diary?entry_date=2026-10-03', headers=scoped).status_code, 401)
        self.assertIn('image_url', self.client.get(path+'/input', headers=scoped).json())
        result = {'product': VALUES}
        first = self.client.post(path+'/result', json=result, headers=scoped)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.post(path+'/result', json=result, headers=scoped).status_code, 200)
        result['product'] = {**VALUES, 'kcal_per_100g': '500'}
        self.assertEqual(self.client.post(path+'/result', json=result, headers=scoped).status_code, 409)
        status = self.client.get(path, headers=self.headers).json()
        self.assertEqual(status['status'], 'completed')
        self.assertFalse(status['product']['verified'])
        self.assertEqual(status['product']['fat_per_100g'], '0')
        with self.factory() as db:
            self.assertIsNone(db.get(MobileBarcodeProduct, CANONICAL))
            db.get(MobileBarcodeLabelJob, request['request_id']).expires_at = now_utc()-timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post(path+'/result', json={'product': VALUES}, headers=scoped).status_code, 410)
        self.assertEqual(self.diary()['items'], [])


class BarcodeNumbersTest(unittest.IsolatedAsyncioTestCase):
    def test_factory_gtin_and_upc_equivalence(self):
        self.assertEqual(normalize_barcode(CODE), CANONICAL)
        self.assertEqual(normalize_barcode('012345678905'), normalize_barcode('0012345678905'))
        self.assertEqual(normalize_barcode('96385074'), '00000096385074')
        for value in ('3017620422004', '１２３４５６７８', '3017 620422003', '../../'):
            with self.assertRaises(ValueError): normalize_barcode(value)
        # These have valid check digits: rejection must come from the numbering scope.
        for value in ('2100000000012', '2000000000015', '2900000000018', '0200000000011'):
            with self.assertRaisesRegex(ValueError, 'Код магазина'):
                normalize_barcode(value)

    async def test_off_reads_printed_values_and_retains_unknowns(self):
        OFF_REQUESTS.clear()
        captured = []
        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={'status': 1, 'product': {'code': CODE, 'product_name': 'Food',
                'product_quantity_unit': 'ml', 'nutriments': {'energy-kj_100g': 418.4, 'fat_100g': 0, 'proteins_100g': 'NaN'}}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('app.services.barcodes.httpx.AsyncClient', return_value=client):
            result = await lookup_openfoodfacts(CODE)
        self.assertEqual(result['kcal_per_100g'], '100.00')
        self.assertEqual(result['unit'], 'мл')
        self.assertEqual(result['fat_per_100g'], '0.00')
        self.assertIsNone(result['protein_per_100g'])
        self.assertIsNone(result['carbs_per_100g'])
        self.assertNotIn('authorization', captured[0].headers)
        self.assertIn('FoodChecking', captured[0].headers['user-agent'])
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'status':1, 'product':{'code':'96385074', 'product_name':'Wrong'}})))
        with patch('app.services.barcodes.httpx.AsyncClient', return_value=client):
            with self.assertRaises(BarcodeLookupUnavailable): await lookup_openfoodfacts(CODE)
