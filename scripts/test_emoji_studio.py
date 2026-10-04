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
        self.assertEqual(one['layout'],{'columns':12,'rows':1,'frames':12,'order':'expression-rows'})
        duplicate=studio.create({**payload(),'request_id':'another-request-123456789'},submit=False)
        self.assertEqual(duplicate['id'],one['id'])
        self.assertTrue(duplicate['reused_request'])
        with self.assertRaisesRegex(ValueError,'已有任务'):
            studio.create({**payload(),'request_id':'different-request-123456789','expressions':[{'name':'谢谢','description':'双手合起点头'}]},submit=False)

    def test_invalid_uploads_and_names(self):
        for change in ({'image':'not-data'}, {'expressions':[]}, {'expressions':[{'name':'x','description':''}]}, {'outline':'yes'}, {'mode':'video'}):
            with self.assertRaises(ValueError):studio.create({**payload(),**change},submit=False)
        with self.assertRaises(ValueError):studio.folder('../x')

    def test_request_id_cannot_reuse_job_with_different_content(self):
        one=studio.create(payload(),submit=False)
        changed={**payload(),'expressions':[{'name':'谢谢','description':'合起双手点头'}]}
        for legacy in (False,True):
            if legacy:studio.update(one['id'],request_fingerprint=None)
            with patch.object(studio.POOL,'submit') as submit:
                with self.assertRaisesRegex(ValueError,'请求编号已用于不同内容'):
                    studio.create(changed)
            submit.assert_not_called()
            same=studio.create(payload(),submit=False)
            self.assertEqual(same['id'],one['id'])
        self.assertEqual(len(studio.jobs()),1)

    def test_ten_expressions_accepted_and_eleven_rejected(self):
        data=payload()
        data['expressions']=[{'name':f'表情{i+1}','description':'微笑眨眼再挥手'} for i in range(10)]
        mode,items,*_=studio.validate(data)
        self.assertEqual(len(items),10)
        self.assertEqual(studio.plan(mode,10),{'columns':12,'rows':10,'frames':12,'order':'expression-rows'})
        data['expressions'].append({'name':'第十一项','description':'微笑眨眼再挥手'})
        with self.assertRaisesRegex(ValueError,'1–10'):studio.validate(data)

    def test_key_avoids_magenta_subject(self):
        im=Image.new('RGBA',(20,20),'#ff00ff')
        self.assertNotEqual(studio.choose_key(im),'#FF00FF')


    def test_http_400_keeps_safe_reason_in_job_without_retry(self):
        job=studio.create(payload(),submit=False)
        failure=urllib.error.HTTPError(studio.ARK_URL,400,'Bad Request',{'X-Tt-Logid':'test-request-12345678'},
            BytesIO(json.dumps({'error':{'code':'InvalidParameter','param':'size',
            'message':'The parameter size is invalid; dummy-private-key'}}).encode()))
        with patch.object(studio,'credential',return_value='dummy-private-key'),patch.object(studio.urllib.request,'urlopen',side_effect=failure) as call:
            studio.run(job['id'])
        record=studio.read_job(job['id'])
        self.assertEqual(call.call_count,1)
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['model_calls'],1)
        self.assertEqual(record['provider_error']['http_status'],400)
        self.assertIn('字段：size',record['message'])
        self.assertNotIn('dummy-private-key',json.dumps(record))


    def test_policy_rejected_same_content_never_submits_again(self):
        job=studio.create(payload(),submit=False)
        studio.update(job['id'],status='failed',provider_error={'error_code':'OutputImageSensitiveContentDetected.PolicyViolation'})
        changed={**payload(),'request_id':'different-request-12345678'}
        with patch.object(studio,'credential') as key,patch.object(studio.POOL,'submit') as submit:
            reused=studio.create(changed)
            self.assertEqual(reused['id'],job['id'])
            self.assertTrue(reused['reused_request'])
        key.assert_not_called();submit.assert_not_called()
        self.assertEqual(len(studio.jobs()),1)

    def test_border_key_calibration_preserves_subject_colors_and_source(self):
        job=studio.create(payload('static'),submit=False)
        original=Image.new('RGBA',(400,400),'#E93CD4')
        draw=ImageDraw.Draw(original)
        draw.rectangle((130,60,270,340),fill='#fff2d4')
        draw.rectangle((130,150,270,190),fill='#2b7760')
        draw.rectangle((160,200,240,230),fill='white')
        path=studio.folder(job['id'])/'source.png';original.save(path)
        saved=path.read_bytes()
        self.assertEqual(studio.measured_chroma(original,'#FF00FF'),'#E93CD4')
        result=studio.process(job['id'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(path.read_bytes(),saved)
        with Image.open(studio.folder(job['id'])/'output/01/sticker.png') as image:
            colors=list(studio.pixels(image))
            self.assertTrue(any(p[3]>250 and p[:3]==(255,255,255) for p in colors))
            self.assertTrue(any(p[3]>250 and p[:3]==(255,242,212) for p in colors))
            self.assertTrue(any(p[3]>250 and p[:3]==(43,119,96) for p in colors))
            self.assertEqual(image.getpixel((0,0))[3],0)
        white=Image.new('RGBA',(400,400),'white')
        self.assertEqual(studio.measured_chroma(white,'#FF00FF'),'#FF00FF')

    def test_twelve_frame_rows_export_without_new_provider_call(self):
        job=studio.create(payload(),submit=False)
        im=Image.new('RGB',(1200,100),'#FF00FF');draw=ImageDraw.Draw(im)
        for col in range(12):
            draw.ellipse((col*100+30,15,col*100+70,85),fill='#ffe692')
            draw.ellipse((col*100+40,30,col*100+45,35),fill='#203050')
        im.save(studio.folder(job['id'])/'source.png')
        studio.update(job['id'],status='failed',source_available=True,model_calls=1,processing_error={'type':'previous_failure'})
        with patch.object(studio,'provider_generate') as provider:
            queued=studio.reprocess(job['id'],submit=False)
            self.assertEqual(queued['status'],'processing')
            studio.run_processing(job['id'])
        provider.assert_not_called()
        result=studio.read_job(job['id'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['results'][0]['frames'],12)
        self.assertIsNone(result['processing_error'])
        self.assertEqual(result['model_calls'],1)
        self.assertTrue((studio.folder(job['id'])/'stickers.zip').is_file())

    def test_reprocess_refuses_missing_source_and_preserves_missing_frames(self):
        job=studio.create(payload(),submit=False)
        studio.update(job['id'],status='failed')
        with self.assertRaisesRegex(ValueError,'没有可处理'):studio.reprocess(job['id'],submit=False)
        im=Image.new('RGB',(1200,100),'#FF00FF');draw=ImageDraw.Draw(im)
        for col in range(8):draw.ellipse((col*100+30,15,col*100+70,85),fill='#ffe692')
        im.save(studio.folder(job['id'])/'source.png')
        studio.update(job['id'],source_available=True,model_calls=1)
        with patch.object(studio,'provider_generate') as provider:
            studio.reprocess(job['id'],submit=False);studio.run_processing(job['id'])
        provider.assert_not_called()
        result=studio.read_job(job['id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['model_calls'],1)
        self.assertFalse((studio.folder(job['id'])/'stickers.zip').exists())
        self.assertIn('凑帧',result['message'])


    def rejected_job(self):
        job=studio.create(payload(),submit=False)
        return studio.update(job['id'],status='failed',model_calls=1,
            provider_error={'http_status':400,'error_code':'OutputImageSensitiveContentDetected.PolicyViolation'})

    def test_manual_retry_requires_explicit_cost_confirmation(self):
        job=self.rejected_job()
        for confirmation in (None,False,'true',1):
            with patch.object(studio.POOL,'submit') as submit:
                with self.assertRaisesRegex(ValueError,'明确确认'):
                    studio.retry_generation(job['id'],{'confirm_cost':confirmation,'request_id':'retry-request-123456789'})
            submit.assert_not_called()
        self.assertEqual(len(studio.jobs()),1)

    def test_manual_retry_is_one_call_idempotent_and_preserves_input(self):
        job=self.rejected_job()
        args={'confirm_cost':True,'request_id':'retry-request-123456789'}
        with patch.object(studio,'credential',return_value='dummy-key'),patch.object(studio.POOL,'submit') as submit:
            retried=studio.retry_generation(job['id'],args)
            repeated=studio.retry_generation(job['id'],args)
        self.assertEqual(submit.call_count,1)
        self.assertEqual(retried['id'],repeated['id'])
        self.assertEqual(retried['retry_of'],job['id'])
        for field in ('expressions','identity','outline','model'):
            self.assertEqual(retried[field],job[field])
        self.assertEqual((studio.folder(job['id'])/'reference-original').read_bytes(),
            (studio.folder(retried['id'])/'reference-original').read_bytes())
        self.assertFalse(studio.view_job(job)['manual_retry_available'])
        self.assertFalse(studio.view_job(retried)['manual_retry_available'])
        studio.update(retried['id'],status='failed')
        with self.assertRaisesRegex(ValueError,'最多手动重试一次'):
            studio.retry_generation(job['id'],{**args,'request_id':'second-retry-123456789'})
        with self.assertRaisesRegex(ValueError,'最多手动重试一次'):
            studio.retry_generation(retried['id'],{**args,'request_id':'child-retry-123456789'})

    def test_manual_retry_refuses_source_or_changed_model(self):
        job=self.rejected_job()
        args={'confirm_cost':True,'request_id':'retry-request-123456789'}
        with patch.object(studio.POOL,'submit') as submit:
            studio.update(job['id'],source_available=True)
            with self.assertRaisesRegex(ValueError,'有原图请免费'):studio.retry_generation(job['id'],args)
            studio.update(job['id'],source_available=False,model='different-model')
            with self.assertRaisesRegex(ValueError,'模型配置已改变'):studio.retry_generation(job['id'],args)
        submit.assert_not_called()
        self.assertEqual(len(studio.jobs()),1)

    def test_legacy_rejection_reuses_without_permanent_local_error(self):
        job=self.rejected_job()
        studio.update(job['id'],request_fingerprint=None)
        data={**payload(),'request_id':'new-request-123456789'}
        with patch.object(studio.POOL,'submit') as submit:
            recovered=studio.create(data)
        self.assertEqual(recovered['id'],job['id'])
        self.assertTrue(studio.view_job(recovered)['manual_retry_available'])
        submit.assert_not_called()

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
