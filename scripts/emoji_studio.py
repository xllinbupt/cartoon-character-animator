#!/usr/bin/env python3
"""Loopback sticker studio. One Ark POST per job; no automatic generation retry."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse
import uuid
import zipfile

from PIL import Image, ImageOps, ImageFilter, ImageDraw
from action_sheet import matte, extract, write, pixels
from alpha_qa import audit_sheet, strip
from seedream import MODEL, API_URL as ARK_URL, credential, generate, atlas_size

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'assets/emoji-studio'
DATA = Path(os.environ.get('EMOJI_STUDIO_DATA', Path.home() / 'Library/Application Support/CartoonEmojiStudio')).resolve()
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1)
ACTIVE = {'queued', 'generating', 'processing'}
MAX_BODY = 14 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 24_000_000


def folder(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('任务编号无效')
    return DATA / job_id


def read_job(job_id):
    with LOCK:
        return json.loads((folder(job_id) / 'job.json').read_text())


def update(job_id, **fields):
    with LOCK:
        job = read_job(job_id)
        job.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
        write(folder(job_id) / 'job.json', job)
        return job


def jobs():
    DATA.mkdir(parents=True, exist_ok=True)
    records = []
    for p in DATA.glob('*/job.json'):
        try:
            records.append(json.loads(p.read_text()))
        except (ValueError, OSError):
            continue
    return sorted(records, key=lambda j: j['created_at'], reverse=True)


def validate(payload):
    mode = payload.get('mode', 'animated')
    if mode not in ('animated', 'static'):
        raise ValueError('请选择动图或静态表情')
    items = payload.get('expressions')
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise ValueError('每轮支持 1–10 个表情')
    clean = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('表情配置无效')
        name, description = item.get('name', ''), item.get('description', '')
        if not isinstance(name, str) or not isinstance(description, str):
            raise ValueError('名称和描述必须为文字')
        name, description = name.strip(), description.strip()
        if not 1 <= len(name) <= 24 or not 3 <= len(description) <= 500:
            raise ValueError('每个表情需要名称（1–24 字）和描述（3–500 字）')
        if name in [i['name'] for i in clean]:
            raise ValueError('表情名称不能重复')
        clean.append({'name': name, 'description': description})
    outline = payload.get('outline', False)
    if not isinstance(outline, bool):
        raise ValueError('描边选项无效')
    identity = payload.get('identity', '')
    if not isinstance(identity, str) or len(identity) > 500:
        raise ValueError('角色特征最多 500 字')
    image = payload.get('image', '')
    if not isinstance(image, str) or not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+', image):
        raise ValueError('请上传 PNG、JPEG 或 WebP 原始角色图')
    raw = base64.b64decode(image.split(',', 1)[1], validate=True)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('图片不能超过 8 MB')
    with Image.open(BytesIO(raw)) as im:
        if im.width * im.height > 24_000_000 or min(im.size) < 32:
            raise ValueError('图片尺寸需至少 32 像素，且不超过 2400 万像素')
        im.load()
        normalized = ImageOps.exif_transpose(im).convert('RGBA')
    return mode, clean, identity.strip(), outline, normalized, raw


def plan(mode, count):
    if mode == 'static':
        cols = min(4, count)
        return {'columns': cols, 'rows': math.ceil(count / cols), 'frames': 1}
    return {'columns': 4 if count == 1 else 8, 'rows': 3 * math.ceil(count / 2), 'frames': 12}


def choose_key(image):
    sample = image.copy()
    sample.thumbnail((160, 160))
    opaque = [p[:3] for p in pixels(sample) if p[3] > 200]
    for value in ('#FF00FF', '#00FFFF', '#00FF00', '#0000FF'):
        rgb = tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
        if not any(sum((p[i] - rgb[i])**2 for i in range(3)) < 120**2 for p in opaque):
            return value
    raise ValueError('参考图与常用色键颜色过于接近，请先上传背景更简单的角色图')


def prompt_for(job):
    layout = job['layout']
    text = [f"One cartoon sticker sheet, EXACTLY {layout['columns']} columns by {layout['rows']} rows, equal invisible slots.",
            'Input image is CHARACTER IDENTITY only. Preserve exact species, face, head/body proportions, original colors, eyes, clothing and accessories. Never reinterpret the character.',
            'Flat 2D cartoon matching reference. Complete bodies and limbs within generous cell padding. One consistent global character scale. No visible grid, labels, lettering, watermark, motion lines or floor shadows.',
            f"Background perfectly flat {job['key']}, OPAQUE image, NOT transparent. This key color belongs only to background; keep pale body areas, white eye highlights and all props fully drawn and opaque. Never paint the background key color onto the character.",
            'Second reference is LAYOUT GEOMETRY only: use exactly its columns, rows and invisible slots, but do not copy its grid lines into the output.']
    if job['identity']:
        text.append('Must preserve: ' + job['identity'])
    for i, item in enumerate(job['expressions']):
        if job['mode'] == 'static':
            row, col = divmod(i, layout['columns'])
            text.append(f"Cell row {row+1}, column {col+1}: expression {item['name']!r}: {item['description']}. Draw a single clear expressive pose; name is metadata, not image text.")
        else:
            block_row, block_col = divmod(i, 2)
            text.append(f"Expression {i+1}, {item['name']!r}: {item['description']}. Occupies rows {block_row*3+1} through {block_row*3+3}, columns {block_col*4+1} through {block_col*4+4}: a 4x3 block of exactly TWELVE continuous poses, left-to-right then top-to-bottom. Frames 1-4 anticipation and expression begins, 5-8 clear expression/action peak, 9-12 recovery to frame 1. Show actual changes to eyes, face and limbs; not static copies. This one expression loops independently.")
    occupied = len(job['expressions']) * layout['frames']
    if occupied < layout['columns'] * layout['rows']:
        text.append('Unused final cells/block stay empty flat background key, never add new characters or poses.')
    return '\n\n'.join(text)


def create(payload, submit=True):
    mode, items, identity, outline, image, raw = validate(payload)
    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
        raise ValueError('请求编号无效，请刷新页面重试')
    with LOCK:
        records = jobs()
        for existing in records:
            if existing.get('request_id') == request_id:
                return existing
        for existing in records:
            if existing['status'] in ACTIVE:
                raise ValueError('已有任务正在生成，请等它结束后再提交')
        if submit and not credential():
            raise ValueError('服务端未配置生图接口密钥')
        job_id = uuid.uuid4().hex
        destination = folder(job_id)
        destination.mkdir()
        (destination / 'reference-original').write_bytes(raw)
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        image.save(destination / 'reference.png')
        visible = Image.new('RGBA', image.size, 'white')
        visible.alpha_composite(image)
        visible.convert('RGB').save(destination / 'generation-reference.png')
        job = {'id': job_id, 'request_id': request_id, 'created_at': datetime.now(timezone.utc).isoformat(),
               'mode': mode, 'expressions': items, 'identity': identity, 'outline': outline,
               'layout': plan(mode, len(items)), 'status': 'queued', 'message': '等待生成',
               'model': MODEL, 'model_calls': 0, 'results': [], 'warnings': [], 'key': choose_key(image)}
        guide = Image.new('RGB', (job['layout']['columns']*160, job['layout']['rows']*160), job['key'])
        draw = ImageDraw.Draw(guide)
        for y in range(0, guide.height, 160):
            for x in range(0, guide.width, 160):
                draw.rectangle((x+1,y+1,x+159,y+159), outline='#777777', width=2)
        guide.save(destination / 'layout-guide.png')
        write(destination / 'job.json', job)
        (destination / 'prompt.txt').write_text(prompt_for(job))
        if submit:
            POOL.submit(run, job_id)
        return job


def provider_generate(job):
    destination = folder(job['id'])
    size = atlas_size(job['layout']['columns'] / job['layout']['rows'])
    receipt = generate((destination / 'prompt.txt').read_text(),
        [destination / 'generation-reference.png', destination / 'layout-guide.png'],
        destination / 'source.png', size=size, key=credential(),
        on_request=lambda: update(job['id'], model_calls=1))
    update(job['id'], returned_model=receipt['returned_model'], usage=receipt['usage'],
           source_available=True, generation_receipt=receipt)


def gif_bytes(frames, durations, colors):
    paletted = []
    for image in frames:
        frame = image.convert('RGB').quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        frame.paste(255, mask=image.getchannel('A').point(lambda a: 255 if a < 128 else 0))
        palette = (frame.getpalette() or []) + [0] * 768
        frame.putpalette(palette[:768])
        paletted.append(frame)
    stream = BytesIO()
    paletted[0].save(stream, format='GIF', save_all=True, append_images=paletted[1:], duration=durations, loop=0, disposal=2, transparency=255, optimize=False)
    return stream.getvalue()


def with_outline(image):
    alpha = image.getchannel('A')
    under = Image.new('RGBA', image.size, 'white')
    under.putalpha(alpha.filter(ImageFilter.MaxFilter(5)))
    under.alpha_composite(image)
    return under


def process(job_id, background=None):
    job = read_job(job_id)
    destination = folder(job_id)
    layout = job['layout']
    rows, cols, count = layout['rows'], layout['columns'], len(job['expressions'])
    actions = []
    for row in range(rows):
        if job['mode'] == 'static':
            cells = min(cols, count - row * cols)
        else:
            cells = 4 if count % 2 and row >= rows - 3 else cols
        actions.append({'id': f'row{row}', 'name': f'布局第{row+1}行', 'frames': cells, 'fps': 10, 'loop': True, 'motion': {'type': 'none'}, 'root_motion': 'frames'})
    request = {'schema': 'pet-action-request/v1', 'layout': {'columns': cols, 'frame_size': [240, 240], 'padding': 16, 'placement': 'preserve-cell'}, 'actions': actions}
    request['background'] = background or {'mode': 'chroma', 'key': job.get('key', '#FF00FF'), 'threshold': 45, 'softness': 70}
    with Image.open(destination / 'source.png') as source:
        raw = source.convert('RGBA')
    image = matte(raw, request['background'])
    extracted = extract(request, image)
    audit = audit_sheet(request, raw, image, destination, extracted)
    update(job_id, source_available=True, qa_available=True)
    write(destination / 'processing-request.json', request)
    failed = {key: v['errors'] for key, v in extracted.items() if v['errors']}
    if failed:
        write(destination / 'extraction-errors.json', failed)
        raise ValueError('合图布局或完整性检查失败，未强切或凑帧；请查看原图。可修改描述后创建新任务。')
    results = []
    warnings = ['模型生成的姿势、角色一致性和边缘需要目视检查；自动检测不等于视觉通过。']
    for index, item in enumerate(job['expressions']):
        if job['mode'] == 'static':
            row, col = divmod(index, cols)
            frames = [extracted[f'row{row}']['frames'][col]]
        else:
            block_row, block_col = divmod(index, 2)
            frames = [extracted[f'row{block_row*3+r}']['frames'][block_col*4+c] for r in range(3) for c in range(4)]
        result_dir = destination / f'output/{index+1:02d}'
        result_dir.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            frame.save(result_dir / f'frame-{i+1:02}.png')
        if job['outline']:
            frames = [with_outline(f) for f in frames]
        durations = [400] + [100] * 10 + [400] if job['mode'] == 'animated' else [1000]
        encoded = None
        for colors in (128, 64, 32, 16):
            encoded = gif_bytes(frames, durations, colors)
            if len(encoded) <= 100 * 1024:
                break
        (result_dir / 'wechat.gif').write_bytes(encoded)
        frames[0].resize((120, 120), Image.Resampling.LANCZOS).save(result_dir / 'thumbnail.png')
        frames[0].save(result_dir / 'sticker.png')
        frames[0].save(result_dir / 'sticker.apng.png', save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
        strip(frames, result_dir / 'contact-dark.png', '#293a35')
        strip(frames, result_dir / 'contact-light.png', '#fff8ed')
        # APNG preview preserves alpha, while WeChat downloads use GIF.
        info = {'index': index+1, 'name': item['name'], 'description': item['description'], 'frames': len(frames),
                'gif_bytes': len(encoded), 'wechat_target_met': len(encoded) <= 100*1024,
                'status': 'pending_visual_review', 'palette_colors': colors,
                'url': f'/files/{job_id}/output/{index+1:02d}/', 'size': [240, 240]}
        if not info['wechat_target_met']:
            warnings.append(f"{item['name']}超过100KB制作目标，下载前请检查微信实际导入限制。")
        results.append(info)
    write(destination / 'export-manifest.json', {'mode': job['mode'], 'model': job.get('returned_model', MODEL), 'results': results,
          'wechat_profile': {'gif_size': [240, 240], 'thumbnail_size': [120, 120], 'target_bytes': 100*1024, 'official_acceptance': False}})
    with zipfile.ZipFile(destination / 'stickers.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            n = result['index']
            label = re.sub(r'[^\w\u4e00-\u9fff-]', '_', result['name'])[:24]
            for source, suffix in (('wechat.gif', '.gif'), ('thumbnail.png', '-缩略图.png'), ('sticker.png', '.png'), ('sticker.apng.png', '.apng.png')):
                archive.write(destination / f'output/{n:02d}' / source, f'{n:02d}-{label}{suffix}')
        archive.write(destination / 'export-manifest.json', '导出说明.json')
    return update(job_id, status='completed', message='生成完成，检查表情后即可下载', results=results, warnings=warnings,
                  alpha_warning_rows=sum(bool(v['warnings']) for v in audit['actions'].values()))


def run(job_id):
    try:
        job = update(job_id, status='generating', message='正在生成合图（通常需1–3分钟）', model_calls=0)
        provider_generate(job)
        update(job_id, status='processing', message='正在检查布局并导出微信表情')
        process(job_id)
    except urllib.error.HTTPError as error:
        update(job_id, status='failed', message=f'生图接口返回 HTTP {error.code}，未自动重试；请检查服务端接口权限或额度。')
    except (TimeoutError, urllib.error.URLError):
        update(job_id, status='failed', message='生图或下载超时，可能已计费，未自动重试。')
    except Exception as error:
        # Never expose key, provider request headers or signed URLs.
        message = str(error) if isinstance(error, ValueError) else '处理未完成，已保留任务与原图供检查。'
        update(job_id, status='failed', message=message[:400])


class Handler(BaseHTTPRequestHandler):
    def response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, mutation=False):
        host = self.headers.get('Host', '')
        valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in valid:
            self.response({'error': '仅允许本机访问'}, 403)
            return False
        origin = self.headers.get('Origin')
        if mutation and (self.headers.get('X-Studio-Request') != '1' or origin not in (None, f'http://{host}')):
            self.response({'error': '请求来源无效'}, 403)
            return False
        return True

    def file(self, path, attachment=False):
        if not path.is_file():
            self.response({'error': '文件不存在'}, 404)
            return
        size = path.stat().st_size
        self.send_response(200)
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="stickers.zip"')
        self.end_headers()
        with path.open('rb') as stream:
            while chunk := stream.read(64 * 1024):
                self.wfile.write(chunk)

    def do_GET(self):
        if not self.allowed():
            return
        path = urlparse(self.path).path
        try:
            if path == '/api/config':
                return self.response({'model': MODEL, 'credential_ready': bool(credential()), 'max_expressions': 10, 'frames': 12, 'scope': 'local', 'sample_available': (STATIC/'sample.png').exists()})
            if path == '/api/jobs':
                return self.response({'jobs': jobs()[:20]})
            if re.fullmatch(r'/api/jobs/[a-f0-9]{32}', path):
                return self.response(read_job(path.split('/')[-1]))
            if path.startswith('/files/'):
                parts = path.split('/')
                directory = folder(parts[2])
                relative = '/'.join(parts[3:])
                allowed = relative in ('source.png', 'reference.png', 'prompt.txt', 'alpha-qa.json', 'stickers.zip', 'extraction-errors.json', 'export-manifest.json') or re.fullmatch(r'output/[0-9]{2}/(wechat.gif|thumbnail.png|sticker.png|sticker.apng.png|contact-dark.png|contact-light.png|frame-[0-9]{2}.png)', relative)
                if not allowed:
                    raise ValueError('文件路径无效')
                return self.file(directory / relative, relative == 'stickers.zip')
            relative = 'index.html' if path == '/' else path.lstrip('/')
            if relative not in ('index.html', 'app.js', 'style.css', 'sample.png'):
                return self.response({'error': '页面不存在'}, 404)
            return self.file(STATIC / relative)
        except (ValueError, OSError, IndexError):
            self.response({'error': '任务或文件不存在'}, 404)

    def do_POST(self):
        if not self.allowed(True):
            return
        if urlparse(self.path).path != '/api/jobs':
            return self.response({'error': '接口不存在'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('上传数据过大或为空')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('请求格式无效')
            return self.response(create(payload), 202)
        except (ValueError, OSError, Image.DecompressionBombError) as error:
            self.response({'error': str(error)[:200]}, 400)
        except Exception:
            self.response({'error': '请求无效，请检查上传图片和表情描述'}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=4195)
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    for job in jobs():
        if job['status'] in ACTIVE:
            update(job['id'], status='interrupted', message='服务曾中断，任务未自动重试；可能已计费。')
    print(f'表情工坊 http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
