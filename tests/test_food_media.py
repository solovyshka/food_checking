"""OVH link lifetime and auth, using local temp files and no production uploads."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class FoodMediaTest(unittest.TestCase):
    def test_upload_expiry_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            spec=importlib.util.spec_from_file_location('food_media_test',Path(__file__).resolve().parents[1]/'deploy/public/food-media-server.py')
            media=importlib.util.module_from_spec(spec);spec.loader.exec_module(media)
            media.ROOT=Path(folder);media.TOKEN='private-test-key-'+'x'*40
            with media.connect(): pass
            server=media.ThreadingHTTPServer(('127.0.0.1',0),media.Handler)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            def call(path,body=None,auth=None):
                req=Request(base+path,data=body,headers={'Authorization':auth or '', 'Content-Type':'image/jpeg'})
                try:
                    with urlopen(req,timeout=5) as response:return response.status,response.read(),response.headers
                except HTTPError as exc:return exc.code,exc.read(),exc.headers
            try:
                self.assertEqual(call('/internal/food-media/upload',b'image')[0],401)
                auth='Bearer '+media.TOKEN
                self.assertEqual(call('/internal/food-media/upload',b'invalid',auth)[0],422)
                jpeg=b'\xff\xd8\xffsample\xff\xd9'
                status,raw,_=call('/internal/food-media/upload',jpeg,auth)
                self.assertEqual(status,200)
                link=json.loads(raw);self.assertAlmostEqual(link['expires_at'],time.time()+7200,delta=2)
                status,raw,headers=call(link['path'])
                self.assertEqual(status,200);self.assertEqual(raw,jpeg)
                self.assertEqual(headers['Content-Type'],'image/jpeg');self.assertIn('no-store',headers['Cache-Control'])
                self.assertEqual(call('/media/food/')[0],404)
                self.assertEqual(call('/media/food/'+'b'*64+'.jpg')[0],404)
                with media.connect() as db:db.execute('UPDATE links SET expires=?',(int(time.time())-1,))
                self.assertEqual(call(link['path'])[0],410)
                media.cleanup()
                self.assertEqual(list(Path(folder).glob('*.jpg')),[])
                self.assertEqual(call(link['path'])[0],404)
            finally:server.shutdown();server.server_close();worker.join()
