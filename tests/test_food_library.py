"""Recipes, retries and immutable consumption snapshots in an isolated database."""
import hashlib
import json
import unittest
from uuid import uuid4

from app.db.mobile_models import MobileSubmission
from tests import test_mobile_api as api_tests
from tests import test_mobile_accounts as account_tests


def recipe(name='Каша', revision=0):
    return {'request_id': str(uuid4()), 'revision': revision, 'kind': 'recipe', 'name': name,
            'yield_g': '500', 'ingredients': [
                {'name': 'Крупа сухая', 'quantity': '100', 'kcal_per_100g': '340', 'protein_per_100g': '12', 'fat_per_100g': '3', 'carbs_per_100g': '65'},
                {'name': 'Масло', 'quantity': '10', 'kcal_per_100g': '900', 'protein_per_100g': '0', 'fat_per_100g': '100', 'carbs_per_100g': '0'}]}


class FoodLibraryTest(unittest.TestCase):
    setUp = api_tests.MobileApiTest.setUp
    tearDown = api_tests.MobileApiTest.tearDown
    diary = api_tests.MobileApiTest.diary

    def put(self, id, body):
        return self.client.put('/api/mobile/library/'+id, json=body, headers=self.headers)

    def test_seed_short_names_cooked_grains_and_complete_values(self):
        response = self.client.get('/api/mobile/catalog', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        products = response.json()['products']
        self.assertGreaterEqual(len(products), 150)
        self.assertEqual(len({p['id'] for p in products}), len(products))
        self.assertTrue(all(p['nutrition_source'] == 'estimate' and p['unit'] == 'г' for p in products))
        for p in products:
            for field in ('kcal_per_100g','protein_per_100g','fat_per_100g','carbs_per_100g'):
                self.assertIsNotNone(p[field])
        grains = [p for p in products if p['category'] == 'Крупы и гарниры']
        self.assertTrue(all('cooked' in p['source']['description'].lower() for p in grains))
        self.assertTrue(any(p['name'] == 'Гречка' for p in grains))
        self.assertTrue(any(p['name'] == 'Молоко 2,5%' for p in products))
        self.assertEqual(self.client.get('/api/mobile/catalog').status_code, 401)

    def test_recipe_yield_retry_conflict_and_past_diary_snapshot(self):
        id = str(uuid4()); body = recipe()
        saved = self.put(id, body)
        self.assertEqual(saved.status_code, 200, saved.text)
        item = saved.json()
        self.assertEqual(item['data']['kcal_per_100g'], '86.00')
        self.assertEqual(item['data']['protein_per_100g'], '2.40')
        self.assertEqual(item['data']['fat_per_100g'], '2.60')
        self.assertEqual(item['data']['carbs_per_100g'], '13.00')
        self.assertEqual(self.put(id, body).json(), item)
        self.assertEqual(self.put(id, {**body, 'name':'Другое'}).status_code, 409)
        meal = {'request_id':str(uuid4()), 'entry_date':'2026-10-03', 'meal':'lunch', 'items':[
            {k:v for k,v in item['data'].items() if k not in ('ingredients','yield_g')} |
            {'quantity':'200', 'nutrition_source':'estimate', 'macros_source':'estimate', 'library_ref':{'kind':'recipe','id':id,'revision':1}}]}
        saved_meal = self.client.post('/api/mobile/entries', json=meal, headers=self.headers)
        self.assertEqual(saved_meal.status_code,200,saved_meal.text)
        before = self.diary()
        self.assertEqual(before['total_kcal'],'172.0')
        self.assertEqual(before['total_fat'],'5.2')
        changed = recipe(revision=1); changed['yield_g']='1000'
        self.assertEqual(self.put(id,changed).json()['data']['kcal_per_100g'],'43.00')
        stale = recipe(revision=1)
        self.assertEqual(self.put(id,stale).status_code,409)
        delete = {'request_id':str(uuid4()),'revision':2}
        path = '/api/mobile/library/'+id
        for _ in range(2):
            self.assertEqual(self.client.request('DELETE',path,json=delete,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/mobile/library',headers=self.headers).json()['items'],[])
        self.assertEqual(self.diary()['items'],before['items'])
        self.assertEqual(self.put(id,recipe()).status_code,409)

    def test_missing_zero_nonfinite_and_wrong_units_never_become_valid_recipes(self):
        for field,value in [('yield_g','0'),('yield_g','NaN'),('yield_g',True),('ingredients',[])]:
            body=recipe();body[field]=value
            self.assertEqual(self.put(str(uuid4()),body).status_code,422)
        for field,value in [('quantity','-1'),('quantity',True),('unit','мл'),('fat_per_100g',None),('kcal_per_100g','Infinity')]:
            body=recipe();body['ingredients'][0][field]=value
            self.assertEqual(self.put(str(uuid4()),body).status_code,422)
        body=recipe();body['yield_g']='0.001'
        self.assertEqual(self.put(str(uuid4()),body).status_code,422)
        body={'request_id':str(uuid4()),'revision':0,'kind':'product','name':'Вода','product':{'name':'Вода','kcal_per_100g':'0','protein_per_100g':'0','fat_per_100g':'0','carbs_per_100g':'0'}}
        response=self.put(str(uuid4()),body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data']['fat_per_100g'],'0')

    def test_last_apk_pending_submission_hash_remains_replayable(self):
        from app.mobile_api import Save
        body=api_tests.MobileApiTest.body(self)
        old=Save.model_validate(body).model_dump(mode='json')
        for item in old['items']:item.pop('library_ref')
        old_hash=hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.factory() as db:
            db.add(MobileSubmission(id=body['request_id'],payload_hash=old_hash,response={'ids':[999]}));db.commit()
        response=self.client.post('/api/mobile/entries',json=body,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'ids':[999]})


class FoodLibraryAccountsTest(unittest.TestCase):
    setUp=account_tests.MobileAccountsTest.setUp
    tearDown=account_tests.MobileAccountsTest.tearDown

    def test_same_recipe_id_separate_users_and_unauthorized_changes(self):
        id=str(uuid4());path='/api/mobile/library/'+id
        first=recipe('Первый'); second=recipe('Второй')
        self.assertEqual(self.client.put(path,json=first,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/mobile/library',headers=self.second).json()['items'],[])
        self.assertEqual(self.client.put(path,json=second,headers=self.second).status_code,200)
        for headers,name in [(self.headers,'Первый'),(self.second,'Второй')]:
            self.assertEqual(self.client.get('/api/mobile/library',headers=headers).json()['items'][0]['data']['name'],name)
        self.assertEqual(self.client.put(path,json=second).status_code,401)
        self.assertEqual(self.client.request('DELETE',path,json={'request_id':str(uuid4()),'revision':1},headers=self.second).status_code,200)
        self.assertEqual(len(self.client.get('/api/mobile/library',headers=self.headers).json()['items']),1)
