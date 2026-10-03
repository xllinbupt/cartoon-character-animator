"""Provider tests use dummy credentials and local image bytes, never paid calls."""
import base64
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
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
