#!/usr/bin/env python3
"""
Export Blender scene to Assetto Corsa KN5 format (generalized for any track).

Run with: blender --background <track>.blend --python scripts/export_kn5.py

KN5 format: "sc6969" header, embedded PNG textures, materials, recursive node tree.
Based on format spec from RaduMC/kn5-converter.

Configuration:
  - TRACK_ROOT env var: path to track root directory (defaults to parent of script dir)
  - track_config.json: must contain "slug" field for track identifier
  - TRACK_REVERSE env var: set to "1" to export reverse version
"""

import os
import sys
import struct
import json
import math

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.environ.get("TRACK_ROOT", GENERATOR_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "track_config.json")
_REVERSE = os.environ.get("TRACK_REVERSE", "0") == "1"

try:
    import bpy
    import bmesh
except ImportError:
    print("Must run inside Blender.")
    sys.exit(1)

# Load track configuration
try:
    with open(CONFIG_PATH, 'r', encoding="utf-8") as f:
        _config = json.load(f)
    SLUG = _config.get("slug", "track")
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"Warning: Could not load track_config.json: {e}")
    SLUG = "track"

KN5_PATH = os.path.join(ROOT_DIR, f"{SLUG}_reverse.kn5" if _REVERSE else f"{SLUG}.kn5")


# --- KN5 binary writer helpers ---

def write_string(f, s):
    """Write length-prefixed UTF-8 string."""
    data = s.encode('utf-8')
    f.write(struct.pack('<i', len(data)))
    f.write(data)


def write_header(f, version=6):
    """Write KN5 file header."""
    f.write(b'sc6969')
    f.write(struct.pack('<i', version))
    if version > 5:
        f.write(struct.pack('<i', 0))  # v6 extra field


def write_textures(f, textures):
    """Write texture section. textures = list of (name, dds_bytes)."""
    f.write(struct.pack('<i', len(textures)))
    for name, dds_data in textures:
        f.write(struct.pack('<i', 1))  # texture type (1 = active/embedded)
        write_string(f, name)
        f.write(struct.pack('<i', len(dds_data)))
        f.write(dds_data)


def write_materials(f, materials, version=6):
    """Write material section."""
    f.write(struct.pack('<i', len(materials)))
    for mat in materials:
        write_string(f, mat['name'])
        write_string(f, mat['shader'])
        f.write(struct.pack('<h', 0))

        if version > 4:
            f.write(struct.pack('<i', 0))

        props = mat.get('properties', {})
        f.write(struct.pack('<i', len(props)))
        for pname, pvalue in props.items():
            write_string(f, pname)
            f.write(struct.pack('<f', pvalue))
            f.write(b'\x00' * 36)

        samplers = mat.get('samplers', [])
        f.write(struct.pack('<i', len(samplers)))
        for sname, slot, tname in samplers:
            write_string(f, sname)
            f.write(struct.pack('<i', slot))
            write_string(f, tname)


def write_dummy_node(f, name, matrix, children_count):
    """Write a Type 1 (Dummy) node header + transform."""
    f.write(struct.pack('<i', 1))
    write_string(f, name)
    f.write(struct.pack('<i', children_count))
    f.write(struct.pack('<B', 1))
    for row in range(4):
        for col in range(4):
            f.write(struct.pack('<f', matrix[row][col]))


def make_box_mesh(half=0.25):
    """Create a small box mesh (24 verts, 36 indices) for AC_ empty visualization."""
    h = half
    faces_data = [
        ((0, 1, 0), (1, 0, 0), (-h, h, -h), (h, h, -h), (h, h, h), (-h, h, h)),
        ((0, -1, 0), (1, 0, 0), (-h, -h, h), (h, -h, h), (h, -h, -h), (-h, -h, -h)),
        ((1, 0, 0), (0, 0, 1), (h, -h, -h), (h, -h, h), (h, h, h), (h, h, -h)),
        ((-1, 0, 0), (0, 0, -1), (-h, -h, h), (-h, -h, -h), (-h, h, -h), (-h, h, h)),
        ((0, 0, 1), (1, 0, 0), (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)),
        ((0, 0, -1), (-1, 0, 0), (h, -h, -h), (-h, -h, -h), (-h, h, -h), (h, h, -h)),
    ]
    verts = []
    indices = []
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for normal, tangent, v0, v1, v2, v3 in faces_data:
        base = len(verts)
        for v, uv in zip([v0, v1, v2, v3], uvs):
            verts.append((v, normal, uv, tangent))
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return verts, indices


def compute_bounding_sphere(vertices):
    """Compute bounding sphere (center + radius) from vertex positions."""
    if not vertices:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [v[0][0] for v in vertices]
    ys = [v[0][1] for v in vertices]
    zs = [v[0][2] for v in vertices]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    radius = 0.0
    for v in vertices:
        dx = v[0][0] - cx
        dy = v[0][1] - cy
        dz = v[0][2] - cz
        d = (dx * dx + dy * dy + dz * dz) ** 0.5
        if d > radius:
            radius = d
    return (cx, cy, cz, radius)


