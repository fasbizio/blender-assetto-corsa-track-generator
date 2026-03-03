"""Shared Catmull-Rom spline utilities for centerline interpolation."""

import json
import math
import os


def catmull_rom_point(p0, p1, p2, p3, t, alpha=0.5):
    """Centripetal Catmull-Rom interpolation between p1 and p2."""
    def d(a, b):
        return max(((a[0]-b[0])**2 + (a[1]-b[1])**2)**0.5, 1e-6)
    t01 = d(p0, p1) ** alpha
    t12 = d(p1, p2) ** alpha
    t23 = d(p2, p3) ** alpha
    m1 = [0, 0]; m2 = [0, 0]
    for i in range(2):
        m1[i] = (p2[i]-p1[i] + t12*((p1[i]-p0[i])/t01 - (p2[i]-p0[i])/(t01+t12)))
        m2[i] = (p2[i]-p1[i] + t12*((p3[i]-p2[i])/t23 - (p3[i]-p1[i])/(t12+t23)))
    a = [2*(p1[i]-p2[i])+m1[i]+m2[i] for i in range(2)]
    b = [-3*(p1[i]-p2[i])-2*m1[i]-m2[i] for i in range(2)]
    return tuple(a[i]*t**3 + b[i]*t**2 + m1[i]*t + p1[i] for i in range(2))


def interpolate_centerline(ctrl, pts_per_seg=20):
    """Interpolate closed control-point loop with Catmull-Rom."""
    pts = list(ctrl)
    if len(pts) >= 2:
        d = ((pts[0][0]-pts[-1][0])**2 + (pts[0][1]-pts[-1][1])**2)**0.5
        if d < 1.0:
            pts = pts[:-1]
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i-1)%n], pts[i], pts[(i+1)%n], pts[(i+2)%n]
        for j in range(pts_per_seg):
            out.append(catmull_rom_point(p0, p1, p2, p3, j/pts_per_seg))
    return out


def interpolate_open(ctrl, pts_per_seg=20):
    """Catmull-Rom for open polylines (no wrap-around).

    Phantom points at endpoints by reflecting the first/last segment.
    """
    n = len(ctrl)
    if n < 2:
        return list(ctrl)
    # Phantom endpoints
    p_start = [2 * ctrl[0][i] - ctrl[1][i] for i in range(2)]
    p_end = [2 * ctrl[-1][i] - ctrl[-2][i] for i in range(2)]
    pts = [p_start] + list(ctrl) + [p_end]
    out = []
    for i in range(1, len(pts) - 2):
        segs = pts_per_seg if i < len(pts) - 3 else pts_per_seg + 1
        for j in range(segs):
            out.append(catmull_rom_point(pts[i - 1], pts[i], pts[i + 1], pts[i + 2], j / pts_per_seg))
    return out


def resample_at_distance(cl, spacing=2.0):
    """Resample a closed polyline at fixed arc-length spacing."""
    ds = [0.0]
    for i in range(1, len(cl)):
        dx = cl[i][0] - cl[i - 1][0]
        dy = cl[i][1] - cl[i - 1][1]
        ds.append(ds[-1] + math.hypot(dx, dy))
    total = ds[-1]
    dx = cl[0][0] - cl[-1][0]
    dy = cl[0][1] - cl[-1][1]
    close_d = math.hypot(dx, dy)
    total += close_d

    n_pts = max(10, int(total / spacing))
    step = total / n_pts

    full_pts = list(cl) + [cl[0]]
    full_ds = list(ds) + [ds[-1] + close_d]

    out = []
    seg_idx = 0
    for i in range(n_pts):
        target = i * step
        while seg_idx < len(full_ds) - 2 and full_ds[seg_idx + 1] < target:
            seg_idx += 1
        seg_len = full_ds[seg_idx + 1] - full_ds[seg_idx]
        t = 0.0 if seg_len < 1e-9 else (target - full_ds[seg_idx]) / seg_len
        x = full_pts[seg_idx][0] + t * (full_pts[seg_idx + 1][0] - full_pts[seg_idx][0])
        y = full_pts[seg_idx][1] + t * (full_pts[seg_idx + 1][1] - full_pts[seg_idx][1])
        out.append((x, y))
    return out


def interpolate_layer_elevation(ctrl_points, ctrl_elev, spline_len, pts_per_seg, closed):
    """Map elevation from control points to interpolated spline points.

    Uses the segment structure of Catmull-Rom interpolation:
    spline point j belongs to segment (j // pts_per_seg).
    Handles the deduplication that interpolate_centerline() applies
    to closed curves (removes last point if < 1.0m from first).
    """
    n = len(ctrl_points)
    elev = list(ctrl_elev) if ctrl_elev else [0.0] * n

    # Match interpolate_centerline deduplication
    if closed and n > 1:
        dx = ctrl_points[-1][0] - ctrl_points[0][0]
        dy = ctrl_points[-1][1] - ctrl_points[0][1]
        if math.hypot(dx, dy) < 1.0:
            n -= 1
            elev = elev[:n]

    if not elev or all(z == 0 for z in elev):
        return [0.0] * spline_len

    out = []
    for j in range(spline_len):
        seg = j // pts_per_seg
        t = (j % pts_per_seg) / pts_per_seg
        z0 = elev[seg % n]
        if closed:
            z1 = elev[(seg + 1) % n]
        else:
            z1 = elev[min(seg + 1, n - 1)]
        out.append(z0 + t * (z1 - z0))
    return out


def resample_elevation(interp_elev, interp_pts, resampled_pts):
    """Map elevation from interpolated to resampled curve using ordered scan.

    Both curves follow the same path, so we scan forward through
    interp_pts for each resampled point.
    """
    out = []
    search_start = 0
    n_interp = len(interp_pts)
    for px, py in resampled_pts:
        best_d = float('inf')
        best_z = 0.0
        for offset in range(n_interp):
            i = (search_start + offset) % n_interp
            cx, cy = interp_pts[i]
            d = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_d:
                best_d = d
                best_z = interp_elev[i]
                search_start = i
            elif offset > 50:
                break
        out.append(best_z)
    return out


def load_centerline_v2(filepath):
    """Load centerline.json in v2 format.

    Returns dict: {version, layers, start, map_center}.
    """
    if not os.path.isfile(filepath):
        return {"version": 2, "layers": [], "start": None, "map_center": None}
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("start", None)
    data.setdefault("map_center", None)
    data.setdefault("layers", [])
    return data


def save_centerline_v2(filepath, data):
    """Save centerline.json in v2 format."""
    out = {
        "version": 2,
        "layers": data.get("layers", []),
        "start": data.get("start"),
        "map_center": data.get("map_center"),
        "map_search": data.get("map_search"),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
