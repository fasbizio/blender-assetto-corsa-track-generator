#!/usr/bin/env python3
"""
Generate fast_lane.ai for Assetto Corsa using one of two centerline methods:

1. Catmull-Rom from centerline.json (if centerline.json exists in TRACK_ROOT)
2. Mesh boundary extraction from 1ROAD (fallback if centerline.json missing)

Run with: blender --background <track>.blend --python scripts/generate_ai_line.py

The .ai file format (little-endian):
  Header (4 x int32): [version=7, numPoints=N, 0, 0]
  Per point (N times): [x,y,z (float32), cumDist (float32), id (int32)]
  Then 4 sections (speed/gas/brake/lateral): [count (int32), N x float32]
"""

import os
import sys
import struct
import json
import math
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.environ.get("TRACK_ROOT", GENERATOR_DIR)
sys.path.insert(0, SCRIPT_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "track_config.json")
DEFAULTS_PATH = os.path.join(GENERATOR_DIR, "defaults.json")
CENTERLINE_PATH = os.path.join(ROOT_DIR, "centerline.json")
ROAD_OBJECT = "1ROAD"

try:
    import bpy
    import bmesh
except ImportError:
    print("ERROR: Must run inside Blender.")
    sys.exit(1)

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
_layouts = _config.get("layouts", {})
_has_reverse = _layouts.get("reverse", False)
_REVERSE = os.environ.get("TRACK_REVERSE", "0") == "1"

# Determine output path based on reverse layout
if _has_reverse:
    OUTPUT_PATH = os.path.join(
        ROOT_DIR, "mod", _slug,
        "reverse" if _REVERSE else "default",
        "ai", "fast_lane.ai"
    )
else:
    OUTPUT_PATH = os.path.join(ROOT_DIR, "mod", _slug, "ai", "fast_lane.ai")

# AI line config (defaults + track config)
_def_ai = _defaults.get("ai_line", {})
_ai = _config.get("ai_line", {})
DEFAULT_SPEED = _ai.get("default_speed", _def_ai.get("default_speed", 80.0))
MIN_CORNER_SPEED = _ai.get("min_corner_speed", _def_ai.get("min_corner_speed", 35.0))
AI_SPACING = _ai.get("spacing", _def_ai.get("spacing", 2.0))

from spline_utils import (
    interpolate_centerline, resample_at_distance,
    interpolate_layer_elevation, resample_elevation,
)

print(f"Config: slug={_slug}, has_reverse={_has_reverse}, _REVERSE={_REVERSE}")
print(f"Output: {OUTPUT_PATH}")


# --- Centerline extraction: Method 1 (Catmull-Rom from centerline.json) ---

