# Manifest

`scripts/build_animation_assets.py` reads a JSON manifest from the chosen output folder.

Minimal structure:

```json
{
  "canvas": [360, 360],
  "target_body_height": 292,
  "sheets": [
    {
      "path": "source/front_actions_sheet.png",
      "cols": 3,
      "rows": 2,
      "gutter": 12,
      "cells": {
        "idle": [0, 0],
        "wave_a": [1, 0],
        "wave_b": [2, 0],
        "heart": [0, 1],
        "jump": [1, 1],
        "celebrate": [2, 1]
      }
    },
    {
      "path": "source/side_locomotion_sheet.png",
      "cols": 4,
      "rows": 2,
      "gutter": 12,
      "cells": {
        "walk_contact": [0, 0],
        "run_stride": [0, 1]
      }
    }
  ],
  "sequences": [
    {"name": "idle_breathe", "type": "breathe", "sprite": "idle", "frames": 36, "duration": 42},
    {"name": "wave_once", "type": "cycle", "sprites": ["idle", "wave_a", "wave_b", "wave_a", "idle"], "duration": 70, "loop": 1},
    {"name": "walk_smooth_right", "type": "smooth_locomotion", "sprite": "walk_contact", "frames": 24, "duration": 70, "amplitude": 3, "tilt": 1.1, "scale": 1.14}
  ]
}
```

Sequence types:

- `breathe`: subtle scale loop from one sprite.
- `cycle`: list of sprites pasted on the same grounded canvas.
- `jump`: one sprite moved through a vertical arc.
- `smooth_locomotion`: one side-view sprite animated with scale, bob, and lean. Set `flip: true` for left-facing movement.

All paths are relative to the manifest file unless absolute.