def write_mesh_node(f, name, vertices, indices, material_id):
    """Write a Type 2 (Mesh) node with no children."""
    f.write(struct.pack('<i', 2))
    write_string(f, name)
    f.write(struct.pack('<i', 0))
    f.write(struct.pack('<B', 1))
    f.write(struct.pack('<3B', 1, 1, 0))
    f.write(struct.pack('<i', len(vertices)))
    for pos, normal, uv, tangent in vertices:
        f.write(struct.pack('<3f', *pos))
        f.write(struct.pack('<3f', *normal))
        f.write(struct.pack('<2f', *uv))
        f.write(struct.pack('<3f', *tangent))
    f.write(struct.pack('<i', len(indices)))
    for i in range(0, len(indices), 3):
        f.write(struct.pack('<H', indices[i]))
        f.write(struct.pack('<H', indices[i + 1]))
        f.write(struct.pack('<H', indices[i + 2]))
    f.write(struct.pack('<i', material_id))
    cx, cy, cz, radius = compute_bounding_sphere(vertices)
    f.write(struct.pack('<i', 0))
    f.write(struct.pack('<f', 0.0))
    f.write(struct.pack('<f', 0.0))
    f.write(struct.pack('<3f', cx, cy, cz))
    f.write(struct.pack('<f', radius))
    f.write(struct.pack('<B', 1))


# --- Blender scene extraction ---

def get_mesh_data(obj, material_index=None):
    """Extract triangulated mesh data from a Blender object."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    if mesh.uv_layers:
        try:
            mesh.calc_tangents()
        except Exception:
            pass

    uv_layer = mesh.uv_layers.active
    corner_normals = mesh.corner_normals

    vertices = []
    indices = []
    vert_map = {}

    world_mat = obj.matrix_world
    normal_mat = world_mat.to_3x3().inverted_safe().transposed()

    for poly in mesh.polygons:
        if material_index is not None and poly.material_index != material_index:
            continue
        for loop_idx in poly.loop_indices:
            loop = mesh.loops[loop_idx]
            vert = mesh.vertices[loop.vertex_index]

            co = world_mat @ vert.co
            pos = (round(co.x, 6), round(co.z, 6), round(-co.y, 6))
            nv = normal_mat @ corner_normals[loop_idx].vector
            nv.normalize()
            normal = (round(nv.x, 6), round(nv.z, 6), round(-nv.y, 6))

            if uv_layer:
                raw_uv = uv_layer.data[loop_idx].uv
                uv = (round(raw_uv[0], 6), round(1.0 - raw_uv[1], 6))
            else:
                uv = (0.0, 0.0)

            try:
                t = world_mat.to_3x3() @ loop.tangent
                t.normalize()
                tangent = (round(t.x, 6), round(t.z, 6), round(-t.y, 6))
            except Exception:
                tangent = (1.0, 0.0, 0.0)

            key = (pos, normal, uv)
            if key not in vert_map:
                vert_map[key] = len(vertices)
                vertices.append((pos, normal, uv, tangent))

            indices.append(vert_map[key])

    obj_eval.to_mesh_clear()

    if len(vertices) > 65535:
        print(f"  WARNING: {obj.name} has {len(vertices)} verts (>65535), will need splitting")

    return vertices, indices


DEFAULT_SHADER = 'ksPerPixel'
DEFAULT_PROPERTIES = {'ksAmbient': 0.5, 'ksDiffuse': 0.7, 'ksSpecular': 0.2, 'ksSpecularEXP': 15.0}
AC_PROPERTY_NAMES = ['ksAmbient', 'ksDiffuse', 'ksSpecular', 'ksSpecularEXP']


def read_ac_properties(bl_mat):
    """Read AC shader properties from Blender material custom properties."""
    props = {}
    for key in AC_PROPERTY_NAMES:
        props[key] = bl_mat.get(key, DEFAULT_PROPERTIES[key])
    return props


def _register_material(bl_mat, materials_list, mat_name_to_id, textures, seen_tex):
    """Register a single Blender material into the KN5 material map."""
    mat_name = bl_mat.name
    if mat_name in mat_name_to_id:
        return

    tex_name = None
    if bl_mat.use_nodes:
        for node in bl_mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                img = node.image
                if img.packed_file:
                    import tempfile
                    ext = os.path.splitext(img.name)[1] or '.png'
                    name = img.name if ext in img.name else img.name + ext
                    tmp = os.path.join(tempfile.gettempdir(), name)
                    img.save_render(tmp)
                    tex_name = name
                    if tex_name not in seen_tex:
                        seen_tex.add(tex_name)
                        with open(tmp, 'rb') as fh:
                            textures.append((tex_name, fh.read()))
                else:
                    path = bpy.path.abspath(img.filepath)
                    if os.path.isfile(path):
                        tex_name = os.path.basename(path)
                        if tex_name not in seen_tex:
                            seen_tex.add(tex_name)
                            with open(path, 'rb') as fh:
                                textures.append((tex_name, fh.read()))
                    else:
                        print(f"  WARNING: texture not found: {path}")
                break

    shader = bl_mat.get('ac_shader', DEFAULT_SHADER)
    props = read_ac_properties(bl_mat)

    samplers = [('txDiffuse', 0, tex_name)] if tex_name else []
    mat_id = len(materials_list)
    mat_name_to_id[mat_name] = mat_id
    materials_list.append({
        'name': mat_name,
        'shader': shader,
        'properties': props,
        'samplers': samplers,
    })


def build_material_map(mesh_objects):
    """Build KN5 materials directly from Blender scene."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    materials_list = []
    mat_name_to_id = {}
    textures = []
    seen_tex = set()

    for obj in mesh_objects:
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.data
        if not mesh.materials:
            continue
        for bl_mat in mesh.materials:
            if bl_mat is not None:
                _register_material(bl_mat, materials_list, mat_name_to_id,
                                   textures, seen_tex)

    return materials_list, mat_name_to_id, textures


