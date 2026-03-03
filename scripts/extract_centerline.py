#!/usr/bin/env python3
"""
Extract track centerline from layout.svg.

Parses the SVG path data (outer + inner contour of the track ribbon),
samples points along both contours using cubic Bezier evaluation,
computes the midpoint between corresponding points as the centerline,
scales to the real-world track length, and saves to centerline.json.

Run with: python scripts/extract_centerline.py
"""

import os
import re
import json
import math

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.environ.get("TRACK_ROOT", GENERATOR_DIR)
SVG_PATH = os.path.join(ROOT_DIR, "layout.svg")
OUTPUT_PATH = os.path.join(ROOT_DIR, "centerline.json")
CONFIG_PATH = os.path.join(ROOT_DIR, "track_config.json")

TARGET_LENGTH = 1000.0
NUM_CONTROL_POINTS = 80


def cubic_bezier(p0, p1, p2, p3, t):
    """Evaluate cubic Bezier curve at parameter t in [0, 1]."""
    u = 1 - t
    return (
        u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
    )


def parse_svg_path(d_attr):
    """Parse SVG path d attribute, returning list of sub-paths as point lists."""
    tokens = re.findall(r'[MmCcLlHhVvZzSsQqTtAa]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', d_attr)

    sub_paths = []
    current_path = []
    cx, cy = 0.0, 0.0
    i = 0

    while i < len(tokens):
        cmd = tokens[i]
        i += 1

        if cmd == 'M':
            if current_path:
                sub_paths.append(current_path)
            cx = float(tokens[i]); cy = float(tokens[i + 1])
            i += 2
            current_path = [(cx, cy)]
            while i < len(tokens) and tokens[i] not in 'MmCcLlHhVvZzSsQqTtAa':
                cx = float(tokens[i]); cy = float(tokens[i + 1])
                i += 2
                current_path.append((cx, cy))

        elif cmd == 'C':
            while i + 5 < len(tokens) and tokens[i] not in 'MmCcLlHhVvZzSsQqTtAa':
                x1 = float(tokens[i]); y1 = float(tokens[i + 1])
                x2 = float(tokens[i + 2]); y2 = float(tokens[i + 3])
                x3 = float(tokens[i + 4]); y3 = float(tokens[i + 5])
                i += 6
                p0 = (cx, cy)
                p1 = (x1, y1)
                p2 = (x2, y2)
                p3 = (x3, y3)
                for t_idx in range(1, 11):
                    t = t_idx / 10.0
                    current_path.append(cubic_bezier(p0, p1, p2, p3, t))
                cx, cy = x3, y3

        elif cmd == 'Z' or cmd == 'z':
            if current_path:
                sub_paths.append(current_path)
                current_path = []

        else:
            i += 1

    if current_path:
        sub_paths.append(current_path)

    return sub_paths


def apply_transform(points, tx, ty):
    """Apply SVG translate transform."""
    return [(x + tx, y + ty) for x, y in points]


def path_length(pts):
    """Total arc length of a polyline."""
    total = 0.0
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def resample_by_arclength(pts, n):
    """Resample a polyline to n equally-spaced points by arc length."""
    dists = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        dists.append(dists[-1] + math.sqrt(dx * dx + dy * dy))
    total = dists[-1]
    if total < 1e-6:
        return pts[:n]

    out = []
    seg = 0
    for k in range(n):
        target = k / n * total
        while seg < len(dists) - 2 and dists[seg + 1] < target:
            seg += 1
        t = (target - dists[seg]) / max(dists[seg + 1] - dists[seg], 1e-9)
        x = pts[seg][0] + t * (pts[seg + 1][0] - pts[seg][0])
        y = pts[seg][1] + t * (pts[seg + 1][1] - pts[seg][1])
        out.append((x, y))
    return out


def nearest_point_on_contour(pt, contour):
    """Find nearest point on contour to pt."""
    best_d = float('inf')
    best_pt = contour[0]
    for c in contour:
        d = (c[0] - pt[0])**2 + (c[1] - pt[1])**2
        if d < best_d:
            best_d = d
            best_pt = c
    return best_pt


def compute_centerline(outer, inner, n_samples=500):
    """Compute centerline as midpoint between outer and inner contours."""
    outer_r = resample_by_arclength(outer, n_samples)
    inner_r = resample_by_arclength(inner, n_samples)

    centerline = []
    for pt in outer_r:
        nearest = nearest_point_on_contour(pt, inner_r)
        mx = (pt[0] + nearest[0]) / 2
        my = (pt[1] + nearest[1]) / 2
        centerline.append((mx, my))

    return centerline