def extract_centerline_from_json():
    """Build centerline from centerline.json control points (v2 format)."""
    if not os.path.isfile(CENTERLINE_PATH):
        return None

    with open(CENTERLINE_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # v2 format: extract road layer
    road_layer = next((l for l in data.get("layers", []) if l["type"] == "road"), None)
    if not road_layer or len(road_layer.get("points", [])) < 3:
        print("  Warning: no road layer found in centerline.json")
        return None
    control_points = [tuple(p) for p in road_layer["points"]]
    ctrl_elev = road_layer.get("elevation", [0.0] * len(control_points))
    print(f"  Loaded {len(control_points)} control points from centerline.json")

    # Interpolate with Catmull-Rom (2D) + parallel elevation
    cl_interp = interpolate_centerline(control_points, pts_per_seg=20)
    interp_elev = interpolate_layer_elevation(
        control_points, ctrl_elev, len(cl_interp), 20, True)
    print(f"  Interpolated to {len(cl_interp)} points")

    # Resample at uniform spacing
    cl = resample_at_distance(cl_interp, spacing=AI_SPACING)
    dense_elev = resample_elevation(interp_elev, cl_interp, cl)
    print(f"  Resampled to {len(cl)} points (every {AI_SPACING}m)")

    # Read elevation scale from track config
    _def_elev = _defaults.get("elevation", {})
    _elev_cfg = _config.get("elevation", {})
    elev_scale = _elev_cfg.get("scale", _def_elev.get("scale", 1.0))

    # Convert to numpy 3D with elevation
    centerline = np.array([(x, y, z * elev_scale)
                           for (x, y), z in zip(cl, dense_elev)])
    elev_range = centerline[:, 2].max() - centerline[:, 2].min()
    print(f"  Elevation: scale={elev_scale}, range={elev_range:.1f}m")
    return centerline


# --- Centerline extraction: Method 2 (Mesh boundary from 1ROAD) ---

def _chain_boundary_loops(bm, world_mat):
    """Chain boundary edges into closed loops, returning world-space coords."""
    boundary_edges = [e for e in bm.edges if e.is_boundary]
    print(f"  Found {len(boundary_edges)} boundary edges")
    if not boundary_edges:
        print("ERROR: No boundary edges found on road mesh")
        sys.exit(1)

    edge_set = set(boundary_edges)
    loops = []
    visited = set()

    for start_edge in boundary_edges:
        if start_edge in visited:
            continue
        loop_verts = []
        current = start_edge
        v = current.verts[0]
        while current not in visited:
            visited.add(current)
            co = world_mat @ v.co
            loop_verts.append((co.x, co.y, co.z))
            other = current.other_vert(v)
            next_edge = None
            for e in other.link_edges:
                if e in edge_set and e not in visited:
                    next_edge = e
                    break
            if next_edge is None:
                break
            v = other
            current = next_edge
        if len(loop_verts) > 2:
            loops.append(np.array(loop_verts))

    return loops


def extract_centerline_from_mesh():
    """Extract centerline from 1ROAD mesh boundary edges."""
    obj = bpy.data.objects.get(ROAD_OBJECT)
    if obj is None:
        print(f"ERROR: Object '{ROAD_OBJECT}' not found in scene")
        sys.exit(1)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    loops = _chain_boundary_loops(bm, obj.matrix_world)
    bm.free()

    print(f"  Found {len(loops)} boundary loops: {[len(l) for l in loops]}")
    if len(loops) != 2:
        print(f"ERROR: Expected 2 boundary loops (inner/outer road edge), got {len(loops)}")
        sys.exit(1)

    loop_a, loop_b = loops[0], loops[1]

    if len(loop_a) != len(loop_b):
        print(f"WARNING: Loop sizes differ: {len(loop_a)} vs {len(loop_b)}")
        min_len = min(len(loop_a), len(loop_b))
        loop_a = loop_a[:min_len]
        loop_b = loop_b[:min_len]

    # Align loop_b start to nearest point to loop_a[0]
    dists = np.sum((loop_b - loop_a[0]) ** 2, axis=1)
    offset = int(np.argmin(dists))
    if offset > 0:
        loop_b = np.roll(loop_b, -offset, axis=0)

    # Ensure both loops go in the same direction
    d_fwd = np.sum((loop_a[1] - loop_b[1]) ** 2)
    d_bwd = np.sum((loop_a[1] - loop_b[-1]) ** 2)
    if d_bwd < d_fwd:
        loop_b = loop_b[::-1]
        # Re-align after reversal
        dists = np.sum((loop_b - loop_a[0]) ** 2, axis=1)
        offset = int(np.argmin(dists))
        if offset > 0:
            loop_b = np.roll(loop_b, -offset, axis=0)

    # Centerline = midpoint of corresponding boundary points
    centerline = (loop_a + loop_b) / 2.0
    return centerline


# --- Direction handling (shared by both methods) ---

def handle_centerline_direction(centerline):
    """Determine direction and adjust based on _has_reverse and _REVERSE flags.

    The Blender->AC transform (x,y,z)->(x,z,-y) negates Y, which mirrors
    the path and flips CW<->CCW when viewed from above.

    Default layout (not reversed) = CW in AC -> need CCW in Blender.
    Reverse layout = CCW in AC -> need CW in Blender.
    """
    # Determine current direction: check if centerline goes clockwise (viewed from +Z)
    pts = centerline[:, :2]
    signed_area = np.sum(pts[:-1, 0] * pts[1:, 1] - pts[1:, 0] * pts[:-1, 1])
    direction = "CCW" if signed_area > 0 else "CW"
    print(f"  Centerline: {len(centerline)} points, direction: {direction}")

    if _has_reverse:
        if _REVERSE:
            # Reverse layout: need CW in Blender (-> CCW in AC)
            if signed_area > 0:  # currently CCW in Blender
                centerline = centerline[::-1]
                print(f"  Reversed to CW in Blender (-> CCW in AC)")
        else:
            # Default layout: need CCW in Blender (-> CW in AC)
            if signed_area < 0:  # currently CW in Blender
                centerline = centerline[::-1]
                print(f"  Reversed to CCW in Blender (-> CW in AC)")
    else:
        # No reverse layout: always ensure CW in Blender (-> CW in AC)
        if signed_area > 0:  # currently CCW in Blender
            centerline = centerline[::-1]
            print(f"  Reversed to CW in Blender")

    return centerline


# --- Curvature and speed computation ---

def compute_curvature(pts_2d):
    """Compute curvature at each point of the 2D path."""
    n = len(pts_2d)
    curvature = np.zeros(n)
    for i in range(n):
        p0 = pts_2d[(i - 1) % n]
        p1 = pts_2d[i]
        p2 = pts_2d[(i + 1) % n]
        v1 = p1 - p0
        v2 = p2 - p1
        cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
        l1 = np.linalg.norm(v1)
        l2 = np.linalg.norm(v2)
        if l1 > 0 and l2 > 0:
            curvature[i] = cross / (l1 * l2)
    return curvature


def compute_speeds(curvature):
    """Compute target speed at each point based on curvature."""
    max_curv = np.percentile(curvature, 95) if np.max(curvature) > 0 else 1.0
    norm_curv = np.clip(curvature / max_curv, 0, 1)
    speeds = DEFAULT_SPEED - norm_curv * (DEFAULT_SPEED - MIN_CORNER_SPEED)
    kernel_size = 15
    kernel = np.ones(kernel_size) / kernel_size
    speeds = np.convolve(speeds, kernel, mode='same')
    return speeds


# --- Start index and AI file writing ---

def find_start_index(pts_blender):
    """Find centerline index closest to AC_START_0 position in Blender."""
    start_obj = bpy.data.objects.get("AC_START_0")
    if start_obj is None:
        print("  WARNING: AC_START_0 not found, using index 0")
        return 0
    pos = start_obj.matrix_world.translation
    dists = (pts_blender[:, 0] - pos.x) ** 2 + (pts_blender[:, 1] - pos.y) ** 2
    idx = int(np.argmin(dists))
    print(f"  Start/finish at AC_START_0: Blender({pos.x:.1f}, {pos.y:.1f}), index {idx}")
    return idx


def write_ai_file(centerline_blender, output_path):
    """Write fast_lane.ai binary file from Blender-coords centerline."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Reindex to start from start/finish line
    start_idx = find_start_index(centerline_blender)
    if start_idx > 0:
        centerline_blender = np.roll(centerline_blender, -start_idx, axis=0)
        print(f"  Reindexed AI line to start from index {start_idx}")

    n = len(centerline_blender)

    # Convert Blender (x,y,z) -> AC (x, z, -y)
    ac_pts = np.zeros((n, 3), dtype=np.float32)
    ac_pts[:, 0] = centerline_blender[:, 0]
    ac_pts[:, 1] = centerline_blender[:, 2]
    ac_pts[:, 2] = -centerline_blender[:, 1]

    # Cumulative distances
    diffs = np.diff(ac_pts, axis=0)
    seg_lengths = np.linalg.norm(diffs, axis=1)
    cum_dist = np.zeros(n, dtype=np.float32)
    cum_dist[1:] = np.cumsum(seg_lengths)

    # Curvature and speeds (Blender XY plane)
    curvature = compute_curvature(centerline_blender[:, :2])
    speeds = compute_speeds(curvature)

    # Gas/brake
    max_speed = np.max(speeds)
    gas = (speeds / max_speed).astype(np.float32)
    brake = np.clip(1.0 - gas, 0, 1).astype(np.float32) * 0.3
    lateral = np.zeros(n, dtype=np.float32)

    with open(output_path, 'wb') as f:
        # Header
        f.write(struct.pack('<4i', 7, n, 0, 0))

        # Point data
        for i in range(n):
            f.write(struct.pack('<3f', ac_pts[i, 0], ac_pts[i, 1], ac_pts[i, 2]))
            f.write(struct.pack('<f', cum_dist[i]))
            f.write(struct.pack('<i', i))

        # Speed section (km/h -> m/s)
        f.write(struct.pack('<i', n))
        for i in range(n):
            f.write(struct.pack('<f', speeds[i] / 3.6))

        # Gas section
        f.write(struct.pack('<i', n))
        for i in range(n):
            f.write(struct.pack('<f', gas[i]))

        # Brake section
        f.write(struct.pack('<i', n))
        for i in range(n):
            f.write(struct.pack('<f', brake[i]))

        # Lateral offset section
        f.write(struct.pack('<i', n))
        for i in range(n):
            f.write(struct.pack('<f', lateral[i]))

    total_length = cum_dist[-1]
    print(f"  Written {n} AI points to {output_path}")
    print(f"  Total AI line length: {total_length:.1f} m")
    print(f"  Speed range: {speeds.min():.1f} - {speeds.max():.1f} km/h")


def main():
    """Auto-detect centerline method and generate AI file."""
    print("Generating AI driving line...")

    # Auto-detect method: check if centerline.json exists
    centerline = None
    if os.path.isfile(CENTERLINE_PATH):
        print("Extracting centerline from centerline.json (Catmull-Rom method)...")
        centerline = extract_centerline_from_json()
    else:
        print("Extracting centerline from 1ROAD mesh (boundary method)...")
        centerline = extract_centerline_from_mesh()

    if centerline is None:
        print("ERROR: Failed to extract centerline")
        sys.exit(1)

    # Handle direction based on reverse layout
    centerline = handle_centerline_direction(centerline)

    print("Writing AI file...")
    write_ai_file(centerline, OUTPUT_PATH)
    print("\nDone!")


if __name__ == "__main__":
    main()
