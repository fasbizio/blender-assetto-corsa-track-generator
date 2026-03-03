#!/usr/bin/env python3
"""
Create the complete Assetto Corsa mod folder structure and config files.

Uses the AC multi-layout convention (like ks_brands_hatch) when reverse=true:
  - models_<layout>.ini in track root
  - <layout>/ai/, <layout>/data/
  - ui/<layout>/ui_track.json, preview.png, outline.png

Single layout mode (reverse=false):
  - ai/, data/, ui/ directly in mod root
  - No models.ini needed (AC auto-detects KN5 by folder name)
"""

import math
import os
import json
import shutil

from PIL import Image, ImageDraw
from spline_utils import load_centerline_v2, interpolate_centerline

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.environ.get("TRACK_ROOT", GENERATOR_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "track_config.json")
DEFAULTS_PATH = os.path.join(GENERATOR_DIR, "defaults.json")

# Load defaults from generator project
_defaults = {}
if os.path.isfile(DEFAULTS_PATH):
    with open(DEFAULTS_PATH, encoding="utf-8") as _df:
        _defaults = json.load(_df)

# Load track config
_config = {}
if os.path.isfile(CONFIG_PATH):
    with open(CONFIG_PATH, encoding="utf-8") as _f:
        _config = json.load(_f)

_slug = _config.get("slug", "track")
_has_reverse = _config.get("layouts", {}).get("reverse", False)
MOD_DIR = os.path.join(ROOT_DIR, "mod", _slug)

# Merge defaults + track surfaces (track config wins)
_def_surfaces = _defaults.get("surfaces", {})
_surfaces = _config.get("surfaces", {})
_merged_surfaces = {**_def_surfaces, **_surfaces}
_info = _config.get("info", {})


