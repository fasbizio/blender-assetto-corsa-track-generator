#!/usr/bin/env python3
"""3D Track Viewer for KN5 files - PyQt5 + OpenGL."""

import struct
import sys
import os
import math
import numpy as np
from io import BytesIO

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QFileDialog, QLabel, QListWidget, QListWidgetItem,
    QSplitter, QGroupBox,
)
from PyQt5.QtCore import Qt, QTimer

from OpenGL.GL import *
from OpenGL.GLU import *
from PyQt5.QtWidgets import QOpenGLWidget

from PIL import Image


# ---------------------------------------------------------------------------
# KN5 Parser
# ---------------------------------------------------------------------------

def read_string(f):
    length = struct.unpack('<i', f.read(4))[0]
    if length < 0 or length > 10000:
        raise ValueError(f"Bad string length: {length}")
    return f.read(length).decode('utf-8', errors='replace')


def parse_kn5(path):
    """Parse KN5 file, return textures, materials, meshes."""
    textures = {}   # name -> PNG bytes
    materials = []   # list of material dicts
    meshes = []      # list of mesh dicts

    with open(path, 'rb') as f:
        magic = f.read(6)
        if magic != b'sc6969':
            raise ValueError(f"Not a KN5 file (magic: {magic})")

        version = struct.unpack('<i', f.read(4))[0]
        if version > 5:
            f.read(4)

        # Textures
        tex_count = struct.unpack('<i', f.read(4))[0]
        for _ in range(tex_count):
            tex_type = struct.unpack('<i', f.read(4))[0]
            tex_name = read_string(f)
            tex_size = struct.unpack('<i', f.read(4))[0]
            tex_data = f.read(tex_size)
            if tex_type == 1:
                textures[tex_name] = tex_data

        # Materials
        mat_count = struct.unpack('<i', f.read(4))[0]
        for _ in range(mat_count):
            mat_name = read_string(f)
            shader = read_string(f)
            f.read(2)  # alphaBlend
            if version > 4:
                f.read(4)  # alphaTested

            prop_count = struct.unpack('<i', f.read(4))[0]
            for _ in range(prop_count):
                read_string(f)
                f.read(4 + 36)

            samp_count = struct.unpack('<i', f.read(4))[0]
            samplers = []
            for _ in range(samp_count):
                samp_name = read_string(f)
                samp_slot = struct.unpack('<i', f.read(4))[0]
                samp_tex = read_string(f)
                samplers.append((samp_name, samp_slot, samp_tex))

            materials.append({
                'name': mat_name,
                'shader': shader,
                'samplers': samplers,
            })

        # Nodes — accumulate transform matrices through hierarchy
        identity = np.eye(4, dtype=np.float32)

        def parse_node(parent_mat=None):
            node_type = struct.unpack('<i', f.read(4))[0]
            name = read_string(f)
            num_children = struct.unpack('<i', f.read(4))[0]
            node_flag = struct.unpack('B', f.read(1))[0]

            if node_type == 1:  # Dummy
                mat_data = struct.unpack('<16f', f.read(64))
                node_mat = np.array(mat_data, dtype=np.float32).reshape(4, 4)
                # Accumulate: child_world = parent @ node (row-major)
                if parent_mat is not None:
                    world_mat = node_mat @ parent_mat
                else:
                    world_mat = node_mat
                for _ in range(num_children):
                    parse_node(world_mat)

            elif node_type == 2:  # Mesh
                f.read(3)  # castShadows, isVisible, isTransparent

                vert_count = struct.unpack('<i', f.read(4))[0]
                # 44 bytes per vertex: pos(12) + normal(12) + uv(8) + tangent(12)
                raw = f.read(vert_count * 44)
                verts = np.zeros((vert_count, 3), dtype=np.float32)
                normals = np.zeros((vert_count, 3), dtype=np.float32)
                uvs = np.zeros((vert_count, 2), dtype=np.float32)
                for i in range(vert_count):
                    off = i * 44
                    verts[i] = struct.unpack_from('<3f', raw, off)
                    normals[i] = struct.unpack_from('<3f', raw, off + 12)
                    uvs[i] = struct.unpack_from('<2f', raw, off + 24)

                # Apply parent transform to vertices and normals
                if parent_mat is not None:
                    ones = np.ones((vert_count, 1), dtype=np.float32)
                    verts_h = np.hstack([verts, ones])
                    verts = (verts_h @ parent_mat)[:, :3]
                    # Normals: rotation only (no translation)
                    rot = parent_mat[:3, :3].copy()
                    normals = (normals @ rot)

                idx_count = struct.unpack('<i', f.read(4))[0]
                idx_raw = f.read(idx_count * 2)
                indices = np.frombuffer(idx_raw, dtype=np.uint16).copy()

                mat_id = struct.unpack('<i', f.read(4))[0]
                f.read(4)   # layer
                f.read(4)   # lodIn
                f.read(4)   # lodOut
                f.read(12)  # bsCenter
                f.read(4)   # bsRadius
                f.read(1)   # isRenderable

                meshes.append({
                    'name': name,
                    'verts': verts,
                    'normals': normals,
                    'uvs': uvs,
                    'indices': indices,
                    'mat_id': mat_id,
                    'tri_count': idx_count // 3,
                })

                for _ in range(num_children):
                    parse_node(parent_mat)

        parse_node()

    return textures, materials, meshes


