---
name: cartoon-character-animator
description: Generate consistent cartoon character animation assets from one or more user reference photos. Use when the user wants to turn a real person, child, mascot, or pet photo into a reusable cartoon character and export specified actions such as idle, wave, jump, heart, walk, run, celebrate, sad, angry, dance, or custom actions as transparent PNG frames, GIF, APNG, WebP, and an HTML preview.
---

# Cartoon Character Animator

## Workflow

1. Collect inputs: reference photo(s), requested actions, target style, output folder, target canvas size, and required formats.
2. Create a fixed character bible before generating any action sheets. Include face, hair, outfit, body proportion, style, camera angle rules, and forbidden changes.
3. Generate bitmap image sheets, not SVGs. Use the available image generation tool and pass the user photo(s) as visual reference whenever possible.
4. Keep consistency by generating related frames in grids: front-facing interaction actions in one sheet, side-facing locomotion actions in another sheet.
5. Build assets with `scripts/build_animation_assets.py` from a manifest. The script crops grids, removes green/white edge artifacts, normalizes character height, anchors feet to the same baseline, exports frames/GIF/APNG/WebP, and creates a preview page.
6. Preview the output and inspect the actual character bbox, not just image element size. If a character appears smaller, adjust `target_body_height` or sequence `scale`.

## Generation Rules

- Prefer one clean master character image first, then use it as reference for all action sheets.
- Keep the same clothing, hair, facial proportions, and rendering style in every prompt.
- Use a plain green or white background for source sheets. Green is easier for edge cleanup when the character has white shoes or clothing.
- Ask for no text, labels, borders, grid captions, watermarks, shadows touching the edge, or decorative frames.
- For repeated actions, generate a multi-frame sheet in one image. Separate single images usually drift in size, pose, lighting, and face.
- For walk/run, be honest about image model limits. If true left/right leg alternation fails, use a smooth side-view glide/bob animation for small desktop-pet use, or ask for image-to-video tooling for real gait.

Read `references/prompting.md` when drafting prompts. Read `references/manifest.md` when creating or editing the asset manifest.

## Asset Build

Create a folder like:

```text
my-output/
├── manifest.json
└── source/
    ├── front_actions_sheet.png
    └── side_locomotion_sheet.png
```

Then run:

```bash
python3 /path/to/cartoon-character-animator/scripts/build_animation_assets.py \
  --manifest my-output/manifest.json \
  --out my-output
```

The script writes:

```text
my-output/
├── sprites/
├── frames/
├── gif/
├── apng/
├── webp/
└── animation_preview.html
```

## Quality Checks

- Open `animation_preview.html` through a local HTTP server when browser file access blocks media.
- Check walk/run frame PNGs directly if the preview looks wrong.
- Confirm transparent edges are clean: no white dividers, green spill, or source-sheet borders.
- Compare alpha bbox heights across actions; the person height should be consistent even if the PNG canvas size differs.
- Avoid infinite-loop previews for one-shot actions. In preview UIs, play wave/jump/heart/custom reaction actions once, then return to idle.
