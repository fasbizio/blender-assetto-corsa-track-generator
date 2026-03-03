#!/usr/bin/env python3
"""
Create reverse variant of a track .blend file.

Opens the CW blend, flips all AC_ empties to face CCW direction,
and saves as {slug}_reverse.blend. The 3D mesh is unchanged.

Run with:
  blender --background <track>.blend --python scripts/create_reverse_blend.py
"""

import os
import sys
import math
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.environ.get("TRACK_ROOT", GENERATOR_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "track_config.json")

_config = {}
if os.path.isfile(CONFIG_PATH):
    with open(CONFIG_PATH, encoding="utf-8") as _f:
        _config = json.load(_f)
_slug = _config.get("slug", "track")

REVERSE_BLEND = os.path.join(ROOT_DIR, f"{_slug}_reverse.blend")

try:
    import bpy
    from mathutils import Euler
except ImportError:
    print("ERROR: Must run inside Blender.")
    sys.exit(1)


def main():
    print("=" * 60)
    print(f"{_slug} — Reverse Empties Generator")
    print("=" * 60)

    count = 0
    for obj in bpy.data.objects:
        if obj.type == 'EMPTY' and obj.name.startswith('AC_'):
            rot = obj.rotation_euler.copy()
            rot.z += math.pi
            obj.rotation_euler = rot
            count += 1
            print(f"  Flipped: {obj.name}")

    print(f"\n  {count} empties flipped to CCW")
    print(f"Saving {REVERSE_BLEND}...")
    bpy.ops.wm.save_as_mainfile(filepath=REVERSE_BLEND)
    print("Done!")


if __name__ == "__main__":
    main()