# ---------------------------------------------------------------------------
# OpenGL Viewport
# ---------------------------------------------------------------------------

class TrackGLWidget(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.meshes = []
        self.materials = []
        self.gl_textures = {}  # tex_name -> GL texture id
        self.mesh_visible = {}  # mesh_name -> bool
        self.display_lists = []  # (mesh_name, dl_id)

        # Camera
        self.cam_yaw = 45.0
        self.cam_pitch = 60.0
        self.cam_dist = 200.0
        self.cam_target = np.array([0.0, 0.0, 0.0], dtype=np.float64)

        # Mouse state
        self._last_pos = None
        self._mouse_button = None

        # Scene bounds
        self._scene_center = np.array([0.0, 0.0, 0.0])
        self._scene_radius = 100.0

        # FPS counter
        self._frame_count = 0
        self._fps = 0
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start(1000)

        self.setFocusPolicy(Qt.StrongFocus)

    def _update_fps(self):
        self._fps = self._frame_count
        self._frame_count = 0
        main = self.window()
        if hasattr(main, 'statusBar') and callable(main.statusBar):
            sb = main.statusBar()
            fname = getattr(main, '_current_file', '')
            sb.showMessage(f"{fname}  |  FPS: {self._fps}")

    def load_scene(self, textures_raw, materials, meshes):
        self.makeCurrent()
        self._cleanup_gl()

        self.materials = materials
        self.meshes = meshes
        self.mesh_visible = {m['name']: True for m in meshes}

        # Upload textures
        for tex_name, tex_bytes in textures_raw.items():
            self._upload_texture(tex_name, tex_bytes)

        # Compute scene bounds
        all_verts = []
        for m in meshes:
            if len(m['verts']) > 0:
                all_verts.append(m['verts'])
        if all_verts:
            combined = np.vstack(all_verts)
            mn = combined.min(axis=0)
            mx = combined.max(axis=0)
            self._scene_center = (mn + mx) / 2.0
            self._scene_radius = float(np.linalg.norm(mx - mn) / 2.0)
        else:
            self._scene_center = np.array([0.0, 0.0, 0.0])
            self._scene_radius = 100.0

        # Extract AC marker positions from mesh centroids
        self._ac_markers = []  # (name, x, y, z, color)
        for m in meshes:
            n = m['name'].upper()
            if n.startswith(('AC_START', 'AC_PIT', 'AC_TIME')):
                centroid = m['verts'].mean(axis=0)
                if 'START' in n:
                    color = (0.2, 0.9, 0.2)   # green
                elif 'PIT' in n:
                    color = (0.2, 0.5, 0.9)   # blue
                else:
                    color = (0.9, 0.9, 0.2)   # yellow
                self._ac_markers.append((m['name'], *centroid, color))

        # Direction arrows along road (set externally via set_direction_path)
        # Initialized empty; populated by manager with centerline data
        if not hasattr(self, '_direction_arrows'):
            self._direction_arrows = []

        # Build display lists
        for m in meshes:
            dl = self._build_display_list(m)
            self.display_lists.append((m['name'], dl))

        # Reset camera
        self.reset_camera()
        self.doneCurrent()
        self.update()

    def _upload_texture(self, name, data):
        try:
            img = Image.open(BytesIO(data))
            img = img.convert('RGBA')
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
            w, h = img.size
            raw = img.tobytes()

            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, raw)
            glGenerateMipmap(GL_TEXTURE_2D)

            self.gl_textures[name] = tex_id
        except Exception as e:
            print(f"Warning: failed to load texture '{name}': {e}")

    def _get_mesh_color(self, mesh_name):
        """Fallback color based on mesh name."""
        n = mesh_name.upper()
        if 'ROAD' in n or 'ASPHALT' in n:
            return (0.35, 0.35, 0.38, 1.0)
        elif 'GRASS' in n or 'GREEN' in n:
            return (0.2, 0.55, 0.15, 1.0)
        elif 'KERB' in n or 'CURB' in n:
            return (0.8, 0.15, 0.15, 1.0)
        elif 'WALL' in n or 'BARRIER' in n:
            return (0.5, 0.5, 0.52, 1.0)
        elif 'GROUND' in n or 'SAND' in n:
            return (0.6, 0.5, 0.3, 1.0)
        elif 'GANTRY' in n:
            if 'LIGHT' in n:
                return (0.9, 0.1, 0.1, 1.0)
            return (0.45, 0.45, 0.48, 1.0)
        return (0.6, 0.6, 0.6, 1.0)

    def _get_mesh_texture(self, mesh):
        """Return GL texture id for mesh's diffuse sampler, or 0."""
        mat_id = mesh['mat_id']
        if 0 <= mat_id < len(self.materials):
            mat = self.materials[mat_id]
            for samp_name, _slot, samp_tex in mat['samplers']:
                if samp_name.lower() in ('txdiffuse', 'diffuse', 'txdetail'):
                    return self.gl_textures.get(samp_tex, 0)
            # Fallback: first sampler
            if mat['samplers']:
                return self.gl_textures.get(mat['samplers'][0][2], 0)
        return 0

    def _build_display_list(self, mesh):
        dl = glGenLists(1)
        if dl == 0:
            return 0
        glNewList(dl, GL_COMPILE)

        tex_id = self._get_mesh_texture(mesh)
        color = self._get_mesh_color(mesh['name'])

        if tex_id:
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glColor4f(1.0, 1.0, 1.0, 1.0)
        else:
            glDisable(GL_TEXTURE_2D)
            glColor4f(*color)

        glMaterialfv(GL_FRONT_AND_BACK, GL_AMBIENT, [color[0]*0.3, color[1]*0.3, color[2]*0.3, 1.0])
        glMaterialfv(GL_FRONT_AND_BACK, GL_DIFFUSE, [color[0], color[1], color[2], 1.0])
        glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR, [0.1, 0.1, 0.1, 1.0])
        glMaterialf(GL_FRONT_AND_BACK, GL_SHININESS, 10.0)

        verts = mesh['verts']
        normals = mesh['normals']
        uvs = mesh['uvs']
        indices = mesh['indices']

        glBegin(GL_TRIANGLES)
        for i in range(0, len(indices), 3):
            for j in range(3):
                idx = indices[i + j]
                glNormal3fv(normals[idx])
                if tex_id:
                    glTexCoord2fv(uvs[idx])
                glVertex3fv(verts[idx])
        glEnd()

        glDisable(GL_TEXTURE_2D)
        glEndList()
        return dl

    def set_direction_path(self, points_2d):
        """Set road centerline for direction arrows. points_2d: list of (x, z) tuples."""
        self._direction_arrows = []
        if not points_2d or len(points_2d) < 10:
            return
        pts = points_2d
        n = len(pts)
        # Place an arrow every ~40 points
        step = max(1, n // 15)
        for i in range(0, n, step):
            j = (i + 3) % n
            x0, z0 = pts[i]
            x1, z1 = pts[j]
            dx, dz = x1 - x0, z1 - z0
            length = math.sqrt(dx * dx + dz * dz)
            if length < 0.01:
                continue
            dx, dz = dx / length, dz / length
            self._direction_arrows.append(((x0, 0.15, z0), (dx, 0, dz)))
        self.update()

    def reset_camera(self):
        self.cam_target = self._scene_center.copy()
        self.cam_dist = self._scene_radius * 2.5
        self.cam_yaw = 45.0
        self.cam_pitch = 60.0
        self.update()

    def _cleanup_gl(self):
        for _name, dl in self.display_lists:
            glDeleteLists(dl, 1)
        self.display_lists = []
        for tex_id in self.gl_textures.values():
            glDeleteTextures([tex_id])
        self.gl_textures = {}

    # -- OpenGL callbacks --

    def initializeGL(self):
        glClearColor(0.15, 0.15, 0.18, 1.0)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)

        # Light from above-right
        glLightfv(GL_LIGHT0, GL_POSITION, [0.3, 1.0, 0.5, 0.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.3, 0.3, 0.3, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.8, 0.8, 0.78, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [0.3, 0.3, 0.3, 1.0])

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        self._update_projection()

    def _update_projection(self):
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        near = max(0.1, self.cam_dist * 0.01)
        far = max(self.cam_dist, self._scene_radius) * 20.0
        gluPerspective(45.0, w / h, near, far)
        glMatrixMode(GL_MODELVIEW)

    def paintGL(self):
        self._frame_count += 1
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        # Camera position from spherical coordinates
        yaw_r = math.radians(self.cam_yaw)
        pitch_r = math.radians(self.cam_pitch)
        cx = self.cam_target[0] + self.cam_dist * math.cos(pitch_r) * math.cos(yaw_r)
        cy = self.cam_target[1] + self.cam_dist * math.sin(pitch_r)
        cz = self.cam_target[2] + self.cam_dist * math.cos(pitch_r) * math.sin(yaw_r)

        gluLookAt(cx, cy, cz,
                  self.cam_target[0], self.cam_target[1], self.cam_target[2],
                  0, 1, 0)

        # Draw ground grid
        self._draw_grid()

        # Draw meshes
        for mesh_name, dl in self.display_lists:
            if dl and self.mesh_visible.get(mesh_name, True):
                glCallList(dl)

        # Draw direction arrows and AC markers on top
        self._draw_direction_arrows()
        self._draw_ac_markers()

    def _draw_grid(self):
        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.3, 0.3, 0.3, 0.5)
        glLineWidth(1.0)

        step = max(10.0, self._scene_radius / 10.0)
        extent = self._scene_radius * 1.5
        cx, cz = self._scene_center[0], self._scene_center[2]

        glBegin(GL_LINES)
        x = cx - extent
        while x <= cx + extent:
            glVertex3f(x, -0.1, cz - extent)
            glVertex3f(x, -0.1, cz + extent)
            x += step
        z = cz - extent
        while z <= cz + extent:
            glVertex3f(cx - extent, -0.1, z)
            glVertex3f(cx + extent, -0.1, z)
            z += step
        glEnd()

        glEnable(GL_LIGHTING)

    def _draw_direction_arrows(self):
        """Draw arrows along road centerline showing track direction."""
        if not hasattr(self, '_direction_arrows') or not self._direction_arrows:
            return

        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)

        arrow_len = max(1.5, self._scene_radius * 0.015)
        head_len = arrow_len * 0.4
        head_w = arrow_len * 0.25

        glColor4f(1.0, 0.7, 0.0, 0.85)  # orange
        glLineWidth(2.0)

        for pos, tangent in self._direction_arrows:
            px, py, pz = pos
            tx, ty, tz = tangent
            # Arrow shaft
            ex = px + tx * arrow_len
            ey = py + ty * arrow_len
            ez = pz + tz * arrow_len
            glBegin(GL_LINES)
            glVertex3f(px, py, pz)
            glVertex3f(ex, ey, ez)
            glEnd()
            # Arrowhead (triangle) — perpendicular in XZ plane
            nx, nz = -tz, tx  # normal to tangent
            hx = ex - tx * head_len
            hz = ez - tz * head_len
            glBegin(GL_TRIANGLES)
            glVertex3f(ex, ey, ez)
            glVertex3f(hx + nx * head_w, py, hz + nz * head_w)
            glVertex3f(hx - nx * head_w, py, hz - nz * head_w)
            glEnd()

        glEnable(GL_LIGHTING)
        glLineWidth(1.0)

    def _draw_ac_markers(self):
        """Draw colored 3D markers for AC_START, AC_PIT, AC_TIME positions."""
        if not hasattr(self, '_ac_markers') or not self._ac_markers:
            return
        # Check collective visibility
        show_markers = any(
            self.mesh_visible.get(name, True)
            for name, *_ in self._ac_markers
        )
        if not show_markers:
            return

        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_DEPTH_TEST)

        marker_size = max(0.5, self._scene_radius * 0.008)

        for name, mx, my, mz, color in self._ac_markers:
            if not self.mesh_visible.get(name, True):
                continue

            # Vertical pole
            glColor4f(*color, 0.9)
            glLineWidth(2.0)
            glBegin(GL_LINES)
            glVertex3f(mx, my, mz)
            glVertex3f(mx, my + marker_size * 4, mz)
            glEnd()

            # Diamond marker at top
            top_y = my + marker_size * 4
            s = marker_size
            glBegin(GL_TRIANGLE_FAN)
            glVertex3f(mx, top_y + s, mz)      # top
            glVertex3f(mx - s, top_y, mz)       # left
            glVertex3f(mx, top_y, mz - s)       # front
            glVertex3f(mx + s, top_y, mz)       # right
            glVertex3f(mx, top_y, mz + s)       # back
            glVertex3f(mx - s, top_y, mz)       # close
            glEnd()
            glBegin(GL_TRIANGLE_FAN)
            glVertex3f(mx, top_y - s, mz)      # bottom
            glVertex3f(mx - s, top_y, mz)
            glVertex3f(mx, top_y, mz + s)
            glVertex3f(mx + s, top_y, mz)
            glVertex3f(mx, top_y, mz - s)
            glVertex3f(mx - s, top_y, mz)
            glEnd()

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glLineWidth(1.0)

    # -- Mouse events --

    def mousePressEvent(self, event):
        self._last_pos = event.pos()
        self._mouse_button = event.button()

    def mouseReleaseEvent(self, event):
        self._last_pos = None
        self._mouse_button = None

    def mouseMoveEvent(self, event):
        if self._last_pos is None:
            return
        dx = event.x() - self._last_pos.x()
        dy = event.y() - self._last_pos.y()
        self._last_pos = event.pos()

        if self._mouse_button == Qt.LeftButton:
            # Orbit
            self.cam_yaw -= dx * 0.3
            self.cam_pitch += dy * 0.3
            self.cam_pitch = max(-89.0, min(89.0, self.cam_pitch))

        elif self._mouse_button == Qt.MiddleButton:
            # Pan along view plane
            speed = max(self.cam_dist, self._scene_radius * 0.01) * 0.003
            yaw_r = math.radians(self.cam_yaw)
            pitch_r = math.radians(self.cam_pitch)
            # Screen-space right vector
            rx = -math.sin(yaw_r)
            rz = math.cos(yaw_r)
            # Screen-space up vector (view plane, accounts for pitch)
            ux = -math.sin(pitch_r) * math.cos(yaw_r)
            uy = math.cos(pitch_r)
            uz = -math.sin(pitch_r) * math.sin(yaw_r)
            self.cam_target[0] += (rx * dx + ux * dy) * speed
            self.cam_target[1] += uy * dy * speed
            self.cam_target[2] += (rz * dx + uz * dy) * speed

        self._update_projection()
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 0.85 if delta > 0 else 1.0 / 0.85
        self.cam_dist *= factor
        min_dist = max(0.5, self._scene_radius * 0.005)
        max_dist = self._scene_radius * 50.0
        self.cam_dist = max(min_dist, min(self.cam_dist, max_dist))
        self._update_projection()
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_R:
            self.reset_camera()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class TrackViewerWindow(QMainWindow):
    def __init__(self, kn5_path=None):
        super().__init__()
        self.setWindowTitle("Track Viewer - Casaluce")
        self.resize(1280, 800)
        self._current_file = ""

        # Widgets
        self.gl_widget = TrackGLWidget()
        self.mesh_list = QListWidget()
        self.mesh_list.itemChanged.connect(self._on_mesh_toggled)
        self.info_label = QLabel("No file loaded")
        self.info_label.setWordWrap(True)

        # Sidebar
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)

        btn_open = QPushButton("Open KN5...")
        btn_open.clicked.connect(self._open_file)
        sidebar_layout.addWidget(btn_open)

        btn_reset = QPushButton("Reset Camera (R)")
        btn_reset.clicked.connect(self.gl_widget.reset_camera)
        sidebar_layout.addWidget(btn_reset)

        # Mesh group
        mesh_group = QGroupBox("Meshes")
        mesh_layout = QVBoxLayout(mesh_group)
        mesh_layout.addWidget(self.mesh_list)
        sidebar_layout.addWidget(mesh_group, stretch=1)

        # Info group
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.addWidget(self.info_label)
        sidebar_layout.addWidget(info_group)

        sidebar.setFixedWidth(260)

        # Splitter layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(sidebar)
        splitter.addWidget(self.gl_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        # Status bar
        self.statusBar().showMessage("Ready")

        # Auto-load if path given
        if kn5_path and os.path.isfile(kn5_path):
            QTimer.singleShot(100, lambda: self._load_kn5(kn5_path))

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open KN5 file", "",
            "KN5 files (*.kn5);;All files (*)")
        if path:
            self._load_kn5(path)

    def _load_kn5(self, path):
        self.statusBar().showMessage(f"Loading {os.path.basename(path)}...")
        QApplication.processEvents()

        try:
            textures, materials, meshes = parse_kn5(path)
        except Exception as e:
            self.statusBar().showMessage(f"Error: {e}")
            return

        self._current_file = os.path.basename(path)
        self.setWindowTitle(f"Track Viewer - {self._current_file}")

        # Load into GL
        self.gl_widget.load_scene(textures, materials, meshes)

        # Update mesh list
        self.mesh_list.blockSignals(True)
        self.mesh_list.clear()
        for m in meshes:
            item = QListWidgetItem(f"{m['name']} ({m['tri_count']} tri)")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, m['name'])
            self.mesh_list.addItem(item)
        self.mesh_list.blockSignals(False)

        # Update info
        total_verts = sum(len(m['verts']) for m in meshes)
        total_tris = sum(m['tri_count'] for m in meshes)
        self.info_label.setText(
            f"File: {self._current_file}\n"
            f"Meshes: {len(meshes)}\n"
            f"Vertices: {total_verts:,}\n"
            f"Triangles: {total_tris:,}\n"
            f"Textures: {len(textures)}\n"
            f"Materials: {len(materials)}"
        )

        self.statusBar().showMessage(f"{self._current_file}  |  FPS: --")

    def _on_mesh_toggled(self, item):
        mesh_name = item.data(Qt.UserRole)
        visible = item.checkState() == Qt.Checked
        self.gl_widget.mesh_visible[mesh_name] = visible
        self.gl_widget.update()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Dark theme
    from PyQt5.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(45, 45, 48))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(35, 35, 38))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
    palette.setColor(QPalette.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(55, 55, 58))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    kn5_path = sys.argv[1] if len(sys.argv) > 1 else None
    window = TrackViewerWindow(kn5_path)
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