def get_material_slots(obj, mat_name_to_id):
    """Get list of (slot_index, kn5_material_id) for materials actually used by faces."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.data

    if not mesh.materials:
        return [(None, 0)]
    used_indices = set(p.material_index for p in mesh.polygons)
    slots = []
    for idx in sorted(used_indices):
        if idx < len(mesh.materials) and mesh.materials[idx] is not None:
            mat_name = mesh.materials[idx].name
            kn5_id = mat_name_to_id.get(mat_name, 0)
            slots.append((idx, kn5_id))
    return slots if slots else [(None, 0)]


def identity_matrix():
    """Return 4x4 identity as list of lists."""
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def obj_to_matrix(obj):
    """Convert a Blender object's world matrix to KN5 row-major format.

    Applies change-of-basis from Blender (Z-up) to AC (Y-up):
      AC_x = Bl_x, AC_y = Bl_z, AC_z = -Bl_y
    Formula: transpose(S * M * S^-1) for DirectX row-vector convention.
    """
    m = obj.matrix_world
    r = [[m[row][col] for col in range(4)] for row in range(4)]
    return [
        [ r[0][0],  r[2][0], -r[1][0],  r[3][0]],
        [ r[0][2],  r[2][2], -r[1][2],  r[3][2]],
        [-r[0][1], -r[2][1],  r[1][1], -r[3][1]],
        [ r[0][3],  r[2][3], -r[1][3],  r[3][3]],
    ]


def main():
    print("=" * 60)
    print(f"{SLUG} - KN5 Exporter")
    print("=" * 60)

    print("\nCollecting scene objects...")
    mesh_objects = []
    empty_objects = []

    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            mesh_objects.append(obj)
        elif obj.type == 'EMPTY':
            empty_objects.append(obj)

    mesh_objects.sort(key=lambda o: o.name)
    empty_objects.sort(key=lambda o: o.name)

    print(f"  Meshes: {len(mesh_objects)}")
    print(f"  Empties: {len(empty_objects)}")

    print("\nBuilding materials...")
    materials_list, mat_name_to_id, textures = build_material_map(mesh_objects)
    for mat in materials_list:
        tex = mat['samplers'][0][2] if mat['samplers'] else '(none)'
        print(f"  [{mat_name_to_id[mat['name']]}] {mat['name']} -> {tex}")

    print(f"\nEmbedding {len(textures)} textures...")
    for tex_name, tex_data in textures:
        print(f"  {tex_name}: {len(tex_data)} bytes")

    mesh_nodes = []
    for obj in mesh_objects:
        slots = get_material_slots(obj, mat_name_to_id)
        if len(slots) == 1:
            mesh_nodes.append((obj, obj.name, slots[0][0], slots[0][1]))
        else:
            for slot_idx, kn5_id in slots:
                mat_name = obj.data.materials[slot_idx].name
                node_name = f"{obj.name}_{mat_name}"
                mesh_nodes.append((obj, node_name, slot_idx, kn5_id))

    total_children = len(mesh_nodes) + len(empty_objects)

    print(f"\nWriting KN5 to {KN5_PATH}...")
    with open(KN5_PATH, 'wb') as f:
        write_header(f, version=6)
        write_textures(f, textures)
        write_materials(f, materials_list, version=6)

        write_dummy_node(f, SLUG, identity_matrix(), total_children)

        for obj, node_name, slot_idx, mat_id in mesh_nodes:
            print(f"  Mesh: {node_name} (material={mat_id})")
            verts, indices = get_mesh_data(obj, material_index=slot_idx)
            print(f"    {len(verts)} vertices, {len(indices)} indices")
            write_mesh_node(f, node_name, verts, indices, mat_id)

        box_verts, box_indices = make_box_mesh()

        for obj in empty_objects:
            mat = obj_to_matrix(obj)
            print(f"  Empty: {obj.name} pos=({mat[3][0]:.1f}, {mat[3][1]:.1f}, {mat[3][2]:.1f})")
            write_dummy_node(f, obj.name, mat, 1)
            write_mesh_node(f, obj.name, box_verts, box_indices, 0)

    file_size = os.path.getsize(KN5_PATH)
    print(f"\nDone! KN5 file: {KN5_PATH} ({file_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
