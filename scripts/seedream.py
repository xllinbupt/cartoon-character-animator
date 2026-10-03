#!/usr/bin/env python3
"""Seedream 5.0 Pro adapter and CLI. One generation POST; never retries."""
import argparse
import base64
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from PIL import Image, ImageOps

MODEL = 'doubao-seedream-5-0-pro-260628'
API_URL = 'https://ark.cn-beijing.volces.com/api/v3/images/generations'
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MIN_PIXELS, MAX_PIXELS = 921600, 4624220


class SeedreamError(ValueError):
    pass



class SeedreamAPIError(SeedreamError):
    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def http_failure(error, key, stage='generation'):
    """Keep technical identifiers only; never relay arbitrary provider messages."""
    def identifier(value, pattern):
        return value if isinstance(value, str) and (not key or key not in value) and re.fullmatch(pattern, value) else None
    try:
        raw = error.read(65537)
        data = json.loads(raw) if len(raw) <= 65536 else {}
        detail = data.get('error', {}) if isinstance(data, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
    except Exception:
        data, detail = {}, {}
    finally:
        error.close()
    code = identifier(detail.get('code'), r'[A-Za-z][A-Za-z0-9_.-]{0,95}')
    parameters = {'model', 'prompt', 'image', 'size', 'output_format', 'background', 'response_format', 'watermark'}
    candidate_param = detail.get('param')
    param = candidate_param if isinstance(candidate_param, str) and candidate_param in parameters else None
    message = detail.get('message')
    if isinstance(message, str) and not param:
        match = re.search(r"\bparameter(?:s)?\s+['\"`]?([a-z_]+)\b", message, re.I)
        if match and match.group(1) in parameters:
            param = match.group(1)
    request_id = None
    candidates = [data.get('request_id') if isinstance(data, dict) else None, detail.get('request_id')]
    if error.headers:
        candidates.extend(error.headers.get(h) for h in ('X-Tt-Logid', 'X-Request-Id'))
    if isinstance(message, str):
        match = re.search(r'\bRequest[ _]id[:：]\s*([A-Za-z0-9_-]+)', message, re.I)
        if match:
            candidates.append(match.group(1))
    for value in candidates:
        request_id = identifier(value, r'[A-Za-z0-9_-]{8,128}')
        if request_id:
            break
    diagnosis = {'http_status': error.code, 'error_code': code, 'parameter': param,
                 'request_id': request_id, 'stage': stage}
    category = '接口未说明可安全展示的具体原因'
    if stage == 'download':
        category = '原图下载被拒绝，可能已生成并计费，请先检查任务'
    elif code and ('SensitiveContentDetected' in code or 'RiskDetection' in code):
        category = '生成图片未通过服务端内容检查' if code.startswith('Output') else '输入内容未通过服务端内容检查'
    elif code == 'ContentSecurityDetectionError':
        category = '服务端内容检查服务异常'
    elif code and code.startswith(('InvalidParameter', 'MissingParameter', 'InvalidImage', 'InvalidArgument', 'OutofContext')):
        category = '请求参数或参考图不符合接口要求' + (f'（字段：{param}）' if param else '')
    elif code and code.startswith(('ModelNotOpen', 'InvalidEndpoint', 'NotFound')):
        category = '模型未开通或接入点不可用'
    elif error.code == 401:
        category = '本机凭证未通过鉴权'
    elif code and 'ServiceOverdue' in code:
        category = '账户欠费，需检查余额'
    elif error.code == 403:
        category = '请求被拒绝，需检查模型权限'
    elif error.code == 429:
        category = '请求达到速率或额度限制'
    elif error.code >= 500:
        category = '生图服务暂时异常'
    label = '原图下载' if stage == 'download' else '生图接口'
    text = f'{label} HTTP {error.code}：{category}' + (f'；错误码 {code}' if code else '')
    if request_id:
        text += f'；请求编号 {request_id}'
    return SeedreamAPIError(text + '；未自动重试', diagnosis)


def credential():
    value = os.environ.get('ARK_API_KEY', '').strip()
    if value:
        return value
    key_file = Path(os.environ.get('ARK_API_KEY_FILE', Path.home() / '.config/cartoon-character-animator/ark.key')).expanduser()
    return key_file.read_text().strip() if key_file.is_file() else ''


def atlas_size(ratio):
    if not 1/16 <= ratio <= 16:
        raise SeedreamError('合图宽高比应在 1:16 到 16:1 之间')
    width = round(math.sqrt(4_400_000 * ratio) / 16) * 16
    height = round(math.sqrt(4_400_000 / ratio) / 16) * 16
    return f'{width}x{height}'


def valid_size(value):
    if value in ('1K', '1.5K', '2K'):
        return value
    match = re.fullmatch(r'([1-9][0-9]*)x([1-9][0-9]*)', value)
    if not match:
        raise SeedreamError('Seedream 5.0 Pro 尺寸需为 1K、1.5K、2K 或 宽x高')
    width, height = map(int, match.groups())
    if not MIN_PIXELS <= width*height <= MAX_PIXELS or not 1/16 <= width/height <= 16:
        raise SeedreamError('Seedream 5.0 Pro 尺寸超出像素总量或宽高比范围')
    return value


def build_payload(prompt, references, size='2K', model=MODEL, background='opaque'):
    if not isinstance(prompt, str) or not prompt.strip():
        raise SeedreamError('提示词不能为空')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', model):
        raise SeedreamError('模型编号无效')
    if len(references) > 10:
        raise SeedreamError('Seedream 5.0 Pro 最多支持 10 张参考图')
    if background not in ('opaque', 'transparent'):
        raise SeedreamError('背景选项无效')
    if background == 'transparent' and len(references) != 1:
        raise SeedreamError('原生透明模式只能输入一张带透明通道的参考图；不能同时附加布局 guide')
    body = {'model': model, 'prompt': prompt, 'size': valid_size(size), 'output_format': 'png',
            'background': background, 'response_format': 'b64_json', 'watermark': False}
    encoded = []
    for path in references:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            raise SeedreamError('参考图不存在或超过 30 MB')
        with Image.open(path) as image:
            if image.width*image.height > 36_000_000 or min(image.size) <= 14 or not 1/16 <= image.width/image.height <= 16:
                raise SeedreamError('参考图尺寸或宽高比超出范围')
            if background == 'transparent' and 'A' not in image.getbands() and 'transparency' not in image.info:
                raise SeedreamError('原生透明模式需要带透明通道的参考图')
            image = ImageOps.exif_transpose(image).convert('RGBA')
            if background == 'opaque':
                visible = Image.new('RGBA', image.size, 'white')
                visible.alpha_composite(image)
                image = visible.convert('RGB')
            stream = BytesIO()
            image.save(stream, format='PNG')
        if stream.tell() > MAX_IMAGE_BYTES:
            raise SeedreamError('标准化后的参考图超过 30 MB')
        encoded.append('data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
    if encoded:
        body['image'] = encoded
    return body


def generate(prompt, references, output, size='2K', model=MODEL, background='opaque', key=None, on_request=None):
    output = Path(output)
    if output.exists():
        raise SeedreamError('输出图片已存在，请使用新输出目录；不会覆盖或自动重复调用模型')
    body = build_payload(prompt, references, size, model, background)
    key = key if key is not None else credential()
    if not key:
        raise SeedreamError('请在本机设置 ARK_API_KEY 或 ARK_API_KEY_FILE')
    if not isinstance(key, str) or not re.fullmatch(r'[!-~]+', key):
        raise SeedreamError('本机凭证格式无效；请检查空白或换行，不显示凭证内容')
    request = urllib.request.Request(API_URL, json.dumps(body).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
    stage = 'generation'
    try:
        if on_request is not None:
            on_request()
        # Exactly one POST. A timeout may have been billed; do not retry.
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read(48 * 1024 * 1024 + 1))
        items = result.get('data', [])
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SeedreamError('接口未返回一张合图，未自动重试')
        item = items[0]
        if isinstance(item.get('b64_json'), str):
            content = base64.b64decode(item['b64_json'], validate=True)
        elif isinstance(item.get('url'), str):
            if urlparse(item['url']).scheme != 'https':
                raise SeedreamError('接口返回了无效图片地址')
            # Compatibility download: signed URLs are never printed or logged.
            stage = 'download'
            with urllib.request.urlopen(item['url'], timeout=90) as response:
                content = response.read(MAX_IMAGE_BYTES + 1)
        else:
            raise SeedreamError('接口没有返回可读取的图片')
        if len(content) > MAX_IMAGE_BYTES:
            raise SeedreamError('返回图片超过 30 MB')
        with Image.open(BytesIO(content)) as image:
            dimensions, image_format = list(image.size), image.format
            if image.width*image.height > 36_000_000:
                raise SeedreamError('返回图片尺寸过大')
            image.verify()
        if image_format != 'PNG':
            raise SeedreamError('接口未按要求返回 PNG，未自动重试')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream:
            stream.write(content)
        usage = result.get('usage') or {}
        if not isinstance(usage, dict):
            usage = {}
        safe_usage = {k: v for k, v in usage.items() if k in ('input_images', 'generated_images', 'output_tokens', 'total_tokens') and isinstance(v, (int, float))}
        return {'status': 'generated_pending_review', 'model_calls': 1, 'requested_model': model,
                'returned_model': result.get('model') if isinstance(result.get('model'), str) else None,
                'usage': safe_usage, 'requested_size': size, 'returned_size': dimensions,
                'image_format': image_format, 'sha256': hashlib.sha256(content).hexdigest()}
    except urllib.error.HTTPError as error:
        raise http_failure(error, key, stage) from None
    except (TimeoutError, urllib.error.URLError):
        raise SeedreamError('生图或下载连接中断，可能已计费；未自动重试') from None
    except SeedreamError:
        raise
    except Exception:
        # Library exceptions can include headers or signed URLs; do not relay them.
        raise SeedreamError('生图请求或图片响应无法处理；未自动重试，未显示私有请求内容') from None


def main():
    parser = argparse.ArgumentParser(description='使用本机密钥调用 Seedream 5.0 Pro；一张合图、一次 POST、不自动重试')
    parser.add_argument('--prompt', required=True, type=Path, help='UTF-8 提示词文件')
    parser.add_argument('--reference', action='append', default=[], type=Path, help='角色参考，可重复传入')
    parser.add_argument('--guide', type=Path, help='布局参考；只控制格子，不照画线框')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--size', default='auto', help='auto 按 guide 宽高比配置；否则 1K/1.5K/2K/宽x高')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--background', choices=('opaque', 'transparent'), default='opaque')
    parser.add_argument('--dry-run', action='store_true', help='验证输入和请求配置；不联网、不读取密钥、不写任务')
    args = parser.parse_args()
    record = None
    log = args.out / 'generation-log.json'
    try:
        prompt = args.prompt.read_text(encoding='utf-8')
        references = args.reference + ([args.guide] if args.guide else [])
        size = args.size
        if size == 'auto':
            if args.guide:
                with Image.open(args.guide) as guide:
                    size = atlas_size(guide.width / guide.height)
            else:
                size = '2K'
        build_payload(prompt, references, size, args.model, args.background)
        plan = {'requested_model': args.model, 'requested_size': size, 'reference_count': len(references),
                'background': args.background, 'model_calls': 0, 'dry_run': args.dry_run}
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False))
            return
        if log.exists() or (args.out / 'source.png').exists():
            raise SeedreamError('输出目录已有生成记录；请检查旧任务并使用新目录，避免重复计费')
        key = credential()
        if not key:
            raise SeedreamError('请在本机设置 ARK_API_KEY 或 ARK_API_KEY_FILE')
        args.out.mkdir(parents=True, exist_ok=True)
        pending = {**plan, 'status': 'prepared', 'model_calls': 0, 'reference_names': [p.name for p in references]}
        # Acquire the record before modifying any job files.
        with log.open('x', encoding='utf-8') as stream:
            json.dump(pending, stream, ensure_ascii=False, indent=2)
        record = pending
        (args.out / 'prompt.txt').write_text(prompt, encoding='utf-8')
        def submitted():
            record.update(status='generating', model_calls=1)
            log.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        receipt = generate(prompt, references, args.out / 'source.png', size, args.model, args.background, key, on_request=submitted)
        record.update(receipt)
        log.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, SeedreamError) else '本机输入或返回图片无法读取，请检查输入文件；未自动重试'
        if record is not None and log.exists():
            record.update(status='failed', message=message, automatic_retry=False)
            if isinstance(error, SeedreamAPIError):
                record['provider_error'] = error.diagnostic
            log.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        parser.exit(1, message + '\n')


if __name__ == '__main__':
    main()
