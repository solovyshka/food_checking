"""Callback, original units, photo access and once-only dispatch on an isolated DB."""
from test_mobile_api import MobileApiTest
from app.db.mobile_models import MobileParseJob, MobileParsedFood
from app.db.models import ConsumptionTranscript
from app.services.grok_analysis import callback_token, now_utc
from app.services.grok_bot import GrokDispatchUnknown, GrokCallFailed
from datetime import timedelta
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
