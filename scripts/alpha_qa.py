"""Read-only alpha diagnostics. Findings are candidates, never automatic repairs."""
from collections import deque
from pathlib import Path

from PIL import Image


def inspect_alpha(image):
    image = image.convert("RGBA")
    w, h = image.size
    rgba = list(image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata())
    alpha = [p[3] for p in rgba]

    def neighbors(p):
        x, y = p % w, p // w
        return [n for n in (p - 1 if x else -1, p + 1 if x + 1 < w else -1,
                            p - w if y else -1, p + w if y + 1 < h else -1) if n >= 0]

    # A low-alpha component enclosed by opaque artwork can be a cutout defect
    # OR a real gap between limbs. Report coordinates; do not fill it.
    seen = bytearray(w * h)
    holes = []
    for start, a in enumerate(alpha):
        if a >= 128 or seen[start]:
            continue
        queue = deque([start])
        seen[start] = 1
        area, border = 0, False
        x0, y0, x1, y1 = w, h, 0, 0
        while queue:
            p = queue.popleft()
            x, y = p % w, p // w
            area += 1
            border |= x == 0 or y == 0 or x == w - 1 or y == h - 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + 1), max(y1, y + 1)
            for n in neighbors(p):
                if not seen[n] and alpha[n] < 128:
                    seen[n] = 1
                    queue.append(n)
        if not border and area >= 4:
            holes.append({"bbox": [x0, y0, x1, y1], "pixels": area})

    # Pale partial-alpha pixels near an opaque contour may carry white matte.
    # White feathers and legitimate antialiasing can also match this heuristic.
    fringe = []
    for p, (r, g, b, a) in enumerate(rgba):
        if not (8 < a < 240 and min(r, g, b) >= 200 and max(r, g, b) - min(r, g, b) <= 45):
            continue
        near = set(neighbors(p))
        near.update(n for q in list(near) for n in neighbors(q))
        if any(alpha[n] >= 240 for n in near) and any(alpha[n] < 8 for n in near):
            fringe.append(p)
    warnings = []
    if holes:
        warnings.append(f"{len(holes)} enclosed low-alpha region(s); check belly, props and intentional gaps")
    if len(fringe) >= 4:
        warnings.append(f"{len(fringe)} pale partial-alpha edge pixels; inspect on dark background")
    return {"size": [w, h], "transparent_pixels": sum(a == 0 for a in alpha),
            "partial_alpha_pixels": sum(0 < a < 255 for a in alpha),
            "opaque_pixels": sum(a == 255 for a in alpha),
            "enclosed_low_alpha_regions": holes, "pale_edge_pixels": len(fringe),
            "warnings": warnings, "semantic_verdict": "requires_visual_review"}


def strip(frames, path, background):
    w, h = frames[0].size
    board = Image.new("RGBA", (w * len(frames), h), background)
    for i, frame in enumerate(frames):
        board.alpha_composite(frame.convert("RGBA"), (i * w, 0))
    board.convert("RGB").save(path)


def source_strip(frames, path):
    w, h = frames[0].size
    board = Image.new("RGB", (w * len(frames), h * 3))
    for i, frame in enumerate(frames):
        # RGB ignoring alpha is diagnostic only: hidden RGB is not proof of
        # correct subject segmentation and must not replace the delivered PNG.
        board.paste(frame.convert("RGB"), (i * w, 0))
        dark = Image.new("RGBA", frame.size, "#293a35")
        dark.alpha_composite(frame)
        board.paste(dark.convert("RGB"), (i * w, h))
        board.paste(frame.getchannel("A").convert("RGB"), (i * w, h * 2))
    board.save(path)


def audit_sheet(request, source, matted, out, extracted):
    from action_sheet import read, write
    out = Path(out)
    (out / "qa").mkdir(parents=True, exist_ok=True)
    source = source.convert("RGBA")
    cols, rows = request["layout"]["columns"], len(request["actions"])
    old = read(out / "alpha-qa.json") if (out / "alpha-qa.json").exists() else {}
    report = {"schema": "pet-alpha-qa/v1", "automatic_acceptance": False,
              "limits": "Heuristics miss open cutouts and cannot distinguish real gaps or pale antialiasing from defects.",
              "actions": old.get("actions", {})}
    for row, action in enumerate(request["actions"]):
        key = action["id"]
        originals, records, warnings = [], [], []
        for col in range(action["frames"]):
            box = (round(col * source.width / cols), round(row * source.height / rows),
                   round((col + 1) * source.width / cols), round((row + 1) * source.height / rows))
            raw, processed = source.crop(box), matted.crop(box)
            originals.append(raw)
            before, after = inspect_alpha(raw), inspect_alpha(processed)
            records.append({"frame": col + 1, "source_cell": list(box), "source": before, "after_matte": after})
            for stage, finding in (("source", before), ("after_matte", after)):
                warnings.extend(f"alpha QA frame {col + 1} ({stage}): {w}" for w in finding["warnings"])
        source_strip(originals, out / "qa" / (key + "-alpha-source.png"))
        frames = extracted[key]["frames"] if not extracted[key]["errors"] else []
        if frames:
            for record, frame in zip(records, frames):
                record["export"] = inspect_alpha(frame)
                warnings.extend(f"alpha QA frame {record['frame']} (export): {w}" for w in record["export"]["warnings"])
            strip(frames, out / "qa" / (key + "-contact-light.png"), "#f5f3ec")
            strip(frames, out / "qa" / (key + "-contact-dark.png"), "#293a35")
        report["actions"][key] = {"frames": records, "warnings": warnings,
                                  "source_diagnostic": f"qa/{key}-alpha-source.png"}
    write(out / "alpha-qa.json", report)
    return report


def audit(request, sheet, out):
    from action_sheet import extract, matte
    out = Path(out)
    if out.exists():
        raise ValueError("audit output already exists; choose a separate diagnostic directory")
    with Image.open(sheet) as source:
        raw = source.convert("RGBA")
    matted = matte(raw, request.get("background", {"mode": "alpha"}))
    report = audit_sheet(request, raw, matted, out, extract(request, matted))
    return {"output": str(out.resolve()), "model_calls": 0, "source_modified": False,
            "warning_actions": [key for key, a in report["actions"].items() if a["warnings"]]}
