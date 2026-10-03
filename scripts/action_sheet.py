#!/usr/bin/env python3
"""Action-row extraction used by build_animation_assets.py; no model calls."""
import base64
import copy
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pixels(image):
    return image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def validate(r):
    if r.get("schema") != "pet-action-request/v1":
        raise ValueError("request schema must be pet-action-request/v1")
    layout = r["layout"]
    cols = layout["columns"]
    size = layout["frame_size"]
    if not isinstance(cols, int) or not 1 <= cols <= 32:
        raise ValueError("columns must be 1..32")
    if len(size) != 2 or any(not isinstance(v, int) or v < 32 for v in size):
        raise ValueError("frame_size must contain two integers >=32")
    pad = layout.get("padding", 12)
    if not isinstance(pad, int) or pad < 0 or 2 * pad >= min(size):
        raise ValueError("invalid padding")
    if layout.get("placement", "preserve-cell") not in ("preserve-cell", "grounded"):
        raise ValueError("unknown placement")
    ids = set()
    if not r["actions"]:
        raise ValueError("no actions")
    for a in r["actions"]:
        key = a["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or key in ids:
            raise ValueError(f"invalid or duplicate action id: {key}")
        ids.add(key)
        if not isinstance(a["frames"], int) or not 1 <= a["frames"] <= cols:
            raise ValueError(f"invalid frame count: {key}")
        if not isinstance(a["fps"], (int, float)) or not math.isfinite(a["fps"]) or a["fps"] <= 0:
            raise ValueError(f"invalid fps: {key}")
        if not isinstance(a.get("loop"), bool):
            raise ValueError(f"loop must be boolean: {key}")
        motion = a.get("motion", {"type": "none"})
        kind = motion.get("type", "none")
        if kind not in ("none", "translate", "arc"):
            raise ValueError(f"unsupported motion: {key}")
        if motion.get("finish", "keep_position") not in ("keep_position", "return_origin"):
            raise ValueError(f"unknown finish: {key}")
        if motion.get("easing", "linear") not in ("linear", "ease_in_out"):
            raise ValueError(f"unknown easing: {key}")
        for field in ("dx", "dy", "height", "duration_ms"):
            value = motion.get(field, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid motion {field}: {key}")
        if kind != "none" and motion.get("duration_ms", 0) <= 0:
            raise ValueError(f"moving action needs duration_ms: {key}")
        owner = a.get("root_motion", "runtime")
        if owner not in ("runtime", "frames"):
            raise ValueError(f"unknown root_motion: {key}")
        if owner == "frames" and (kind != "none" or layout.get("placement") == "grounded"):
            raise ValueError(f"double/discarded root motion: {key}")
        offsets = a.get("frame_offsets", [])
        if offsets and (len(offsets) != a["frames"] or any(len(p) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p) for p in offsets)):
            raise ValueError(f"invalid frame_offsets: {key}")
    bg = r.get("background", {"mode": "alpha"})
    if bg.get("mode") not in ("alpha", "chroma"):
        raise ValueError("background mode must be alpha or chroma")
    if bg["mode"] == "chroma" and not re.fullmatch(r"#[0-9a-fA-F]{6}", bg.get("key", "")):
        raise ValueError("chroma key must be #RRGGBB")
    return r


def prepare(r, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write(out / "request.json", r)
    cols, rows = r["layout"]["columns"], len(r["actions"])
    w, h = r["layout"]["frame_size"]
    pad = r["layout"].get("padding", 12)
    guide = Image.new("RGB", (cols * w, rows * h), "#f5f4ee")
    draw = ImageDraw.Draw(guide)
    prompt = ["Use case: illustration-story", "Asset type: one multi-action pet sprite atlas candidate.",
              "Produce ONE image, not separate images. Exactly %d columns by %d rows, read left-to-right then top-to-bottom." % (cols, rows),
              "Subject / identity: " + r.get("style", "Keep the same pet identity throughout."),
              "References: pet/style reference owns appearance; layout guide owns only row/column count, spacing and safe padding. Do not copy labels, lines, scenery or text from references.",
              "Keep the same body scale, proportions, eye spacing, colors, line style and accessories across every pose. Preserve crouching/lying height changes; do not enlarge short poses to standing height.",
              "Complete bodies with limbs and props inside each invisible slot; clear separation; no visible grid, text, numbering, watermark, shadows, speed lines, floating effects or background scene."]
    bg = r.get("background", {"mode": "alpha"})
    prompt.append("Background: genuine transparent alpha PNG; NO painted checkerboard. The pet, belly, pale markings and held props remain opaque; transparency belongs only outside the subject and in intentional gaps. No white matte halo." if bg["mode"] == "alpha" else "Background: perfectly flat " + bg["key"] + "; never use this key color on the pet, including belly, outlines, accessories or props. Preserve pale and white subject parts; no glow or blended backdrop.")
    for row, a in enumerate(r["actions"]):
        prompt.append(f"ROW {row + 1}: {a['name']} ({a['id']}), exactly {a['frames']} poses. " + a.get("poses", ""))
        prompt.append("Overall body location stays in place; motion is performed by the player. Keep a common foot baseline while changing pose." if a.get("root_motion", "runtime") == "runtime" else "Preserve the intended whole-body position trajectory inside the slots; the player adds no root motion.")
        if a["frames"] < cols:
            prompt.append(f"Leave the final {cols - a['frames']} slots in this row EMPTY, do not add duplicate poses.")
        if a.get("loop"):
            prompt.append("Last pose connects naturally to the first; distinct intermediate motion where requested.")
        else:
            prompt.append("Clear anticipation, action and recovery; last pose returns to the stated resting stance.")
        for col in range(cols):
            x, y = col * w, row * h
            draw.rectangle((x, y, x + w - 1, y + h - 1), outline="#7b7b71", width=2)
            if col < a["frames"]:
                draw.rectangle((x + pad, y + pad, x + w - pad - 1, y + h - pad - 1), outline="#72988a", width=2)
                draw.line((x + w // 2, y + pad, x + w // 2, y + h - pad), fill="#c5ccc7")
    guide.save(out / "layout-guide.png")
    (out / "prompt.txt").write_text("\n\n".join(prompt), encoding="utf-8")
    return {"prepared": str(out.resolve()), "actions": len(r["actions"]), "model_calls": 0}


def matte(image, bg):
    image = image.convert("RGBA")
    data = list(pixels(image))
    if bg["mode"] == "alpha":
        if not any(p[3] == 0 for p in data):
            raise ValueError("No real transparent pixels. Painted background is not alpha; preserve source and use explicit chroma config or report failure.")
        result = [(0, 0, 0, 0) if p[3] == 0 else p for p in data]
    else:
        key = tuple(int(bg["key"][i:i + 2], 16) for i in (1, 3, 5))
        threshold = float(bg.get("threshold", 40))
        softness = float(bg.get("softness", 35))
        if threshold < 0 or softness <= 0:
            raise ValueError("invalid chroma thresholds")
        result = []
        for *rgb, alpha in data:
            distance = math.sqrt(sum((rgb[i] - key[i]) ** 2 for i in range(3)))
            coverage = max(0, min(1, (distance - threshold) / softness))
            a = round(alpha * coverage)
            if not a:
                result.append((0, 0, 0, 0))
            elif coverage < 1:
                color = [round(max(0, min(255, (rgb[i] - (1 - coverage) * key[i]) / coverage))) for i in range(3)]
                result.append((*color, a))
            else:
                result.append((*rgb, a))
        if not any(p[3] == 0 for p in result):
            raise ValueError("chroma removed no background; inspect the actual key")
    image.putdata(result)
    return image


def components(image):
    w, h = image.size
    alpha = image.getchannel("A").tobytes()
    seen = bytearray(w * h)
    found = []
    for origin in range(len(alpha)):
        if seen[origin] or alpha[origin] <= 8:
            continue
        seen[origin] = 1
        queue = deque([origin])
        pixels, sx, sy = [], 0, 0
        x0, y0, x1, y1 = w, h, 0, 0
        while queue:
            p = queue.popleft()
            x, y = p % w, p // w
            pixels.append(p)
            sx += x
            sy += y
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + 1), max(y1, y + 1)
            for n in (p - 1 if x else -1, p + 1 if x < w - 1 else -1, p - w if y else -1, p + w if y < h - 1 else -1):
                if n >= 0 and not seen[n] and alpha[n] > 8:
                    seen[n] = 1
                    queue.append(n)
        found.append({"pixels": pixels, "area": len(pixels), "center": [sx / len(pixels), sy / len(pixels)], "bbox": [x0, y0, x1, y1]})
    return found


def extract(r, image):
    cols, rows = r["layout"]["columns"], len(r["actions"])
    sw, sh = image.width / cols, image.height / rows
    slots = {(row, col): [] for row in range(rows) for col in range(cols)}
    errors = {a["id"]: [] for a in r["actions"]}
    warnings = {a["id"]: [] for a in r["actions"]}
    min_fragment = max(3, round(image.width * image.height * 0.000002))
    for comp in components(image):
        if comp["area"] < min_fragment:
            continue
        col = min(cols - 1, int(comp["center"][0] / sw))
        row = min(rows - 1, int(comp["center"][1] / sh))
        box = comp["bbox"]
        if box[2] - box[0] > sw * 1.18 or box[3] - box[1] > sh * 1.18:
            for rr in range(max(0, int(box[1] / sh)), min(rows, math.ceil(box[3] / sh))):
                errors[r["actions"][rr]["id"]].append("component spans multiple cells; merged poses need inspection")
        slots[row, col].append(comp)
    cw, ch = r["layout"]["frame_size"]
    pad = r["layout"].get("padding", 12)
    scale = min((cw - 2 * pad) / sw, (ch - 2 * pad) / sh)
    filter_ = Image.Resampling.NEAREST if r["layout"].get("pixel_art") else Image.Resampling.LANCZOS
    src = image.load()
    results = {}
    for row, a in enumerate(r["actions"]):
        key, frames, metrics = a["id"], [], []
        for col in range(a["frames"]):
            parts = slots[row, col]
            if not parts or sum(c["area"] for c in parts) < max(20, sw * sh * 0.003):
                errors[key].append(f"frame {col + 1}: empty/sparse")
                continue
            main = max(parts, key=lambda c: c["area"])
            mx0, my0, mx1, my1 = main["bbox"]
            reach = max(6, min(sw, sh) * 0.06)
            retained = [c for c in parts if c["area"] >= main["area"] * 0.35 or (
                c["bbox"][0] < mx1 + reach and c["bbox"][2] > mx0 - reach and
                c["bbox"][1] < my1 + reach and c["bbox"][3] > my0 - reach)]
            if len(retained) != len(parts):
                warnings[key].append(f"frame {col + 1}: removed {len(parts) - len(retained)} remote tiny fragment(s); compare with source")
            parts = retained
            if sum(c["area"] > main["area"] * 0.35 for c in parts) > 1:
                warnings[key].append(f"frame {col + 1}: multiple large components, check detached limbs/props")
            x0, y0 = min(c["bbox"][0] for c in parts), min(c["bbox"][1] for c in parts)
            x1, y1 = max(c["bbox"][2] for c in parts), max(c["bbox"][3] for c in parts)
            crop = Image.new("RGBA", (x1 - x0, y1 - y0))
            dest = crop.load()
            for comp in parts:
                for p in comp["pixels"]:
                    x, y = p % image.width, p // image.width
                    dest[x - x0, y - y0] = src[x, y]
            crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), filter_)
            if r["layout"].get("placement", "preserve-cell") == "grounded":
                left, top = round((cw - crop.width) / 2), ch - pad - crop.height
            else:
                left = round((cw - sw * scale) / 2 + (x0 - col * sw) * scale)
                top = round((ch - sh * scale) / 2 + (y0 - row * sh) * scale)
            if a.get("frame_offsets"):
                dx, dy = a["frame_offsets"][col]
                left, top = round(left + dx), round(top + dy)
            if left < 0 or top < 0 or left + crop.width > cw or top + crop.height > ch:
                errors[key].append(f"frame {col + 1}: extracted contour would clip output canvas")
                continue
            frame = Image.new("RGBA", (cw, ch))
            frame.alpha_composite(crop, (left, top))
            # Clear hidden RGB introduced by resampling.
            frame.putdata([(0, 0, 0, 0) if p[3] == 0 else p for p in pixels(frame)])
            frames.append(frame)
            metrics.append({"source_bbox": [x0, y0, x1, y1], "output_bbox": list(frame.getchannel("A").getbbox()), "scale": scale})
        if any(slots[row, col] for col in range(a["frames"], cols)):
            warnings[key].append("unused slots contain unexpected artwork")
        results[key] = {"frames": frames, "metrics": metrics, "errors": list(dict.fromkeys(errors[key])), "warnings": warnings[key]}
    return results


def contact(frames, path):
    cw, ch = frames[0].size
    canvas = Image.new("RGBA", (cw * len(frames), ch), "#f1eee3")
    draw = ImageDraw.Draw(canvas)
    for y in range(0, ch, 12):
        for x in range(0, canvas.width, 12):
            if (x // 12 + y // 12) % 2:
                draw.rectangle((x, y, x + 11, y + 11), fill="#dad8cc")
    for i, frame in enumerate(frames):
        canvas.alpha_composite(frame, (i * cw, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(path)


def compose(out, manifest):
    from build_animation_assets import save_sequence, write_preview
    cw, ch = manifest["frame_size"]
    cols = max([len(a["frames"]) for a in manifest["actions"].values()] + [1])
    atlas = Image.new("RGBA", (cols * cw, len(manifest["actions"]) * ch))
    for row, (key, action) in enumerate(manifest["actions"].items()):
        for col, f in enumerate(action["frames"]):
            with Image.open(out / f["png"]) as frame:
                atlas.alpha_composite(frame.convert("RGBA"), (col * cw, row * ch))
            f.update(x=col * cw, y=row * ch, w=cw, h=ch)
        standalone = copy.deepcopy(action)
        standalone["schema"] = "pet-action/v1"
        standalone["sprite_sheet"] = "../atlas.png"
        for f in standalone["frames"]:
            f["png"] = "../" + f["png"]
        write(out / "actions" / (key + ".json"), standalone)
    atlas.save(out / "atlas.png")
    manifest["sheet_size"] = list(atlas.size)
    write(out / "manifest.json", manifest)
    playable = []
    for key, action in manifest["actions"].items():
        if not action["frames"]:
            continue
        frames = []
        for record in action["frames"]:
            with Image.open(out / record["png"]) as frame:
                frames.append(frame.convert("RGBA"))
        save_sequence(out, key, frames, [f["duration_ms"] for f in action["frames"]],
                      0 if action.get("loop") else None, write_frames=False)
        playable.append({"name": key})
    write_preview(out, playable, (cw, ch))
    # Embed media so the delivered preview also works offline without fetch/file CORS.
    def data_image(path):
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    embedded = {"pack": manifest, "atlas": data_image(out / "atlas.png"), "contacts": {}}
    for key, a in manifest["actions"].items():
        path = out / "qa" / (key + "-contact.png")
        if a["frames"] and path.exists():
            embedded["contacts"][key] = data_image(path)
    payload = json.dumps(embedded, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "assets" / "action_preview.html").read_text(encoding="utf-8")
    first_action = manifest["actions"].get("idle")
    if not first_action or not first_action["frames"]:
        first_action = next((a for a in manifest["actions"].values() if a["frames"]), None)
    first = first_action["frames"][0] if first_action else None
    template = template.replace("__FIRST_FRAME__", data_image(out / first["png"]) if first else "")
    ax, ay = first_action.get("anchor", [cw / 2, ch]) if first_action else (cw / 2, ch)
    style = f"position:absolute;width:{cw}px;height:{ch}px;left:{180-ax}px;top:{280-ay}px" if first else "display:none"
    template = template.replace("__FALLBACK_STYLE__", style)
    (out / "preview.html").write_text(template.replace("<script>", '<script id="embedded-data" type="application/json">' + payload + "</script>\n<script>", 1), encoding="utf-8")


def build(r, sheet, out, previous=None):
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("output already exists; choose a new version directory")
    previous = Path(previous).resolve() if previous else None
    if previous and (out == previous or previous in out.parents):
        raise ValueError("output must not be inside previous bundle")
    with Image.open(sheet) as source:
        raw = source.convert("RGBA")
        image = matte(raw, r.get("background", {"mode": "alpha"}))
    extracted = extract(r, image)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".stage-", dir=out.parent))
    try:
        if previous:
            old = read(previous / "manifest.json")
            if old["pet_id"] != r["pet_id"] or old["layout"] != r["layout"]:
                raise ValueError("previous identity/layout mismatch")
            shutil.copytree(previous, stage, dirs_exist_ok=True)
            manifest = old
        else:
            manifest = {"schema": "pet-action-atlas/v1", "pet_id": r["pet_id"], "frame_size": r["layout"]["frame_size"], "layout": r["layout"], "sprite_sheet": "atlas.png", "actions": {}, "sources": []}
        source_id = hashlib.sha256(Path(sheet).read_bytes()).hexdigest()
        source_path = stage / "source" / (source_id[:12] + ".png")
        source_path.parent.mkdir(exist_ok=True)
        # Copy actual encoding losslessly instead of re-encoding the source.
        shutil.copy2(sheet, source_path)
        manifest["sources"].append({"file": str(source_path.relative_to(stage)), "sha256": source_id, "size": list(image.size), "background": r.get("background", {"mode": "alpha"})})
        from alpha_qa import audit_sheet
        alpha_report = audit_sheet(r, raw, image, stage, extracted)
        report = {"schema": "pet-action-qa/v1", "source_sha256": source_id, "actions": {}, "visual_qa": "not_automatically_verified", "alpha_qa": "alpha-qa.json"}
        for a in r["actions"]:
            key, result = a["id"], extracted[a["id"]]
            result["warnings"].extend(alpha_report["actions"][key]["warnings"])
            if result["errors"]:
                if key in manifest["actions"] and manifest["actions"][key]["frames"]:
                    manifest["actions"][key]["last_repair_failed"] = result["errors"]
                else:
                    manifest["actions"][key] = {**a, "frames": [], "status": "extraction_failed", "errors": result["errors"], "warnings": result["warnings"]}
                report["actions"][key] = {"ok": False, "errors": result["errors"], "warnings": result["warnings"]}
                continue
            folder = stage / "frames" / key
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
            records = []
            for i, (frame, metric) in enumerate(zip(result["frames"], result["metrics"])):
                file = folder / f"f{i + 1:02d}.png"
                frame.save(file)
                records.append({"png": str(file.relative_to(stage)), "duration_ms": round(1000 / a["fps"]), **metric})
            contact(result["frames"], stage / "qa" / (key + "-contact.png"))
            action = {**a, "frames": records, "anchor": [r["layout"]["frame_size"][0] / 2, r["layout"]["frame_size"][1] - r["layout"].get("padding", 12)], "status": "pending_visual_review", "errors": [], "warnings": result["warnings"], "source_sha256": source_id}
            manifest["actions"][key] = action
            report["actions"][key] = {"ok": True, "frames": len(records), "warnings": result["warnings"]}
        write(stage / "request.json", r)
        write(stage / "qa-report.json", report)
        compose(stage, manifest)
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"output": str(out), "actions": {k: a["status"] for k, a in manifest["actions"].items()}, "failed": [k for k, v in report["actions"].items() if not v["ok"]]}


def review(out, key, verdict, note, defect_stage=None):
    if not note.strip():
        raise ValueError("visual review needs a specific note")
    if defect_stage is not None and (verdict != "rejected" or defect_stage not in ("upstream", "processing", "animation")):
        raise ValueError("defect_stage is upstream/processing/animation and only applies to rejection")
    out = Path(out)
    manifest = read(out / "manifest.json")
    a = manifest["actions"][key]
    if not a["frames"]:
        raise ValueError("cannot visually approve/reject empty action; extraction_failed remains")
    a["status"], a["review_note"] = verdict, note
    if verdict == "approved":
        a.pop("defect_stage", None)
    elif defect_stage is not None:
        a["defect_stage"] = defect_stage
    compose(out, manifest)
    return {"action": key, "status": verdict}


def repair(r, manifest, out):
    processing = [key for key, a in manifest["actions"].items() if a["status"] in ("extraction_failed", "rejected") and a.get("defect_stage") == "processing"]
    bad = {key for key, a in manifest["actions"].items() if a["status"] in ("extraction_failed", "rejected") and key not in processing}
    selected = [copy.deepcopy(a) for a in r["actions"] if a["id"] in bad]
    if not selected:
        return {"repair_needed": False, "processing_actions": processing, "reason": "processing defects need reprocessing, not generation" if processing else "no known failed actions; pending_visual_review is not a failure"}
    for a in selected:
        old = manifest["actions"][a["id"]]
        a["poses"] = a.get("poses", "") + "\nRepair only these defects: " + old.get("review_note", "; ".join(old.get("errors", [])))
    result = copy.deepcopy(r)
    result["actions"] = selected
    return {"repair_needed": True, "processing_actions": processing, **prepare(result, out)}
