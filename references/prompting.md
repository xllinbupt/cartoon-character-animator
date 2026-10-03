# Prompting

## Character Bible Template

Use a fixed paragraph like this for every generated sheet:

```text
Create a highly stylized cute cartoon character based on the provided reference photo(s): [age/person description], [face shape], [hair], [key features], [outfit]. Keep the same identity, outfit, body proportion, color palette, and rendering style in every panel. Full body, centered, [background specification selected from request.json], no text, no labels, no watermark, no frame border.
```

For a child, avoid making the result photorealistic. Ask for a clearly cartoon mascot interpretation:

```text
Make it more cartoon than realistic: big head, rounded face, simplified features, soft 3D storybook rendering, warm colors, playful expression, child-safe cute mascot style.
```

## Sheet Prompts

For the user's pet-action workflow, default to one row per action and twelve generated time samples per row. The following small grids are retained for legacy key-pose/procedural animation requests; they are not the default twelve-frame action sheet.

Front actions:

```text
[character bible]
Create a 3 columns by 2 rows action sprite sheet. Each panel shows the exact same character, same scale, same outfit, same face, full body centered. Panels: idle relaxed smile; wave pose A; wave pose B with the same hand; heart hands; jump pose; custom reaction. [Selected background specification]. No text, no labels, no borders.
```

Side locomotion:

```text
[character bible]
Create a side-view action sprite sheet, character facing right, same scale in every panel. 4 columns by 2 rows. Top row: walk contact, walk down, opposite contact, walk up. Bottom row: run stride, run gather, opposite stride, run gather. Full body centered, [selected background specification], no text, no labels, no borders.
```

Custom action:

```text
[character bible]
Create a [columns] by [rows] sprite sheet for the action: [action]. The same character appears at the same scale in every panel. Show a coherent sequence from anticipation, action peak, recovery, then return-to-idle. [Selected background specification]. No text, no labels, no borders.
```

Background specification follows request.json: use a tested genuine-alpha path, or a flat chroma key absent from the pet and all props. Do not default to green for a green pet. Explicitly keep pale belly, markings and held props opaque; no white matte halo. See [alpha-quality.md](alpha-quality.md) for background selection and inspection.

## Consistency Tactics

- Reuse the same generated master character image as image reference for every sheet.
- Keep action sheets grouped by camera angle: front-facing for interaction, side-facing for movement.
- Avoid changing clothing in action prompts. If clothing matters, spell it out every time.
- If a sheet includes visible panel dividers, crop with extra gutter and rely on edge artifact cleanup.

## Twelve-frame action planning

Reuse the character bible and identity reference in a single multi-action sheet. Specify canvas and invisible cells consistently with request.json; prepare generates the guide from those values. A guide is geometry only. All limbs/props stay inside each slot's safe padding, without grid lines, labels or scenery.

Write a temporal plan per row rather than twelve independent expressions. Repeat the specific constraints for failure-prone anatomy:

- **Wave:** name the same anatomical hand throughout. Frames 1–4 rest, lift to chest, lift higher, reach wave position; 5–8 wrist out/in/out/in while the other arm stays down; 9–12 lower toward chest, lower, rest, settle. Never raise the other hand to the face or turn the wave into rubbing eyes. If the source identity is three-quarter view, keep it throughout the row.
- **Walk:** strictly right-facing side profile, both feet visible and visually separate, unchanged head/body. Twelve phases: right-foot contact, right weight down, left toe-off, left passing, left forward swing, transition; left-foot contact, left weight down, right toe-off, right passing, right forward swing, transition. Show leg motion, not only changing arms. In-place gait when runtime owns translation. Side locomotion may be separated into a repair row if mixed-view generation fails; do not reduce frame count.
- **Hop:** standing, slight crouch, deep crouch, anticipation, leg extension, stretched takeoff pose, tucked airborne pose, legs open, prepare landing, deep landing compression, slight crouch, recover. Runtime-owned hop requests pose changes only; preserve natural height differences. Never add another vertical image trajectory on top of the runtime arc.
- **Map:** rest, reach backpack, retrieve folded map, grip with both hands, partly unfold, fully unfold, look down, look across, partly fold, fold, store, rest. The same map remains attached to hands during handling, no abrupt replacement by a book, flag or another prop.
- **Idle/sleep:** use subtle continuous breathing and consistent blink timing. Few visible changes may be appropriate here; do not reuse that minimal amplitude as justification for unclear walk/wave actions.

These plans improve constraint clarity but do not guarantee that a model follows it. Judge actual foot/hand/prop behavior; if a row fails, record the precise defect and repair only that row. Do not silently replace a required true gait with one-pose glide/bob; procedural loops remain available when explicitly suitable to the requested effect.