def create_directories():
    """Create the mod folder structure (multi-layout or single-layout)."""
    if _has_reverse:
        # Multi-layout structure (default + reverse sub-layouts)
        dirs = [
            os.path.join(MOD_DIR, "default", "ai"),
            os.path.join(MOD_DIR, "default", "data"),
            os.path.join(MOD_DIR, "reverse", "ai"),
            os.path.join(MOD_DIR, "reverse", "data"),
            os.path.join(MOD_DIR, "ui", "default"),
            os.path.join(MOD_DIR, "ui", "reverse"),
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        print("  Created directory structure (default + reverse sub-layouts)")
    else:
        # Single-layout structure (flat)
        dirs = [
            os.path.join(MOD_DIR, "ai"),
            os.path.join(MOD_DIR, "data"),
            os.path.join(MOD_DIR, "ui"),
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        print("  Created directory structure (single layout)")


def _write_data_file(layout, filename, content):
    """Write a data file to <layout>/data/<filename> or data/<filename>."""
    if _has_reverse:
        path = os.path.join(MOD_DIR, layout, "data", filename)
    else:
        path = os.path.join(MOD_DIR, "data", filename)
    with open(path, 'w', encoding="utf-8", newline="\r\n") as f:
        f.write(content)
    return path


def write_surfaces_ini():
    """Write surfaces.ini — surface physics definitions."""
    road_friction = _merged_surfaces.get("road_friction", 0.97)
    kerb_friction = _merged_surfaces.get("kerb_friction", 0.93)
    grass_friction = _merged_surfaces.get("grass_friction", 0.60)

    # NOTE: No WALL surface entry. AC treats unmatched meshes as solid barriers
    # by default — defining KEY=WALL would override this and make walls driveable
    # surfaces instead of collision barriers. Kunos tracks (e.g. ks_laguna_seca)
    # also omit WALL from surfaces.ini.
    content = f"""\
[SURFACE_0]
KEY=ROAD
FRICTION={road_friction}
DAMPING=0.0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0.0
IS_VALID_TRACK=1
IS_PITLANE=0
BLACK_FLAG_TIME=0.0
SIN_HEIGHT=0
SIN_LENGTH=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[SURFACE_1]
KEY=KERB
FRICTION={kerb_friction}
DAMPING=0.0
WAV=kerb
WAV_PITCH=1
FF_EFFECT=KERB
DIRT_ADDITIVE=0.0
IS_VALID_TRACK=1
IS_PITLANE=0
BLACK_FLAG_TIME=0.0
SIN_HEIGHT=0.005
SIN_LENGTH=0.15
VIBRATION_GAIN=0.5
VIBRATION_LENGTH=0.15

[SURFACE_2]
KEY=GRASS
FRICTION={grass_friction}
DAMPING=0.1
WAV=grass
WAV_PITCH=0
FF_EFFECT=GRASS
DIRT_ADDITIVE=0.5
IS_VALID_TRACK=0
IS_PITLANE=0
BLACK_FLAG_TIME=3.0
SIN_HEIGHT=0
SIN_LENGTH=0
VIBRATION_GAIN=0.2
VIBRATION_LENGTH=0.5

[SURFACE_3]
KEY=PIT
FRICTION=0.97
DAMPING=0.0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0.0
IS_VALID_TRACK=1
IS_PITLANE=1
BLACK_FLAG_TIME=0.0
SIN_HEIGHT=0
SIN_LENGTH=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[SURFACE_4]
KEY=GROUND
FRICTION={grass_friction}
DAMPING=0.15
WAV=grass
WAV_PITCH=0
FF_EFFECT=GRASS
DIRT_ADDITIVE=0.5
IS_VALID_TRACK=0
IS_PITLANE=0
BLACK_FLAG_TIME=3.0
SIN_HEIGHT=0
SIN_LENGTH=0
VIBRATION_GAIN=0.3
VIBRATION_LENGTH=0.5
"""
    if _has_reverse:
        for layout in ("default", "reverse"):
            p = _write_data_file(layout, "surfaces.ini", content)
            print(f"  Written {p}")
    else:
        p = _write_data_file(None, "surfaces.ini", content)
        print(f"  Written {p}")


def write_cameras_ini():
    """Write cameras.ini — 6 replay cameras around the circuit."""
    content = """\
[HEADER]
VERSION=2
CAMERA_COUNT=6
SET_NAME=replay


[CAMERA_0]
NAME=Start/Finish
POSITION=5.0, 3.0, 0.0
FORWARD=0.0, -0.3, 1.0
FOV=56.0
NEAR=0.1
FAR=800.0
MIN_DISTANCE=3.0
MAX_DISTANCE=120.0

[CAMERA_1]
NAME=Curva 1
POSITION=-30.0, 4.0, 50.0
FORWARD=0.5, -0.3, -0.5
FOV=50.0
NEAR=0.1
FAR=800.0
MIN_DISTANCE=3.0
MAX_DISTANCE=100.0

[CAMERA_2]
NAME=Tornante Nord
POSITION=-50.0, 5.0, 100.0
FORWARD=0.7, -0.3, -0.3
FOV=48.0
NEAR=0.1
FAR=800.0
MIN_DISTANCE=3.0
MAX_DISTANCE=100.0

[CAMERA_3]
NAME=Chicane
POSITION=20.0, 3.5, 80.0
FORWARD=-0.5, -0.2, -0.5
FOV=52.0
NEAR=0.1
FAR=800.0
MIN_DISTANCE=3.0
MAX_DISTANCE=100.0

[CAMERA_4]
NAME=Curva Sud
POSITION=40.0, 4.0, -20.0
FORWARD=-0.6, -0.3, 0.4
FOV=50.0
NEAR=0.1
FAR=800.0
MIN_DISTANCE=3.0
MAX_DISTANCE=100.0

[CAMERA_5]
NAME=Panoramica
POSITION=0.0, 25.0, 50.0
FORWARD=0.0, -0.8, -0.2
FOV=70.0
NEAR=0.1
FAR=1200.0
MIN_DISTANCE=5.0
MAX_DISTANCE=200.0
"""
    if _has_reverse:
        for layout in ("default", "reverse"):
            p = _write_data_file(layout, "cameras.ini", content)
            print(f"  Written {p}")
    else:
        p = _write_data_file(None, "cameras.ini", content)
        print(f"  Written {p}")



def write_lighting_ini():
    """Write lighting.ini — sun position."""
    content = """\
[LIGHTING]
SUN_PITCH_ANGLE=45
SUN_HEADING_ANGLE=45
"""
    if _has_reverse:
        for layout in ("default", "reverse"):
            p = _write_data_file(layout, "lighting.ini", content)
            print(f"  Written {p}")
    else:
        p = _write_data_file(None, "lighting.ini", content)
        print(f"  Written {p}")


def write_groove_ini():
    """Write groove.ini — rubber groove config."""
    content = """\
[HEADER]
GROOVES_NUMBER=0
"""
    if _has_reverse:
        for layout in ("default", "reverse"):
            p = _write_data_file(layout, "groove.ini", content)
            print(f"  Written {p}")
    else:
        p = _write_data_file(None, "groove.ini", content)
        print(f"  Written {p}")


def write_models_ini():
    """Write models_<layout>.ini in track root (only for multi-layout).

    The KN5 references are swapped: the Blender→AC coordinate transform
    (x,y,z)→(x,z,-y) flips the apparent rotation direction viewed from above.
    The master .blend empties (CW in Blender) become CCW in AC, and the
    reverse .blend empties (CCW in Blender) become CW in AC.
    So: default (CW in AC) → reverse KN5, reverse (CCW in AC) → default KN5.
    """
    if not _has_reverse:
        # Single-layout: no models.ini needed
        return

    # Default layout (CW in AC) → uses reverse KN5 (empties flipped = CW in AC)
    path_def = os.path.join(MOD_DIR, "models_default.ini")
    with open(path_def, 'w', encoding="utf-8", newline="\r\n") as f:
        f.write(f"[MODEL_0]\nFILE={_slug}_reverse.kn5\nPOSITION=0,0,0\nROTATION=0,0,0\n")
    print(f"  Written {path_def}")

    # Reverse layout (CCW in AC) → uses default KN5 (master empties = CCW in AC)
    path_rev = os.path.join(MOD_DIR, "models_reverse.ini")
    with open(path_rev, 'w', encoding="utf-8", newline="\r\n") as f:
        f.write(f"[MODEL_0]\nFILE={_slug}.kn5\nPOSITION=0,0,0\nROTATION=0,0,0\n")
    print(f"  Written {path_rev}")


def write_ui_track_json():
    """Write ui_track.json for layouts."""
    name = _info.get("name", _slug.capitalize())
    city = _info.get("city", "")
    province = _info.get("province", "")
    region = _info.get("region", "")
    country = _info.get("country", "")
    length = str(_info.get("length", "1000"))
    pitboxes = str(_info.get("pitboxes", "5"))
    direction = _info.get("direction", "clockwise")
    geotags = _info.get("geotags", ["0.0", "0.0"])
    _geo = _config.get("geometry", {})
    road_w = _geo.get("road_width", 8.0)

    def _make_ui_json(is_reverse=False):
        """Generate ui_track.json data."""
        run = "counter-clockwise" if is_reverse else direction
        suffix = " [reverse]" if is_reverse else ""
        reverse_tag = ["reverse"] if is_reverse else []

        return {
            "name": f"{name}{suffix}",
            "description": f"{name} - {city} ({province}), "
                          f"{region} - {country}. Tracciato su {length} metri."
                          f"{' Senso antiorario.' if is_reverse else ''}",
            "tags": ["circuit", "kart", "italy", "short"] + reverse_tag,
            "geotags": geotags,
            "country": country,
            "city": city,
            "length": length,
            "width": f"{road_w:.0f}-{road_w + 1:.0f}",
            "pitboxes": pitboxes,
            "run": run,
            "author": "Track Generator",
            "version": "2.0.0"
        }

    if _has_reverse:
        # Default layout
        path_def = os.path.join(MOD_DIR, "ui", "default", "ui_track.json")
        with open(path_def, 'w', encoding="utf-8") as f:
            json.dump(_make_ui_json(is_reverse=False), f, indent=2, ensure_ascii=False)
        print(f"  Written {path_def}")

        # Reverse layout
        path_rev = os.path.join(MOD_DIR, "ui", "reverse", "ui_track.json")
        with open(path_rev, 'w', encoding="utf-8") as f:
            json.dump(_make_ui_json(is_reverse=True), f, indent=2, ensure_ascii=False)
        print(f"  Written {path_rev}")
    else:
        # Single layout
        path = os.path.join(MOD_DIR, "ui", "ui_track.json")
        with open(path, 'w', encoding="utf-8") as f:
            json.dump(_make_ui_json(is_reverse=False), f, indent=2, ensure_ascii=False)
        print(f"  Written {path}")


def generate_outline(dst_path):
    """Generate outline.png from centerline.json — white road shape on transparent bg."""
    _geo = _config.get("geometry", {})
    road_width = _geo.get("road_width", _defaults.get("geometry", {}).get("road_width", 8.0))

    cl_path = os.path.join(ROOT_DIR, "centerline.json")
    data = load_centerline_v2(cl_path)
    road_layer = None
    for layer in data.get("layers", []):
        if layer.get("type") == "road":
            road_layer = layer
            break
    if not road_layer or len(road_layer.get("points", [])) < 3:
        return False

    ctrl = [(p[0], p[1]) for p in road_layer["points"]]
    dense = interpolate_centerline(ctrl, pts_per_seg=40)

    # Compute normals and offset edges
    n = len(dense)
    left = []
    right = []
    hw = road_width / 2.0
    for i in range(n):
        x0, y0 = dense[i]
        x1, y1 = dense[(i + 1) % n]
        tx, ty = x1 - x0, y1 - y0
        ln = math.hypot(tx, ty)
        if ln < 1e-9:
            continue
        nx, ny = -ty / ln, tx / ln
        left.append((x0 + nx * hw, y0 + ny * hw))
        right.append((x0 - nx * hw, y0 - ny * hw))

    # Build closed polygon: left edge + reversed right edge
    polygon = left + right[::-1]

    # Fit to 1024x1024 with margin
    size = 1024
    margin = 60
    all_x = [p[0] for p in polygon]
    all_y = [p[1] for p in polygon]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    extent_x = max_x - min_x or 1.0
    extent_y = max_y - min_y or 1.0
    scale = (size - 2 * margin) / max(extent_x, extent_y)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0

    def to_px(x, y):
        px = (x - cx) * scale + size / 2.0
        py = (y - cy) * scale + size / 2.0
        return (px, py)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    px_poly = [to_px(x, y) for x, y in polygon]
    draw.polygon(px_poly, fill=(255, 255, 255, 255))

    img.save(dst_path, "PNG")
    return True


def copy_images():
    """Copy cover/preview images and generate outline + map from centerline."""
    # Cover.png as preview (track project -> generator default)
    cover = os.path.join(ROOT_DIR, "cover.png")
    generator_cover = os.path.join(GENERATOR_DIR, "cover.png")
    if os.path.exists(cover):
        preview_src = cover
        label = "cover.png"
    elif os.path.exists(generator_cover):
        preview_src = generator_cover
        label = "cover.png (default)"
    else:
        preview_src = None
        label = ""
    if preview_src:
        if _has_reverse:
            for layout in ("default", "reverse"):
                dst = os.path.join(MOD_DIR, "ui", layout, "preview.png")
                shutil.copy2(preview_src, dst)
            print(f"  Copied {label} -> ui/default/preview.png + ui/reverse/preview.png")
        else:
            dst = os.path.join(MOD_DIR, "ui", "preview.png")
            shutil.copy2(preview_src, dst)
            print(f"  Copied {label} -> ui/preview.png")

    # Generate outline.png from centerline
    root_outline = os.path.join(ROOT_DIR, "outline.png")
    if _has_reverse:
        dst = os.path.join(MOD_DIR, "ui", "default", "outline.png")
        if generate_outline(dst):
            shutil.copy2(dst, os.path.join(MOD_DIR, "ui", "reverse", "outline.png"))
            shutil.copy2(dst, root_outline)
            print(f"  Generated outline.png from centerline -> ui/default/ + ui/reverse/ + root")
    else:
        dst = os.path.join(MOD_DIR, "ui", "outline.png")
        if generate_outline(dst):
            shutil.copy2(dst, root_outline)
            print(f"  Generated outline.png from centerline -> ui/ + root")


def main():
    print("Setting up Assetto Corsa mod folder...")
    print(f"  Mode: {'Multi-layout (default + reverse)' if _has_reverse else 'Single layout'}")

    create_directories()
    write_surfaces_ini()
    write_cameras_ini()
    write_lighting_ini()
    write_groove_ini()
    write_models_ini()
    write_ui_track_json()
    copy_images()

    print(f"\nMod structure created at: {MOD_DIR}")
    if _has_reverse:
        print(f"  Layouts: default (CW) + reverse (CCW)")
    else:
        print(f"  Layout: single (direction from config)")


if __name__ == "__main__":
    main()
