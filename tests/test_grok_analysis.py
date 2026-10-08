"""Callback, original units, photo access and once-only dispatch on an isolated DB."""
from test_mobile_api import MobileApiTest
from app.db.mobile_models import MobileParseJob, MobileParsedFood, MobileParsedNutrition
from app.db.models import ConsumptionTranscript
from app.services.grok_analysis import callback_token, now_utc
from app.services.grok_bot import GrokDispatchUnknown, GrokCallFailed
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, AsyncMock
from uuid import uuid4
from PIL import Image
import io
import os


class GrokAnalysisTest(MobileApiTest):
    def queue(self, text='Молоко 2.5% 200 г', day='2026-10-03', image=None):
        body = dict(request_id=str(uuid4()), entry_date=day, meal='breakfast', text=text)
        if image: body['image_id'] = image
        r = self.client.post('/api/mobile/queue', json=body, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def start(self, ids, request_id=None, failure=None):
        body = dict(request_id=request_id or str(uuid4()), entry_ids=ids)
        with patch('app.mobile_api.check_analysis_config'), patch('app.mobile_api.send_analysis', AsyncMock(side_effect=failure)) as send, patch('app.mobile_api.publish_image', AsyncMock(return_value='https://food-consumption.solovyshka.com/media/food/'+'a'*64+'.jpg')):
            r = self.client.post('/api/mobile/queue/parse', json=body, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), send

    def headers_for(self, job):
        with self.factory() as db:
            return {'Authorization': 'Bearer ' + callback_token(db.get(MobileParseJob, job['id']))}

    def result(self, raw, unit='г', amount='200', qty='200', kcal='999'):
        return {'transcript_id':raw, 'foods':[{'id':'milk', 'name':'Молоко 2.5%', 'amount':amount, 'unit':unit}],
                'nutrition':[{'food_id':'milk', 'quantity':qty, 'unit':'г', 'kcal_per_100g':kcal, 'nutrition_source':'label'}]}

    def callback(self, job, body, headers=None):
        return self.client.post(f"/api/mobile/grok/jobs/{job['id']}/result", json=body, headers=headers or self.headers_for(job))

    def test_batch_once_callback_once_and_exact_label(self):
        self.post(self.body())
        a, b = self.queue(), self.queue(day='2026-10-02')
        job, send = self.start([a,b]); send.assert_awaited_once()
        import json
        payload=send.call_args.args[0]
        self.assertLess(len(json.dumps(payload).encode()),1000)
        self.assertIn('result_url',payload)
        self.assertNotIn(os.environ['FOOD_MOBILE_TOKEN'],json.dumps(payload))
        retry, send = self.start([b,a], request_id=job['id']); send.assert_not_called()
        self.assertEqual(retry['status'], 'running')
        body = {'entries':[self.result(a), self.result(b, 'шт','2','120','280')]}
        # A mobile token cannot substitute for the per-job Grok capability.
        self.assertEqual(self.callback(job, body, self.headers).status_code,401)
        self.assertEqual(self.callback(job, body).status_code,200)
        self.assertEqual(self.callback(job, body).status_code,200)
        current = self.diary()
        self.assertEqual(len(current['foods']),1)
        self.assertEqual(current['total_kcal'],'208.0')
        self.assertEqual(current['nutrition'][0]['nutrition_source'],'label')
        previous = self.diary('2026-10-02')
        self.assertEqual(previous['foods'][0]['unit'],'шт')
        self.assertEqual(previous['foods'][0]['amount'],'2.000')
        self.assertTrue(previous['nutrition'][0]['portion_is_estimate'])
        body['entries'][0]['nutrition'][0]['kcal_per_100g']='55'
        self.assertEqual(self.callback(job,body).status_code,409)
        # Edits and deletion survive a duplicate callback.
        food_id=current['foods'][0]['id']
        self.assertEqual(self.client.patch(f'/api/mobile/grok/foods/{food_id}',headers=self.headers,
            json={'name':'Вода','quantity':'200','unit':'мл','kcal_per_100g':'0','nutrition_source':'manual'}).status_code,200)
        self.assertEqual(self.diary()['total_kcal'],'104.0')
        self.client.delete(f'/api/mobile/grok/foods/{food_id}',headers=self.headers)
        self.assertEqual(self.diary()['foods'],[])

    def test_image_only_queue_scoped_access_and_forced_estimate(self):
        buf=io.BytesIO(); Image.new('RGB',(20,20),'green').save(buf,'PNG')
        def upload(): return self.client.post('/api/mobile/images',headers=self.headers,files={'file':('food.png',buf.getvalue(),'image/png')})
        image=upload().json()['id']; self.assertEqual(upload().json()['id'],image)
        a=self.queue('',image=image); job,_=self.start([a])
        prefix=f"/api/mobile/grok/jobs/{job['id']}"
        self.assertEqual(self.client.get(prefix+'/input').status_code,401)
        headers=self.headers_for(job)
        inp=self.client.get(prefix+'/input',headers=headers).json()
        self.assertTrue(inp['entries'][0]['image_url'].endswith('a'*64+'.jpg'))
        self.assertNotIn('/grok/jobs/',inp['entries'][0]['image_url'])
        self.assertIn('HTTP POST',inp['instruction'])
        self.assertEqual(self.client.get(prefix+'/images/'+image).status_code,401)
        self.assertEqual(self.client.get(prefix+'/images/'+image,headers=headers).headers['content-type'],'image/jpeg')
        self.assertEqual(self.client.get(prefix+'/images/'+str(uuid4()),headers=headers).status_code,404)
        self.assertEqual(self.callback(job,{'entries':[self.result(a,kcal='52')]}).status_code,200)
        row=self.diary()['nutrition'][0]
        self.assertEqual(row['nutrition_source'],'estimate'); self.assertTrue(row['portion_is_estimate'])
        self.assertIn('фотографии',row['note'])
        bad=self.client.post('/api/mobile/images',headers=self.headers,files={'file':('bad.jpg',b'not image','image/jpeg')})
        self.assertEqual(bad.status_code,422)

    def test_invalid_result_does_not_consume_queue(self):
        a,b=self.queue(),self.queue();job,_=self.start([a,b])
        self.assertEqual(self.callback(job,{'entries':[self.result(a)]}).status_code,422)
        wrong=self.result(a);wrong['nutrition'][0]['quantity']='190'
        self.assertEqual(self.callback(job,{'entries':[wrong,self.result(b)]}).status_code,422)
        self.assertEqual(self.diary()['foods'],[])
        with self.factory() as db:
            self.assertEqual(db.get(ConsumptionTranscript,a).status,'queued')
        self.assertEqual(self.callback(job,{'entries':[self.result(a),self.result(b)]}).status_code,200)

    def test_macros_calculated_per_portion_and_preserved_on_quantity_edit(self):
        a = self.queue('Молоко 2.5% 125 г')
        job, _ = self.start([a])
        entry = self.result(a, amount='125', qty='125', kcal='52')
        entry['nutrition'][0].update(protein_per_100g='3.1', fat_per_100g='2.5', carbs_per_100g='4.7')
        body = {'entries': [entry]}
        self.assertEqual(self.callback(job, body).status_code, 200)
        row = self.diary()['nutrition'][0]
        self.assertEqual((row['protein'], row['fat'], row['carbs']), ('3.9', '3.1', '5.9'))
        self.assertEqual(row['macros_source'], 'estimate')
        food_id = row['food_id']
        edit = dict(name='Молоко 2.5%', quantity='200', unit='г', kcal_per_100g='52', nutrition_source='manual')
        self.assertEqual(self.client.patch(f'/api/mobile/grok/foods/{food_id}', headers=self.headers, json=edit).status_code, 200)
        self.assertEqual(self.callback(job, body).status_code, 200)
        row = self.diary()['nutrition'][0]
        self.assertEqual((row['protein'], row['fat'], row['carbs']), ('6.2', '5.0', '9.4'))
        edit['name'] = 'Вода'
        self.client.patch(f'/api/mobile/grok/foods/{food_id}', headers=self.headers, json=edit)
        self.assertIsNone(self.diary()['nutrition'][0]['protein'])

    def test_unknown_macros_are_not_zero_and_bad_values_leave_queue_intact(self):
        a = self.queue(); job, _ = self.start([a])
        entry = self.result(a)
        for value in ('-1', 'NaN', 'Infinity', '201', '1.234'):
            entry['nutrition'][0]['protein_per_100g'] = value
            self.assertEqual(self.callback(job, {'entries': [entry]}).status_code, 422)
            self.assertEqual(self.diary()['foods'], [])
        entry['nutrition'][0].update(protein_per_100g=None, fat_per_100g='0', carbs_per_100g=None)
        self.assertEqual(self.callback(job, {'entries': [entry]}).status_code, 200)
        row = self.diary()['nutrition'][0]
        self.assertIsNone(row['protein']); self.assertIsNone(row['carbs'])
        self.assertEqual(row['fat'], '0.0')
        a = self.queue('Молоко, количество неизвестно'); job, _ = self.start([a])
        entry = self.result(a, unit='не указано', amount=None, qty=None)
        entry['nutrition'][0]['protein_per_100g'] = '3'
        self.assertEqual(self.callback(job, {'entries': [entry]}).status_code, 200)
        row = self.diary()['nutrition'][-1]
        self.assertEqual(row['protein_per_100g'], '3.00'); self.assertIsNone(row['protein'])

    def test_manual_macros_survive_portion_edits_and_clear_when_unit_changes(self):
        body = self.body()
        body['items'][0].update(protein_per_100g='3', fat_per_100g='2.5', carbs_per_100g='4.7', macros_source='label')
        saved = self.post(body)
        self.assertEqual(saved.status_code, 200)
        entry_id = saved.json()['ids'][0]
        self.assertEqual(self.diary()['items'][0]['protein'], '6.0')
        edit = dict(name='Молоко 2.5%', quantity='100', unit='г', kcal_per_100g='52', nutrition_source='label', meal='lunch')
        self.assertEqual(self.client.patch(f'/api/mobile/entries/{entry_id}', headers=self.headers, json=edit).status_code, 200)
        self.assertEqual(self.diary()['items'][0]['protein'], '3.0')
        edit['unit'] = 'мл'
        self.client.patch(f'/api/mobile/entries/{entry_id}', headers=self.headers, json=edit)
        self.assertIsNone(self.diary()['items'][0]['protein'])

    def test_pre_macros_manual_save_retry_does_not_duplicate(self):
        import hashlib, json
        from app.db.mobile_models import MobileSubmission
        from app.mobile_api import Save
        body = self.body(); saved = self.post(body).json()
        payload = Save.model_validate(body).model_dump(mode='json')
        for item in payload['items']:
            for field in ('protein_per_100g', 'fat_per_100g', 'carbs_per_100g', 'macros_source', 'barcode', 'library_ref'):
                item.pop(field)
        with self.factory() as db:
            prior = db.get(MobileSubmission, body['request_id'])
            prior.payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            db.commit()
        self.assertEqual(self.post(body).json(), saved)
        self.assertEqual(len(self.diary()['items']), 1)
        body['items'][0]['protein_per_100g'] = '3'
        self.assertEqual(self.post(body).status_code, 409)

    def test_daily_macros_combine_manual_and_grok_and_follow_date_and_deletion(self):
        body = self.body()
        body['items'][0].update(quantity='125', protein_per_100g='3.1', fat_per_100g='2.5', carbs_per_100g='4.7', macros_source='label')
        self.assertEqual(self.post(body).status_code, 200)
        a, b = self.queue('Йогурт 200 г'), self.queue('Хлеб 200 г', day='2026-10-02')
        job, _ = self.start([a, b])
        entry = self.result(a)
        entry['foods'][0]['name'] = 'Йогурт'
        entry['nutrition'][0].update(protein_per_100g='3', fat_per_100g='2', carbs_per_100g=None)
        other = self.result(b)
        other['foods'][0]['name'] = 'Хлеб'
        other['nutrition'][0].update(protein_per_100g='10', fat_per_100g='10', carbs_per_100g='60')
        self.assertEqual(self.callback(job, {'entries': [entry, other]}).status_code, 200)
        day = self.diary()
        self.assertEqual((day['total_protein'], day['total_fat'], day['total_carbs']), ('9.9', '7.1', '5.9'))
        self.assertEqual(day['missing_macros'], dict(protein=0, fat=0, carbs=1))
        self.assertEqual(day['estimated_macros'], dict(protein=True, fat=True, carbs=False))
        food_id = day['nutrition'][0]['food_id']
        self.client.delete(f'/api/mobile/grok/foods/{food_id}', headers=self.headers)
        day = self.diary()
        self.assertEqual((day['total_protein'], day['total_fat'], day['total_carbs']), ('3.9', '3.1', '5.9'))
        self.assertEqual(day['missing_macros'], dict(protein=0, fat=0, carbs=0))
        self.assertFalse(any(day['estimated_macros'].values()))

    def test_daily_macros_empty_unknown_zero_and_round_once(self):
        from app.services.nutrients import daily_macros
        self.assertEqual(self.diary()['total_protein'], '0.0')
        body = self.body(); self.post(body)
        day = self.diary()
        self.assertIsNone(day['total_protein']); self.assertIsNone(day['total_fat']); self.assertIsNone(day['total_carbs'])
        self.assertEqual(day['missing_macros'], dict(protein=1, fat=1, carbs=1))
        rows = [dict(quantity='25', unit='г', protein_per_100g='1', fat_per_100g='0', carbs_per_100g=None)] * 3
        totals = daily_macros(rows)
        self.assertEqual(totals['total_protein'], '0.8')
        self.assertEqual(totals['total_fat'], '0.0')
        self.assertIsNone(totals['total_carbs'])
        totals = daily_macros([dict(quantity=None, unit='г', protein_per_100g='3')])
        self.assertIsNone(totals['total_protein'])

    def test_energy_delta_includes_grok_and_tracks_edits(self):
        self.post(self.body())
        self.client.put('/api/mobile/days/2026-10-03/energy', json={'spent_kcal':'500.25'}, headers=self.headers)
        transcript_id = self.queue()
        job, _ = self.start([transcript_id])
        self.assertTrue(self.diary()['energy_delta_incomplete'])
        result = self.result(transcript_id)
        self.assertEqual(self.callback(job, {'entries':[result]}).status_code, 200)
        day = self.diary()
        self.assertEqual(Decimal(day['energy_delta']), (Decimal(day['total_kcal'])-Decimal('500.25')).quantize(Decimal('0.1'), rounding='ROUND_HALF_UP'))
        self.assertFalse(day['energy_delta_incomplete'])
        row = day['nutrition'][0]
        self.client.patch(f"/api/mobile/grok/foods/{row['food_id']}", json={'name':'Milk', 'quantity':'300', 'unit':'г', 'kcal_per_100g':'60', 'nutrition_source':'label'}, headers=self.headers)
        self.assertEqual(self.diary()['energy_delta'], '-216.3')
        self.client.delete(f"/api/mobile/grok/foods/{row['food_id']}", headers=self.headers)
        self.assertEqual(self.diary()['energy_delta'], '-396.3')

    def test_verified_macros_override_model_guesses_and_photo_remains_estimate(self):
        body = self.body()
        body['items'][0].update(protein_per_100g='3', fat_per_100g='2.5', carbs_per_100g='4.7', macros_source='label')
        self.assertEqual(self.post(body).status_code, 200)
        a = self.queue(); job, _ = self.start([a])
        inp = self.client.get(f"/api/mobile/grok/jobs/{job['id']}/input", headers=self.headers_for(job)).json()
        self.assertEqual(inp['known_nutrition'][0]['protein_per_100g'], '3.00')
        self.assertIn('protein_per_100g', inp['result_schema']['$defs']['NutritionResult']['properties'])
        entry = self.result(a)
        entry['nutrition'][0].update(protein_per_100g='50', fat_per_100g='50', carbs_per_100g='50')
        self.assertEqual(self.callback(job, {'entries': [entry]}).status_code, 200)
        row = self.diary()['nutrition'][0]
        self.assertEqual(row['protein'], '6.0'); self.assertEqual(row['macros_source'], 'label')
        buf = io.BytesIO(); Image.new('RGB', (10,10), 'red').save(buf, 'PNG')
        image = self.client.post('/api/mobile/images', headers=self.headers, files={'file': ('food.png', buf.getvalue(), 'image/png')}).json()['id']
        a = self.queue('', image=image); job, _ = self.start([a])
        self.assertEqual(self.callback(job, {'entries': [self.result(a)]}).status_code, 200)
        row = self.diary()['nutrition'][-1]
        self.assertEqual(row['protein'], '6.0'); self.assertEqual(row['macros_source'], 'estimate')

    def test_completed_legacy_callback_still_idempotent(self):
        from copy import deepcopy
        from app.services.grok_analysis import digest_json
        a = self.queue(); job, _ = self.start([a]); body = {'entries': [self.result(a)]}
        self.assertEqual(self.callback(job, body).status_code, 200)
        with self.factory() as db:
            row = db.get(MobileParseJob, job['id'])
            old_result = deepcopy(row.result)
            for entry in old_result['entries']:
                for nutrition in entry['nutrition']:
                    for field in ('protein_per_100g', 'fat_per_100g', 'carbs_per_100g', 'macros_source'):
                        nutrition.pop(field, None)
            row.result = dict(old_result); row.result_hash = digest_json(old_result); db.commit()
        self.assertEqual(self.callback(job, body).status_code, 200)

    def test_dispatch_timeout_does_not_launch_a_second_run(self):
        a=self.queue();job,send=self.start([a],failure=GrokDispatchUnknown('timeout'))
        self.assertEqual(job['status'],'dispatch_unknown')
        _,send=self.start([a],request_id=job['id']);send.assert_not_called()
        with self.factory() as db:
            row=db.get(MobileParseJob,job['id']);row.expires_at=now_utc()-timedelta(seconds=1);db.commit()
        r=self.client.get(f"/api/mobile/queue/jobs/{job['id']}",headers=self.headers)
        self.assertEqual(r.json()['status'],'failed')
        self.assertEqual(self.callback(job,{'entries':[self.result(a)]}).status_code,410)
        with self.factory() as db: self.assertIsNone(db.get(ConsumptionTranscript,a).parse_batch_id)
        job,_=self.start([a],failure=GrokCallFailed('rejected'))
        self.assertEqual(job['status'],'failed')
        job,_=self.start([a]);self.assertEqual(job['status'],'running')
        self.assertEqual(self.callback(job,{'error':'Не удалось увидеть картинку'}).status_code,200)
        with self.factory() as db: self.assertIsNone(db.get(ConsumptionTranscript,a).parse_batch_id)
