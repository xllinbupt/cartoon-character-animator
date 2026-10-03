"""No billable provider calls. API/storage/export checks and real sheet import."""
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from PIL import Image, ImageDraw
import emoji_studio as studio


def image_data():
    image = Image.new('RGBA', (100,100))
    ImageDraw.Draw(image).ellipse((20,10,80,90),fill=(240,220,100,255))
    out = BytesIO(); image.save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


def payload(mode='animated'):
    return {'request_id':'test-request-123456789','image':image_data(),'mode':mode,
            'expressions':[{'name':'开心','description':'微笑眨眼再挥手'}], 'outline':False}


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original=studio.DATA
        studio.DATA=Path(self.tmp.name)

    def tearDown(self):
        studio.DATA=self.original
        self.tmp.cleanup()

    def test_upload_and_duplicate_creation(self):
        one=studio.create(payload(),submit=False)
        two=studio.create(payload(),submit=False)
        self.assertEqual(one['id'],two['id'])
        self.assertEqual(one['model_calls'],0)
        self.assertTrue((studio.folder(one['id'])/'reference-original').exists())
        self.assertTrue((studio.folder(one['id'])/'layout-guide.png').exists())
        self.assertEqual(one['layout'],{'columns':4,'rows':3,'frames':12})
        with self.assertRaises(ValueError):
            studio.create({**payload(),'request_id':'another-request-123456789'},submit=False)

    def test_invalid_uploads_and_names(self):
        for change in ({'image':'not-data'}, {'expressions':[]}, {'expressions':[{'name':'x','description':''}]}, {'outline':'yes'}, {'mode':'video'}):
            with self.assertRaises(ValueError):studio.create({**payload(),**change},submit=False)
        with self.assertRaises(ValueError):studio.folder('../x')

    def test_ten_expressions_accepted_and_eleven_rejected(self):
        data=payload()
        data['expressions']=[{'name':f'表情{i+1}','description':'微笑眨眼再挥手'} for i in range(10)]
        mode,items,*_=studio.validate(data)
        self.assertEqual(len(items),10)
        self.assertEqual(studio.plan(mode,10),{'columns':8,'rows':15,'frames':12})
        data['expressions'].append({'name':'第十一项','description':'微笑眨眼再挥手'})
        with self.assertRaisesRegex(ValueError,'1–10'):studio.validate(data)

    def test_key_avoids_magenta_subject(self):
        im=Image.new('RGBA',(20,20),'#ff00ff')
        self.assertNotEqual(studio.choose_key(im),'#FF00FF')

    def test_provider_timeout_is_one_post(self):
        job=studio.create(payload(),submit=False)
        with patch.object(studio,'credential',return_value='unit-test-key'),patch.object(studio.urllib.request,'urlopen',side_effect=TimeoutError()) as call:
            studio.run(job['id'])
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[0].method,'POST')
        body=json.loads(call.call_args.args[0].data)
        self.assertEqual(body['model'],studio.MODEL)
        self.assertEqual(len(body['image']),2)
        result=studio.read_job(job['id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['model_calls'],1)
        self.assertNotIn('unit-test-key',json.dumps(result))

    def test_static_multiple_exports_and_zip(self):
        p=payload('static')
        p['expressions'].append({'name':'谢谢','description':'双手合起点头致意'})
        job=studio.create(p,submit=False)
        im=Image.new('RGB',(800,400),'#FF00FF');draw=ImageDraw.Draw(im)
        for x in (0,400):
            draw.ellipse((x+100,60,x+300,340),fill='#eddc64')
            draw.ellipse((x+130,120,x+155,145),fill='#223344')
        im.save(studio.folder(job['id'])/'source.png')
        result=studio.process(job['id'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(result['results']),2)
        for output in result['results']:
            path=studio.folder(job['id'])/f"output/{output['index']:02}/wechat.gif"
            with Image.open(path) as gif:
                self.assertEqual(gif.size,(240,240))
                self.assertEqual(gif.n_frames,1)
            self.assertTrue(output['wechat_target_met'])
        import zipfile
        with zipfile.ZipFile(studio.folder(job['id'])/'stickers.zip') as archive:
            self.assertEqual(len(archive.namelist()),9)
            self.assertFalse(any('reference' in f for f in archive.namelist()))

    def test_empty_source_fails_without_fabricated_frames(self):
        job=studio.create(payload(),submit=False)
        Image.new('RGB',(800,600),'#FF00FF').save(studio.folder(job['id'])/'source.png')
        with self.assertRaises(ValueError):studio.process(job['id'])
        self.assertFalse((studio.folder(job['id'])/'stickers.zip').exists())

    def test_http_origin_host_and_traversal(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),studio.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base+'/api/config') as response:
                data=json.load(response)
            self.assertNotIn('key',data)
            for path,headers,body in [('/api/config',{'Host':'evil.example'},None),('/api/jobs',{'X-Studio-Request':'1','Origin':'https://evil.example'},json.dumps(payload()).encode()),('/files/../reference-original',{},None)]:
                request=urllib.request.Request(base+path,data=body,headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                self.assertIn(error.exception.code,(403,404))
                error.exception.close()
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
