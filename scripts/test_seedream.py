"""Provider tests use dummy credentials and local image bytes, never paid calls."""
import base64
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from PIL import Image
import seedream


class SeedreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ref = self.root / 'reference.png'
        Image.new('RGBA', (100,100), (10,20,30,0)).save(self.ref)
        self.png = BytesIO()
        Image.new('RGBA', (1280,720), (240,230,100,255)).save(self.png,format='PNG')

    def tearDown(self):
        self.tmp.cleanup()

    def response(self):
        return BytesIO(json.dumps({'model':seedream.MODEL,'data':[{'b64_json':base64.b64encode(self.png.getvalue()).decode()}],'usage':{'generated_images':1,'private_field':'must-not-log'}}).encode())

    def test_success_one_post_preserves_source_and_safe_receipt(self):
        out=self.root/'source.png'
        with patch.object(seedream.urllib.request,'urlopen',return_value=self.response()) as call:
            receipt=seedream.generate('ONE sheet',[self.ref],out,key='dummy-key-not-real')
        self.assertEqual(call.call_count,1)
        body=json.loads(call.call_args.args[0].data)
        self.assertEqual(body['model'],seedream.MODEL)
        self.assertEqual(body['response_format'],'b64_json')
        self.assertNotIn('sequential_image_generation',body)
        self.assertEqual(out.read_bytes(),self.png.getvalue())
        self.assertEqual(receipt['returned_size'],[1280,720])
        self.assertNotIn('dummy-key-not-real',json.dumps(receipt))
        self.assertNotIn('private_field',receipt['usage'])
        with Image.open(BytesIO(base64.b64decode(body['image'][0].split(',')[1]))) as visible:
            self.assertEqual(visible.getpixel((0,0)),(255,255,255))
        with Image.open(self.ref) as original:self.assertEqual(original.getpixel((0,0)),(10,20,30,0))

    def test_timeout_does_not_retry_or_expose_key(self):
        with patch.object(seedream.urllib.request,'urlopen',side_effect=TimeoutError()) as call:
            with self.assertRaises(seedream.SeedreamError) as error:
                seedream.generate('ONE sheet',[self.ref],self.root/'source.png',key='dummy-private-key')
        self.assertEqual(call.call_count,1)
        self.assertNotIn('dummy-private-key',str(error.exception))
        self.assertFalse((self.root/'source.png').exists())

    def test_invalid_sizes_and_native_alpha_validation_are_offline(self):
        with patch.object(seedream.urllib.request,'urlopen') as call:
            for value in ('4K','512x512','4096x4096'):
                with self.assertRaises(seedream.SeedreamError):
                    seedream.generate('sheet',[self.ref],self.root/'source.png',size=value,key='dummy')
            with self.assertRaises(seedream.SeedreamError):
                seedream.build_payload('sheet',[self.ref,self.ref],background='transparent')
        call.assert_not_called()
        native=seedream.build_payload('sheet',[self.ref],background='transparent')
        self.assertEqual(native['background'],'transparent')

    def test_transport_exception_and_bad_header_cannot_leak_key(self):
        with patch.object(seedream.urllib.request,'urlopen',side_effect=ValueError('header contains dummy-private-key')) as call:
            with self.assertRaises(seedream.SeedreamError) as error:
                seedream.generate('ONE sheet',[self.ref],self.root/'source.png',key='dummy-private-key')
        self.assertEqual(call.call_count,1)
        self.assertNotIn('dummy-private-key',str(error.exception))
        with patch.object(seedream.urllib.request,'urlopen') as call:
            with self.assertRaises(seedream.SeedreamError):
                seedream.generate('ONE sheet',[self.ref],self.root/'source.png',key='dummy-key\nsecond-line')
        call.assert_not_called()


    def test_http_parameter_error_is_actionable_and_does_not_retry(self):
        failure=urllib.error.HTTPError(seedream.API_URL,400,'Bad Request',
            {'X-Tt-Logid':'20261003-test-request-id'}, BytesIO(json.dumps({'error':{
                'code':'InvalidParameter','message':'The parameter size is invalid; dummy-private-key',
                'param':'size'}}).encode()))
        with patch.object(seedream.urllib.request,'urlopen',side_effect=failure) as call:
            with self.assertRaises(seedream.SeedreamAPIError) as caught:
                seedream.generate('sheet',[self.ref],self.root/'source.png',key='dummy-private-key')
        self.assertEqual(call.call_count,1)
        error=caught.exception
        self.assertIn('字段：size',str(error))
        self.assertNotIn('额度',str(error))
        self.assertNotIn('dummy-private-key',str(error))
        self.assertEqual(error.diagnostic['request_id'],'20261003-test-request-id')
        self.assertEqual(error.diagnostic['error_code'],'InvalidParameter')
        self.assertFalse((self.root/'source.png').exists())
        self.assertTrue(failure.fp.closed)

    def test_http_content_rejection_is_not_reported_as_credentials(self):
        failure=urllib.error.HTTPError(seedream.API_URL,400,'Bad Request',{},BytesIO(json.dumps({'error':{
            'code':'OutputImageSensitiveContentDetected','message':'Request ID: request-12345678. private prompt'}}).encode()))
        error=seedream.http_failure(failure,'dummy-private-key')
        self.assertIn('生成图片未通过',str(error))
        self.assertNotIn('权限',str(error))
        self.assertNotIn('private prompt',str(error))
        self.assertEqual(error.diagnostic['request_id'],'request-12345678')

    def test_http_untrusted_error_fields_and_non_json_remain_private(self):
        bodies=[json.dumps({'error':{'code':'InvalidParameter.dummy-private-key','param':{'private':'value'},
                'request_id':'dummy-private-key','message':'Bearer dummy-private-key https://private.example/a?signature=abc'}}).encode(),
                b'<html>dummy-private-key</html>',json.dumps(['dummy-private-key']).encode()]
        for body in bodies:
            error=seedream.http_failure(urllib.error.HTTPError(seedream.API_URL,400,'Bad Request',{},BytesIO(body)),'dummy-private-key')
            public=str(error)+json.dumps(error.diagnostic)
            self.assertNotIn('dummy-private-key',public)
            self.assertNotIn('private.example',public)
            self.assertNotIn('signature',public)
            self.assertIsNone(error.diagnostic['error_code'])
            self.assertIn('未说明',str(error))
        download=seedream.http_failure(urllib.error.HTTPError('https://private.example',403,'Forbidden',{},BytesIO(b'')),'dummy-private-key','download')
        self.assertIn('原图下载',str(download))
        self.assertIn('可能已生成并计费',str(download))

    def test_cli_failure_persists_safe_provider_diagnostic(self):
        prompt=self.root/'prompt.txt';prompt.write_text('ONE sheet')
        out=self.root/'out'
        args=['seedream.py','--prompt',str(prompt),'--reference',str(self.ref),'--out',str(out)]
        failure=urllib.error.HTTPError(seedream.API_URL,400,'Bad Request',{},BytesIO(json.dumps({'error':{
            'code':'InvalidParameter','param':'size','message':'dummy-private-key'}}).encode()))
        with patch('sys.argv',args),patch.object(seedream,'credential',return_value='dummy-private-key'),patch.object(seedream.urllib.request,'urlopen',side_effect=failure) as call,patch('sys.stderr',new_callable=StringIO):
            with self.assertRaises(SystemExit):seedream.main()
        record=json.loads((out/'generation-log.json').read_text())
        self.assertEqual(call.call_count,1)
        self.assertEqual(record['provider_error']['parameter'],'size')
        self.assertEqual(record['model_calls'],1)
        self.assertEqual(record['status'],'failed')
        self.assertNotIn('dummy-private-key',json.dumps(record))

    def test_atlas_size_stays_in_model_limits(self):
        for ratio in (1/16,1/3,8/15,1,12/10,12,16):
            value=seedream.atlas_size(ratio)
            self.assertEqual(seedream.valid_size(value),value)

    def test_credential_file_and_environment_priority(self):
        file=self.root/'private.key';file.write_text('file-dummy-secret')
        with patch.dict(os.environ,{'ARK_API_KEY':'','ARK_API_KEY_FILE':str(file)}):
            self.assertEqual(seedream.credential(),'file-dummy-secret')
            with patch.dict(os.environ,{'ARK_API_KEY':'env-dummy-secret'}):
                self.assertEqual(seedream.credential(),'env-dummy-secret')

    def test_cli_dry_run_does_not_read_credential_or_connect(self):
        prompt=self.root/'prompt.txt';prompt.write_text('ONE sheet')
        args=['seedream.py','--prompt',str(prompt),'--reference',str(self.ref),'--out',str(self.root/'out'),'--dry-run']
        with patch('sys.argv',args),patch.object(seedream,'credential') as key,patch.object(seedream.urllib.request,'urlopen') as call,patch('sys.stdout',new_callable=StringIO) as printed:
            seedream.main()
        self.assertEqual(json.loads(printed.getvalue())['model_calls'],0)
        key.assert_not_called();call.assert_not_called()
        self.assertFalse((self.root/'out').exists())

    def test_cli_success_and_second_run_refused(self):
        prompt=self.root/'prompt.txt';prompt.write_text('ONE sheet')
        out=self.root/'out'
        args=['seedream.py','--prompt',str(prompt),'--reference',str(self.ref),'--out',str(out)]
        with patch('sys.argv',args),patch.object(seedream,'credential',return_value='dummy-key-not-real'),patch.object(seedream.urllib.request,'urlopen',return_value=self.response()) as call,patch('sys.stdout',new_callable=StringIO),patch('sys.stderr',new_callable=StringIO):
            seedream.main()
            original_log=(out/'generation-log.json').read_bytes()
            with self.assertRaises(SystemExit):seedream.main()
        self.assertEqual(call.call_count,1)
        self.assertEqual((out/'generation-log.json').read_bytes(),original_log)
        self.assertNotIn('dummy-key-not-real',original_log.decode())


if __name__=='__main__':unittest.main()