def smooth_centerline(pts, iterations=3):
    """Simple moving-average smoothing (closed loop)."""
    n = len(pts)
    result = list(pts)
    for _ in range(iterations):
        new = []
        for i in range(n):
            px = (result[(i - 1) % n][0] + result[i][0] + result[(i + 1) % n][0]) / 3
            py = (result[(i - 1) % n][1] + result[i][1] + result[(i + 1) % n][1]) / 3
            new.append((px, py))
        result = new
    return result


def signed_area(pts):
    """Compute signed area (positive = CCW, negative = CW)."""
    area = 0.0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return area / 2.0


def main():
    print("=" * 60)
    print("Track — Centerline Extractor")
    print("=" * 60)

    target_len = TARGET_LENGTH
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        info = cfg.get("info", {})
        if "length" in info:
            target_len = float(info["length"])
            print(f"Target length from config: {target_len} m")

    print(f"\nReading {SVG_PATH}...")
    with open(SVG_PATH, encoding="utf-8") as f:
        svg = f.read()

    pattern = r'<path d="([^"]+)"[^>]*fill="#080808"'
    match = re.search(pattern, svg)
    if not match:
        paths = re.findall(r'<path d="([^"]+)"', svg)
        if len(paths) >= 3:
            d_attr = paths[2]
        else:
            print("ERROR: Could not find track path in SVG")
            return
    else:
        d_attr = match.group(1)

    t_match = re.search(r'translate\(([-\d.]+),([-\d.]+)\)', svg.split('#080808')[0].split('<path')[-1] if '#080808' in svg else svg)
    tx, ty = 0.0, 0.0
    if t_match:
        tx = float(t_match.group(1))
        ty = float(t_match.group(2))
    path_section = svg.split('#080808')[0].split('<path')[-1] if '#080808' in svg else ''
    t_match2 = re.search(r'translate\(([-\d.]+),([-\d.]+)\)', path_section)
    if not t_match2:
        for m in re.finditer(r'<path[^>]*fill="#080808"[^>]*>', svg):
            t_match2 = re.search(r'translate\(([-\d.]+),([-\d.]+)\)', m.group())
            if t_match2:
                break
    if t_match2:
        tx = float(t_match2.group(1))
        ty = float(t_match2.group(2))

    print(f"Transform: translate({tx}, {ty})")

    sub_paths = parse_svg_path(d_attr)
    print(f"Found {len(sub_paths)} sub-paths")

    if len(sub_paths) < 2:
        print("ERROR: Expected 2 sub-paths (outer + inner contour)")
        return

    outer = apply_transform(sub_paths[0], tx, ty)
    inner = apply_transform(sub_paths[1], tx, ty)

    print(f"Outer contour: {len(outer)} points, length {path_length(outer):.1f} px")
    print(f"Inner contour: {len(inner)} points, length {path_length(inner):.1f} px")

    print("\nComputing centerline...")
    cl = compute_centerline(outer, inner, n_samples=600)

    cl = smooth_centerline(cl, iterations=5)

    area = signed_area(cl)
    print(f"Signed area: {area:.1f} ({'CW' if area < 0 else 'CCW'})")
    if area > 0:
        cl.reverse()
        print("  Reversed to CW")

    pl = path_length(cl)
    dx = cl[0][0] - cl[-1][0]
    dy = cl[0][1] - cl[-1][1]
    pl += math.sqrt(dx * dx + dy * dy)
    scale = target_len / pl
    print(f"Pixel length: {pl:.1f} px, scale factor: {scale:.4f} m/px")

    cx = sum(p[0] for p in cl) / len(cl)
    cy = sum(p[1] for p in cl) / len(cl)
    cl = [((p[0] - cx) * scale, (p[1] - cy) * scale) for p in cl]

    cl = [(p[0], -p[1]) for p in cl]

    final_len = path_length(cl)
    dx = cl[0][0] - cl[-1][0]
    dy = cl[0][1] - cl[-1][1]
    final_len += math.sqrt(dx * dx + dy * dy)
    print(f"Final length: {final_len:.1f} m")

    cl_sub = resample_by_arclength(cl, NUM_CONTROL_POINTS)
    cl_sub.append(cl_sub[0])

    result = [[round(p[0], 1), round(p[1], 1)] for p in cl_sub]

    # Save in v2 format
    data = {
        "version": 2,
        "layers": [
            {"name": "road", "type": "road", "closed": True, "points": result}
        ],
        "start": None,
        "map_center": None,
    }

    print(f"\nSaving {len(result)} control points to {OUTPUT_PATH} (v2 format)...")
    with open(OUTPUT_PATH, 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Done!")


if __name__ == "__main__":
    main()
