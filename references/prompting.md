# Prompting

## Character Bible Template

Use a fixed paragraph like this for every generated sheet:

```text
Create a highly stylized cute cartoon character based on the provided reference photo(s): [age/person description], [face shape], [hair], [key features], [outfit]. Keep the same identity, outfit, body proportion, color palette, and rendering style in every panel. Full body, centered, transparent-friendly plain green background, no text, no labels, no watermark, no frame border.
```

For a child, avoid making the result photorealistic. Ask for a clearly cartoon mascot interpretation:

```text
Make it more cartoon than realistic: big head, rounded face, simplified features, soft 3D storybook rendering, warm colors, playful expression, child-safe cute mascot style.
```

## Sheet Prompts

Front actions:

```text
[character bible]
Create a 3 columns by 2 rows action sprite sheet. Each panel shows the exact same character, same scale, same outfit, same face, full body centered. Panels: idle relaxed smile; wave pose A; wave pose B with the same hand; heart hands; jump pose; custom reaction. Plain green background. No text, no labels, no borders.
```

Side locomotion:

```text
[character bible]
Create a side-view action sprite sheet, character facing right, same scale in every panel. 4 columns by 2 rows. Top row: walk contact, walk down, opposite contact, walk up. Bottom row: run stride, run gather, opposite stride, run gather. Full body centered, plain green background, no text, no labels, no borders.
```

Custom action:

```text
[character bible]
Create a [columns] by [rows] sprite sheet for the action: [action]. The same character appears at the same scale in every panel. Show a coherent sequence from anticipation, action peak, recovery, then return-to-idle. Plain green background. No text, no labels, no borders.
```

## Consistency Tactics

- Reuse the same generated master character image as image reference for every sheet.
- Keep action sheets grouped by camera angle: front-facing for interaction, side-facing for movement.
- Avoid changing clothing in action prompts. If clothing matters, spell it out every time.
- If a sheet includes visible panel dividers, crop with extra gutter and rely on edge artifact cleanup.
