from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def chroma_to_alpha(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    px = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = px[x, y]
            green = g > 135 and g > r * 1.25 and g > b * 1.25
            white = min(r, g, b) > 245 and max(r, g, b) - min(r, g, b) < 10
            if green or white:
                px[x, y] = (r, g, b, 0)
            elif g > 100 and g > r and g > b:
                alpha = max(0, min(255, int((max(r, b) / max(1, g)) * 255)))
                px[x, y] = (r, g, b, alpha)
    return rgba


def remove_edge_connected_light(img: Image.Image, threshold: int = 220) -> Image.Image:
    rgba = img.convert("RGBA")
    px = rgba.load()
    q: deque[tuple[int, int]] = deque()
    seen: set[tuple[int, int]] = set()

    def artifact(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        neutral_light = min(r, g, b) >= threshold and max(r, g, b) - min(r, g, b) <= 35
        green_spill = g > 120 and g > r * 1.15 and g > b * 1.15
        return a > 0 and (neutral_light or green_spill)

    def add(x: int, y: int) -> None:
        if (x, y) not in seen and artifact(x, y):
            seen.add((x, y))
            q.append((x, y))

    for x in range(rgba.width):
        add(x, 0)
        add(x, rgba.height - 1)
    for y in range(rgba.height):
        add(0, y)
        add(rgba.width - 1, y)

    while q:
        x, y = q.popleft()
        r, g, b, _ = px[x, y]
        px[x, y] = (r, g, b, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < rgba.width and 0 <= ny < rgba.height:
                add(nx, ny)
    return rgba


def trim_alpha(img: Image.Image, pad: int) -> Image.Image:
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    return img.crop((
        max(0, left - pad),
        max(0, top - pad),
        min(img.width, right + pad),
        min(img.height, bottom + pad),
    ))


def normalize_sprite(img: Image.Image, target_height: int) -> Image.Image:
    scale = target_height / max(1, img.height)
    return img.resize((max(1, round(img.width * scale)), target_height), Image.Resampling.LANCZOS)


def paste_grounded(sprite: Image.Image, canvas: tuple[int, int], yoff: int = 0, xoff: int = 0) -> Image.Image:
    frame = Image.new("RGBA", canvas, (0, 0, 0, 0))
    x = (canvas[0] - sprite.width) // 2 + xoff
    y = canvas[1] - 18 - sprite.height + yoff
    frame.alpha_composite(sprite, (x, y))
    return frame


def transparent_gif_frame(img: Image.Image) -> Image.Image:
    alpha = img.getchannel("A")
    rgb = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rgb.alpha_composite(img)
    pal = rgb.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    mask = alpha.point(lambda a: 255 if a <= 8 else 0)
    pal.paste(255, mask=mask)
    palette = (pal.getpalette() or []) + [0] * 768
    palette = palette[:768]
    palette[255 * 3 : 255 * 3 + 3] = [0, 255, 0]
    pal.putpalette(palette)
    pal.info["transparency"] = 255
    return pal


def save_sequence(out: Path, name: str, frames: list[Image.Image], duration: int | list[int], loop: int | None, write_frames: bool = True) -> None:
    for folder in ["frames", "gif", "apng", "webp"]:
        (out / folder).mkdir(parents=True, exist_ok=True)
    frame_dir = out / "frames" / name
    frame_dir.mkdir(parents=True, exist_ok=True)
    if write_frames:
        for i, frame in enumerate(frames, 1):
            frame.save(frame_dir / f"f{i:02d}.png")
    gif_frames = [transparent_gif_frame(frame) for frame in frames]
    gif_loop = {} if loop is None else {"loop": loop}
    gif_frames[0].save(out / "gif" / f"{name}.gif", save_all=True, append_images=gif_frames[1:], duration=duration, disposal=2, transparency=255, **gif_loop)
    media_loop = 1 if loop is None else loop
    frames[0].save(out / "apng" / f"{name}.png", save_all=True, append_images=frames[1:], duration=duration, loop=media_loop, disposal=2)
    frames[0].save(out / "webp" / f"{name}.webp", save_all=True, append_images=frames[1:], duration=duration, loop=media_loop, lossless=True, quality=96)


def crop_sheets(manifest: dict[str, Any], manifest_dir: Path, out: Path) -> dict[str, Image.Image]:
    target_height = int(manifest.get("target_body_height", 292))
    sprites: dict[str, Image.Image] = {}
    (out / "sprites").mkdir(parents=True, exist_ok=True)
    for sheet in manifest.get("sheets", []):
        path = Path(sheet["path"])
        if not path.is_absolute():
            path = manifest_dir / path
        with Image.open(path) as source:
            src = source.convert("RGBA")
        # Explicit keys protect colored characters. Existing RGBA assets retain alpha.
        background = sheet.get("background", manifest.get("background"))
        if background is not None:
            from action_sheet import matte
            src = matte(src, background)
        has_alpha = src.getchannel("A").getextrema()[0] < 255
        cols = int(sheet["cols"])
        rows = int(sheet["rows"])
        gutter = int(sheet.get("gutter", 8))
        for name, pos in sheet["cells"].items():
            col, row = int(pos[0]), int(pos[1])
            left = round(col * src.width / cols) + gutter
            right = round((col + 1) * src.width / cols) - gutter
            top = round(row * src.height / rows) + gutter
            bottom = round((row + 1) * src.height / rows) - gutter
            cell = src.crop((left, top, right, bottom))
            if not has_alpha:
                cell = remove_edge_connected_light(chroma_to_alpha(cell))
            sprite = normalize_sprite(trim_alpha(cell, int(sheet.get("pad", 10))), target_height)
            sprites[name] = sprite
            sprite.save(out / "sprites" / f"{name}.png")
    return sprites


def build_sequence(seq: dict[str, Any], sprites: dict[str, Image.Image], canvas: tuple[int, int]) -> list[Image.Image]:
    kind = seq["type"]
    if kind == "breathe":
        base = sprites[seq["sprite"]]
        frames = int(seq.get("frames", 36))
        result = []
        for i in range(frames):
            t = math.sin(math.tau * i / frames)
            img = base.resize((round(base.width * (1 + 0.012 * t)), round(base.height * (1 + 0.025 * t))), Image.Resampling.LANCZOS)
            result.append(paste_grounded(img, canvas))
        return result
    if kind == "cycle":
        return [paste_grounded(sprites[name], canvas) for name in seq["sprites"]]
    if kind == "jump":
        base = sprites[seq["sprite"]]
        frames = int(seq.get("frames", 24))
        height = int(seq.get("height", 74))
        return [paste_grounded(base, canvas, yoff=-round(math.sin(math.pi * i / max(1, frames - 1)) * height)) for i in range(frames)]
    if kind == "smooth_locomotion":
        base = sprites[seq["sprite"]]
        if seq.get("flip", False):
            base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        frames = int(seq.get("frames", 24))
        amplitude = int(seq.get("amplitude", 3))
        tilt = float(seq.get("tilt", 1.1))
        scale = float(seq.get("scale", 1.0))
        result = []
        for i in range(frames):
            p = i / frames
            wave = math.sin(math.tau * p)
            lean = math.sin(math.tau * p + math.pi / 3) * tilt
            scaled = base.resize((
                round(base.width * scale * (1 + 0.015 * abs(wave))),
                round(base.height * scale * (1 - 0.012 * abs(wave))),
            ), Image.Resampling.LANCZOS)
            result.append(paste_grounded(scaled.rotate(lean, resample=Image.Resampling.BICUBIC, expand=True), canvas, yoff=round(wave * amplitude)))
        return result
    raise ValueError(f"Unsupported sequence type: {kind}")


def write_preview(out: Path, sequences: list[dict[str, Any]], canvas: tuple[int, int]) -> None:
    cards = "\n".join(
        f'<article><div class="checker"><img src="gif/{seq["name"]}.gif?v=1" alt="{seq["name"]}"></div><h2>{seq["name"]}</h2></article>'
        for seq in sequences
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cartoon Animation Preview</title>
<style>
body{{margin:0;padding:24px;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f7fb;color:#202733}}
h1{{font-size:22px;margin:0 0 16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}}
article{{border:1px solid #dbe3ef;border-radius:8px;background:#fff;overflow:hidden}}h2{{font-size:13px;margin:10px 12px 12px}}
.checker{{height:190px;display:grid;place-items:center;background-color:#f8fafc;background-image:linear-gradient(45deg,#e9edf4 25%,transparent 25%),linear-gradient(-45deg,#e9edf4 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#e9edf4 75%),linear-gradient(-45deg,transparent 75%,#e9edf4 75%);background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}}
.checker img{{width:{min(canvas[0], 170)}px;height:{min(canvas[1], 170)}px;object-fit:contain}}
</style>
</head>
<body><h1>Cartoon Animation Preview</h1><main class="grid">{cards}</main></body></html>
"""
    (out / "animation_preview.html").write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--task", choices=("build", "prepare", "audit", "repair", "review"), default="build")
    parser.add_argument("--sheet", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--action")
    parser.add_argument("--verdict", choices=("approved", "rejected"))
    parser.add_argument("--note")
    parser.add_argument("--defect-stage", choices=("upstream", "processing", "animation"))
    args = parser.parse_args()

    if args.task == "review":
        if not all((args.action, args.verdict, args.note)):
            parser.error("review needs --action, --verdict and --note")
        from action_sheet import review
        print(json.dumps(review(args.out, args.action, args.verdict, args.note, args.defect_stage), ensure_ascii=False))
        return
    if not args.manifest:
        parser.error("--manifest is required")
    manifest = load_manifest(args.manifest)
    if manifest.get("schema") == "pet-action-request/v1":
        from action_sheet import validate, prepare, build, repair, read
        validate(manifest)
        if args.task == "prepare":
            result = prepare(manifest, args.out)
        elif args.task == "audit":
            if not args.sheet:
                parser.error("alpha audit needs --sheet")
            from alpha_qa import audit
            result = audit(manifest, args.sheet, args.out)
        elif args.task == "repair":
            if not args.previous:
                parser.error("repair needs --previous bundle")
            result = repair(manifest, read(args.previous / "manifest.json"), args.out)
        else:
            if not args.sheet:
                parser.error("action-row build needs --sheet")
            result = build(manifest, args.sheet, args.out, args.previous)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.task != "build" or args.sheet or args.previous:
        parser.error("legacy manifest supports build without --sheet/--previous")
    canvas = tuple(manifest.get("canvas", [360, 360]))
    if len(canvas) != 2:
        raise ValueError("canvas must be [width, height]")
    canvas_pair = (int(canvas[0]), int(canvas[1]))
    args.out.mkdir(parents=True, exist_ok=True)
    sprites = crop_sheets(manifest, args.manifest.resolve().parent, args.out)
    sequences = manifest.get("sequences", [])
    for seq in sequences:
        frames = build_sequence(seq, sprites, canvas_pair)
        save_sequence(args.out, seq["name"], frames, int(seq.get("duration", 70)), int(seq.get("loop", 0)))
    write_preview(args.out, sequences, canvas_pair)


if __name__ == "__main__":
    main()
