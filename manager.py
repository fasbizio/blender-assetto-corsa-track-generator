#!/usr/bin/env python3
"""Track Manager Hub - Unified PyQt5 GUI for multi-track build pipeline."""

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QTextEdit, QProgressBar,
    QListWidget, QListWidgetItem, QGroupBox, QCheckBox,
    QFormLayout, QDoubleSpinBox, QLineEdit, QComboBox,
    QScrollArea, QSplitter, QFrame, QDialog, QFileDialog,
    QMessageBox, QInputDialog, QButtonGroup, QRadioButton,
    QTreeWidget, QTreeWidgetItem, QSlider, QStackedWidget,
)
from PyQt5.QtCore import (
    Qt, QProcess, QProcessEnvironment, pyqtSignal, QPointF, QTimer,
    QThread, QRectF,
)
from PyQt5.QtGui import (
    QPalette, QColor, QFont, QTextCursor, QPainter, QPen, QBrush,
    QImage, QPolygonF,
)

GENERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(GENERATOR_DIR, "scripts"))
import platform_utils
from blend_meta import is_blend_modified, backup_blend
from spline_utils import (
    catmull_rom_point, interpolate_centerline, interpolate_open,
    load_centerline_v2, save_centerline_v2,
)

LOG_TERM_STYLE = (
    "QTextEdit { background: #1e1e1e; color: #dcdcdc; "
    "font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt; border: none; }"
)

# Load standard defaults from defaults.json
_DEFAULTS = {}
_defaults_path = os.path.join(GENERATOR_DIR, "defaults.json")
if os.path.isfile(_defaults_path):
    with open(_defaults_path, encoding="utf-8") as _df:
        _DEFAULTS = json.load(_df)
_def_geo = _DEFAULTS.get("geometry", {})
_def_ai = _DEFAULTS.get("ai_line", {})
_def_surf = _DEFAULTS.get("surfaces", {})
_def_elev = _DEFAULTS.get("elevation", {})
_def_bank = _DEFAULTS.get("banking", {})

PARAM_DEFS = {
    "geometry": {
        "label": "Geometry",
        "params": [
            ("road_width",       "Road Width (m)",      _def_geo.get("road_width", 6.0),       3.0,  20.0, 0.5),
            ("kerb_width",       "Kerb Width (m)",      _def_geo.get("kerb_width", 1.0),       0.2,  3.0,  0.1),
            ("kerb_height",      "Kerb Height (m)",     _def_geo.get("kerb_height", 0.05),     0.01, 0.3,  0.01),
            ("grass_width",      "Grass Width (m)",     _def_geo.get("grass_width", 2.0),      1.0,  50.0, 1.0),
            ("wall_height",      "Wall Height (m)",     _def_geo.get("wall_height", 1.5),      0.5,  5.0,  0.1),
            ("wall_thickness",   "Wall Thickness (m)",  _def_geo.get("wall_thickness", 1.0),   0.3,  3.0,  0.1),
            ("ground_tile_size", "Ground Tile (m)",     _def_geo.get("ground_tile_size", 30.0), 5.0, 100.0, 5.0),
        ],
    },
    "ai_line": {
        "label": "AI Line",
        "params": [
            ("default_speed",    "Default Speed (km/h)",    _def_ai.get("default_speed", 80.0),    20.0, 200.0, 5.0),
            ("min_corner_speed", "Min Corner Speed (km/h)", _def_ai.get("min_corner_speed", 35.0), 10.0, 100.0, 5.0),
        ],
    },
    "surfaces": {
        "label": "Surfaces",
        "params": [
            ("road_friction",  "Road Friction",  _def_surf.get("road_friction", 0.97),  0.5, 1.0, 0.01),
            ("kerb_friction",  "Kerb Friction",  _def_surf.get("kerb_friction", 0.93),  0.5, 1.0, 0.01),
            ("grass_friction", "Grass Friction", _def_surf.get("grass_friction", 0.60), 0.1, 1.0, 0.01),
        ],
    },
    "elevation": {
        "label": "Elevation",
        "params": [
            ("scale", "Scale", _def_elev.get("scale", 1.0), 0.0, 5.0, 0.1),
        ],
    },
    "banking": {
        "label": "Banking",
        "params": [
            ("design_speed", "Design Speed (km/h)", _def_bank.get("design_speed", 60.0), 20.0, 200.0, 5.0),
            ("friction",     "Friction",            _def_bank.get("friction", 0.7),       0.1,  1.0,   0.05),
            ("scale",        "Scale",               _def_bank.get("scale", 1.0),          0.0,  3.0,   0.1),
            ("max_angle",    "Max Angle (°)",       _def_bank.get("max_angle", 15.0),     1.0,  45.0,  1.0),
        ],
    },
}

INFO_FIELDS = [
    ("name",      "Name",       "line"),
    ("city",      "City",       "line"),
    ("province",  "Province",   "line"),
    ("region",    "Region",     "line"),
    ("country",   "Country",    "line"),
    ("length",    "Length (m)", "line"),
    ("pitboxes",  "Pitboxes",   "line"),
    ("direction", "Direction",  "combo"),
    ("geotags",   "Geotags",    "line"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def apply_dark_theme(app):
    """Apply a dark Fusion theme to the application."""
    app.setStyle("Fusion")
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


class ClickableInfoLabel(QLabel):
    """Small '?' label that shows its tooltip text on click."""

    def __init__(self, info_text, parent=None):
        super().__init__("?", parent)
        self._info_text = info_text
        self.setStyleSheet(
            "QLabel { color: #5599ff; font-size: 9pt; font-weight: bold;"
            " border: 1px solid #5599ff; border-radius: 7px;"
            " min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px;"
            " qproperty-alignment: AlignCenter; }"
        )
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(info_text)

    def mousePressEvent(self, event):
        from PyQt5.QtWidgets import QToolTip
        QToolTip.showText(event.globalPos(), self._info_text, self)


def make_info_label(tooltip_text):
    """Create a small '?' label that shows info on hover and click."""
    return ClickableInfoLabel(tooltip_text)


def flash_status(label, text, color, duration_ms=4000):
    """Show a status message that auto-clears after duration_ms."""
    label.setText(text)
    label.setStyleSheet(f"color: {color};")
    QTimer.singleShot(duration_ms, lambda: label.setText(""))


PARAM_TOOLTIPS = {
    "road_width": (
        "Larghezza della carreggiata asfaltata in metri.\n"
        "Kart: 5-6 m | Stradale: 8-10 m | F1: 12-15 m\n"
        "Default: 6.0 m (kartodromo)"
    ),
    "kerb_width": (
        "Larghezza dei cordoli ai lati della strada.\n"
        "Valori tipici: 0.5-1.5 m\n"
        "Default: 1.0 m"
    ),
    "kerb_height": (
        "Altezza dei cordoli rispetto al piano strada.\n"
        "Troppo alto = le auto rimbalzano. Troppo basso = impercettibile.\n"
        "Valori tipici: 0.03-0.10 m | Default: 0.05 m"
    ),
    "grass_width": (
        "Larghezza della fascia d'erba tra cordolo e barriera.\n"
        "Valori bassi = pista stretta e tecnica. Alti = ampia via di fuga.\n"
        "Kartodromo: 2-3 m | Circuito: 5-15 m | Default: 2.0 m"
    ),
    "wall_height": (
        "Altezza delle barriere perimetrali.\n"
        "Deve essere sufficiente a bloccare le auto.\n"
        "Valori tipici: 1.0-2.5 m | Default: 1.5 m"
    ),
    "wall_thickness": (
        "Spessore delle barriere laterali.\n"
        "Influisce solo sull'aspetto visivo, non sulla collisione.\n"
        "Valori tipici: 0.5-1.5 m | Default: 1.0 m"
    ),
    "ground_tile_size": (
        "Dimensione di ogni tile del terreno sotto la pista.\n"
        "Valori bassi = più dettaglio ma più poligoni.\n"
        "Valori tipici: 20-50 m | Default: 30 m"
    ),
    "default_speed": (
        "Velocità dell'IA sui rettilinei (km/h).\n"
        "Kart: 60-80 | Stradale: 100-150 | GT: 150-200\n"
        "Default: 80 km/h"
    ),
    "min_corner_speed": (
        "Velocità minima dell'IA nelle curve più strette (km/h).\n"
        "L'IA interpola tra questa e la default speed in base alla curvatura.\n"
        "Kart: 25-40 | Stradale: 40-60 | Default: 35 km/h"
    ),
    "road_friction": (
        "Coefficiente d'attrito dell'asfalto.\n"
        "1.0 = grip massimo. Valori più bassi = strada scivolosa.\n"
        "Asfalto nuovo: 0.97-1.00 | Consumato: 0.90-0.95 | Default: 0.97"
    ),
    "kerb_friction": (
        "Coefficiente d'attrito dei cordoli.\n"
        "Di solito leggermente inferiore all'asfalto.\n"
        "Valori tipici: 0.90-0.95 | Default: 0.93"
    ),
    "grass_friction": (
        "Coefficiente d'attrito dell'erba.\n"
        "Valore basso = uscita di pista molto penalizzante.\n"
        "Bagnata: 0.30-0.40 | Secca: 0.50-0.65 | Default: 0.60"
    ),
}

INFO_TOOLTIPS = {
    "name": (
        "Nome della pista visibile in Assetto Corsa e Content Manager.\n"
        "Es: Kartodromo di Casaluce, Pista Caudina"
    ),
    "city": (
        "Città dove si trova il circuito.\n"
        "Es: Casaluce, Montesarchio, Martina Franca"
    ),
    "province": (
        "Sigla della provincia.\n"
        "Es: CE, BN, TA, NA"
    ),
    "region": (
        "Regione geografica.\n"
        "Es: Campania, Puglia, Lazio"
    ),
    "country": (
        "Nazione della pista.\n"
        "Es: Italy, Germany, United Kingdom"
    ),
    "length": (
        "Lunghezza approssimativa del tracciato in metri.\n"
        "Usata per le info di AC. Es: 850, 1200, 3500"
    ),
    "pitboxes": (
        "Numero di posti in pit lane.\n"
        "Determina quanti AC_PIT e AC_START vengono generati.\n"
        "Kart: 10-20 | Circuito: 20-40 | Es: 16"
    ),
    "direction": (
        "Senso di percorrenza principale del layout default.\n"
        "clockwise = senso orario | counter-clockwise = antiorario"
    ),
    "geotags": (
        "Coordinate GPS mostrate in Content Manager.\n"
        "Formato: latitudine, longitudine\n"
        "Es: 40.9784, 14.1892"
    ),
}


def _make_form_label(text, info_text):
    """Create a form row label with a '?' info icon."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(QLabel(text))
    lay.addWidget(ClickableInfoLabel(info_text))
    lay.addStretch()
    return w


def discover_tracks(parent_dir):
    """Scan parent_dir for subdirectories containing track_config.json.

    Returns a sorted list of dicts:
        [{"dir": dirname, "path": abs_path, "config": parsed_json}, ...]
    """
    tracks = []
    for d in sorted(os.listdir(parent_dir)):
        full = os.path.join(parent_dir, d)
        cfg_path = os.path.join(full, "track_config.json")
        if os.path.isdir(full) and os.path.isfile(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    config = json.load(f)
                tracks.append({"dir": d, "path": full, "config": config})
            except Exception:
                pass
    return tracks


def build_steps(config, track_root, force_skip_init=False):
    """Build the step list dynamically from config.

    Returns list of (label, cmd, env_extra) tuples.
    env_extra is a dict of extra env vars for that step.
    If *force_skip_init* is True, the Init Blend step is never prepended.
    """
    slug = config.get("slug", "track")
    has_reverse = config.get("layouts", {}).get("reverse", False)

    blender = platform_utils.find_blender()
    # venv is inside each track project, not the generator
    if platform_utils.IS_WINDOWS:
        venv_py = os.path.join(track_root, ".venv", "Scripts", "python.exe")
    else:
        venv_py = os.path.join(track_root, ".venv", "bin", "python3")
    blend_file = os.path.join(track_root, f"{slug}.blend")
    reverse_blend = os.path.join(track_root, f"{slug}_reverse.blend")
    scripts = os.path.join(GENERATOR_DIR, "scripts")
    init_blend_py = os.path.join(scripts, "init_blend.py")
    centerline_file = os.path.join(track_root, "centerline.json")
    needs_init = not os.path.isfile(blend_file)
    if not needs_init and os.path.isfile(centerline_file):
        if os.path.getmtime(centerline_file) > os.path.getmtime(blend_file):
            needs_init = True

    if has_reverse:
        total = 6
        steps = [
            (f"1/{total} - Export KN5",
             [blender, "--background", blend_file,
              "--python", os.path.join(scripts, "export_kn5.py")],
             {}),
            (f"2/{total} - Mod folder",
             [venv_py, os.path.join(scripts, "setup_mod_folder.py")],
             {}),
            (f"3/{total} - AI line CW",
             [blender, "--background", blend_file,
              "--python", os.path.join(scripts, "generate_ai_line.py")],
             {}),
            (f"4/{total} - Reverse blend",
             [blender, "--background", blend_file,
              "--python", os.path.join(scripts, "create_reverse_blend.py")],
             {}),
            (f"5/{total} - KN5 reverse",
             [blender, "--background", reverse_blend,
              "--python", os.path.join(scripts, "export_kn5.py")],
             {"TRACK_REVERSE": "1"}),
            (f"6/{total} - AI line CCW",
             [blender, "--background", reverse_blend,
              "--python", os.path.join(scripts, "generate_ai_line.py")],
             {"TRACK_REVERSE": "1"}),
        ]
    else:
        total = 3
        steps = [
            (f"1/{total} - Export KN5",
             [blender, "--background", blend_file,
              "--python", os.path.join(scripts, "export_kn5.py")],
             {}),
            (f"2/{total} - Mod folder",
             [venv_py, os.path.join(scripts, "setup_mod_folder.py")],
             {}),
            (f"3/{total} - AI line",
             [blender, "--background", blend_file,
              "--python", os.path.join(scripts, "generate_ai_line.py")],
             {}),
        ]

    if needs_init and not force_skip_init:
        steps.insert(0, ("Init Blend", [blender, "--background", "--python", init_blend_py], {}))
        # Re-label all steps
        total = len(steps)
        steps = [(f"{i+1}/{total} - {lbl.split(' - ', 1)[-1] if ' - ' in lbl else lbl}", cmd, env)
                 for i, (lbl, cmd, env) in enumerate(steps)]

    return steps


# ---------------------------------------------------------------------------
# Tab 1: Parameters
# ---------------------------------------------------------------------------

class ParametersPanel(QWidget):
    """Editable form for track_config.json parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets = {}
        self._config_file = None

        main_layout = QVBoxLayout(self)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Salva")
        btn_save.setStyleSheet(
            "QPushButton { background: #2a6e2a; padding: 6px 16px; }"
            "QPushButton:hover { background: #358535; }"
        )
        btn_save.setToolTip("Salva i parametri nel file track_config.json")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)

        btn_load = QPushButton("Carica")
        btn_load.setToolTip("Ricarica i parametri dal file track_config.json")
        btn_load.clicked.connect(self._load)
        btn_layout.addWidget(btn_load)

        btn_defaults = QPushButton("Ripristina Defaults")
        btn_defaults.setToolTip("Ripristina tutti i parametri ai valori predefiniti del generatore")
        btn_defaults.clicked.connect(self._reset_defaults)
        btn_layout.addWidget(btn_defaults)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # Scrollable area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # Layout section (reverse checkbox)
        layout_box = QGroupBox()
        layout_header = QHBoxLayout()
        layout_header.addWidget(QLabel("<b>Layout</b>"))
        layout_header.addWidget(make_info_label("Configurazione dei layout della pista (default e reverse)"))
        layout_header.addStretch()
        layout_form = QVBoxLayout()
        layout_top = QVBoxLayout(layout_box)
        layout_top.addLayout(layout_header)
        reverse_tip = (
            "Se attivo, la build genera anche il layout in senso opposto.\n"
            "Gli step di build passano da 3 a 6 (KN5 + AI line per entrambe le direzioni).\n"
            "Il risultato è una mod con due layout: default (CW) e reverse (CCW)."
        )
        rev_row = QHBoxLayout()
        self.chk_reverse = QCheckBox("Reverse layout")
        self.chk_reverse.setToolTip(reverse_tip)
        rev_row.addWidget(self.chk_reverse)
        rev_row.addWidget(ClickableInfoLabel(reverse_tip))
        rev_row.addStretch()
        layout_form.addLayout(rev_row)
        layout_top.addLayout(layout_form)
        scroll_layout.addWidget(layout_box)

        # Numeric parameter groups
        group_info = {
            "geometry": "Parametri geometrici che controllano la generazione 3D della pista",
            "ai_line": "Velocità dell'IA: regolano il comportamento delle auto guidate dal computer",
            "surfaces": "Coefficienti d'attrito delle superfici (1.0 = grip massimo)",
            "elevation": "Elevazione del terreno da dati SRTM (0=piatto, 1=reale, >1=esagerato)",
            "banking": "Inclinazione laterale della strada in curva (superelevazione)",
        }
        for group_key, group_def in PARAM_DEFS.items():
            group_box = QGroupBox()
            group_top = QVBoxLayout(group_box)
            gh = QHBoxLayout()
            gh.addWidget(QLabel(f"<b>{group_def['label']}</b>"))
            tip_text = group_info.get(group_key, "")
            if tip_text:
                gh.addWidget(make_info_label(tip_text))
            gh.addStretch()
            group_top.addLayout(gh)

            # Banking: add enable checkbox before spinboxes
            if group_key == "banking":
                bank_chk_row = QHBoxLayout()
                self.chk_banking_enabled = QCheckBox("Enable banking")
                self.chk_banking_enabled.setChecked(_def_bank.get("enabled", True))
                self.chk_banking_enabled.setToolTip(
                    "Attiva/disattiva l'inclinazione laterale automatica in curva")
                bank_chk_row.addWidget(self.chk_banking_enabled)
                bank_chk_row.addStretch()
                group_top.addLayout(bank_chk_row)

            form = QFormLayout()
            for key, label, default, vmin, vmax, step in group_def["params"]:
                spin = QDoubleSpinBox()
                spin.setRange(vmin, vmax)
                spin.setSingleStep(step)
                spin.setDecimals(2)
                spin.setValue(default)
                tip = PARAM_TOOLTIPS.get(key, "")
                if tip:
                    spin.setToolTip(tip)
                    form.addRow(_make_form_label(label, tip), spin)
                else:
                    form.addRow(label, spin)
                self._widgets[f"{group_key}.{key}"] = spin
            group_top.addLayout(form)
            scroll_layout.addWidget(group_box)

        # Info group
        info_box = QGroupBox()
        info_top = QVBoxLayout(info_box)
        ih = QHBoxLayout()
        ih.addWidget(QLabel("<b>Info</b>"))
        ih.addWidget(make_info_label("Metadati della pista visibili in Assetto Corsa e Content Manager"))
        ih.addStretch()
        info_top.addLayout(ih)
        info_form = QFormLayout()
        info_top.addLayout(info_form)
        for key, label, wtype in INFO_FIELDS:
            if wtype == "combo":
                widget = QComboBox()
                widget.addItems(["clockwise", "counter-clockwise"])
            else:
                widget = QLineEdit()
            tip = INFO_TOOLTIPS.get(key, "")
            if tip:
                widget.setToolTip(tip)
                info_form.addRow(_make_form_label(label, tip), widget)
            else:
                info_form.addRow(label, widget)
            self._widgets[f"info.{key}"] = widget
        scroll_layout.addWidget(info_box)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

        # Status
        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

    def load_track(self, config_path):
        """Load parameters from a track_config.json file."""
        self._config_file = config_path
        if os.path.isfile(config_path):
            self._load()

    def _get_values(self):
        """Collect current widget values into a dict."""
        data = {}
        for group_key, group_def in PARAM_DEFS.items():
            data[group_key] = {}
            for key, _label, _default, _vmin, _vmax, _step in group_def["params"]:
                widget = self._widgets[f"{group_key}.{key}"]
                data[group_key][key] = widget.value()

        data["info"] = {}
        for key, _label, wtype in INFO_FIELDS:
            widget = self._widgets[f"info.{key}"]
            if wtype == "combo":
                data["info"][key] = widget.currentText()
            else:
                val = widget.text()
                # Store geotags as list
                if key == "geotags" and "," in val:
                    data["info"][key] = [s.strip() for s in val.split(",")]
                else:
                    data["info"][key] = val

        data["layouts"] = {"reverse": self.chk_reverse.isChecked()}
        # Banking enabled checkbox (not a spinbox, separate handling)
        data.setdefault("banking", {})["enabled"] = self.chk_banking_enabled.isChecked()
        return data

    def _set_values(self, data):
        """Push config data into widgets."""
        # Layout
        has_reverse = data.get("layouts", {}).get("reverse", False)
        self.chk_reverse.setChecked(has_reverse)

        # Numeric groups
        for group_key, group_def in PARAM_DEFS.items():
            group_data = data.get(group_key, {})
            for key, _label, _default, _vmin, _vmax, _step in group_def["params"]:
                if key in group_data:
                    self._widgets[f"{group_key}.{key}"].setValue(group_data[key])

        # Banking enabled checkbox
        bank_enabled = data.get("banking", {}).get("enabled", _def_bank.get("enabled", True))
        self.chk_banking_enabled.setChecked(bank_enabled)

        # Info
        info_data = data.get("info", {})
        for key, _label, wtype in INFO_FIELDS:
            if key in info_data:
                widget = self._widgets[f"info.{key}"]
                val = info_data[key]
                if wtype == "combo":
                    widget.setCurrentText(str(val))
                else:
                    if isinstance(val, list):
                        widget.setText(", ".join(str(v) for v in val))
                    else:
                        widget.setText(str(val))

    def _save(self):
        if not self._config_file:
            flash_status(self.status_label, "Nessuna pista selezionata", "#e8a838")
            return

        data = self._get_values()
        # Merge with existing config to preserve camera, slug, etc.
        if os.path.isfile(self._config_file):
            try:
                with open(self._config_file, encoding="utf-8") as f:
                    existing = json.load(f)
                for k, v in existing.items():
                    if k not in data:
                        data[k] = v
            except Exception:
                pass
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            flash_status(self.status_label, "Parametri salvati", "#55cc55")
        except Exception as e:
            flash_status(self.status_label, f"Errore: {e}", "#ff5555")

    def _load(self):
        if not self._config_file or not os.path.isfile(self._config_file):
            flash_status(self.status_label, "File config non trovato", "#e8a838")
            return
        try:
            with open(self._config_file, encoding="utf-8") as f:
                data = json.load(f)
            self._set_values(data)
            flash_status(self.status_label, "Parametri caricati", "#55cc55")
        except Exception as e:
            flash_status(self.status_label, f"Errore: {e}", "#ff5555")

    def _reset_defaults(self):
        defaults = {}
        for group_key, group_def in PARAM_DEFS.items():
            defaults[group_key] = {}
            for key, _label, default, _vmin, _vmax, _step in group_def["params"]:
                defaults[group_key][key] = default
        self._set_values(defaults)
        flash_status(self.status_label, "Valori predefiniti ripristinati", "#5599ff")


# ---------------------------------------------------------------------------
# New Track Dialog
# ---------------------------------------------------------------------------

class NewTrackDialog(QDialog):
    """Modal dialog for creating a new track project."""

    def __init__(self, default_parent_dir, existing_slugs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuova Pista")
        self.setMinimumWidth(420)
        self._result_data = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Parent directory
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(default_parent_dir)
        self.dir_edit.setToolTip("Cartella in cui verrà creata la directory del progetto pista")
        btn_browse = QPushButton("Sfoglia...")
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, stretch=1)
        dir_row.addWidget(btn_browse)
        form.addRow("Directory padre:", dir_row)

        # Slug
        self.slug_edit = QLineEdit()
        self.slug_edit.setPlaceholderText("nome-cartella (obbligatorio)")
        self.slug_edit.setToolTip("Identificativo unico della pista (usato come nome cartella e file .blend)")
        form.addRow("Slug:", self.slug_edit)

        # Track name
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Nome pista (opzionale, default = slug)")
        self.name_edit.setToolTip("Nome visibile della pista nel menu di Assetto Corsa")
        form.addRow("Nome pista:", self.name_edit)

        layout.addLayout(form)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Annulla")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_ok = QPushButton("Crea")
        btn_ok.setStyleSheet(
            "QPushButton { background: #2a6e2a; padding: 6px 20px; }"
            "QPushButton:hover { background: #358535; }"
        )
        btn_ok.clicked.connect(self._validate_and_accept)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

        self._existing_slugs = existing_slugs

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Seleziona directory padre", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _validate_and_accept(self):
        slug = self.slug_edit.text().strip()
        parent = self.dir_edit.text().strip()
        if not slug:
            QMessageBox.warning(self, "Errore", "Lo slug è obbligatorio.")
            return
        if not os.path.isdir(parent):
            QMessageBox.warning(self, "Errore", "La directory padre non esiste.")
            return
        if slug in self._existing_slugs:
            QMessageBox.warning(self, "Errore", f"Lo slug '{slug}' esiste già.")
            return
        full_path = os.path.join(parent, slug)
        if os.path.exists(full_path):
            QMessageBox.warning(self, "Errore", f"La cartella '{full_path}' esiste già.")
            return
        name = self.name_edit.text().strip() or slug
        self._result_data = {"slug": slug, "name": name, "parent": parent, "path": full_path}
        self.accept()

    def get_result(self):
        return self._result_data


# ---------------------------------------------------------------------------
# Tab 2: Layout Editor (multi-layer)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Elevation helpers (SRTM + CubicSpline — manager-only, NOT for Blender Python)
# ---------------------------------------------------------------------------

def _world_to_latlon(wx, wy, map_center):
    """Convert local meter coordinates to lat/lon for SRTM queries."""
    lat = map_center[0] + wy / 111320.0
    lon = map_center[1] + wx / (111320.0 * math.cos(math.radians(map_center[0])))
    return lat, lon


def _fetch_srtm_elevation(points, map_center):
    """Query SRTM elevation for control points (meters from map_center).

    Returns list of Z values (meters). Returns 0.0 for failed queries.
    """
    try:
        import srtm
    except ImportError:
        return [0.0] * len(points)
    data = srtm.get_data()
    elevations = []
    for wx, wy in points:
        lat, lon = _world_to_latlon(wx, wy, map_center)
        try:
            z = data.get_elevation(lat, lon)
            elevations.append(z if z is not None else 0.0)
        except Exception:
            elevations.append(0.0)
    return elevations


def _smooth_elevation(raw_elev, closed):
    """Smooth elevation with CubicSpline (periodic for closed, natural for open)."""
    try:
        from scipy.interpolate import CubicSpline
    except ImportError:
        return list(raw_elev)
    n = len(raw_elev)
    if n < 3:
        return list(raw_elev)
    if closed:
        x = list(range(n + 1))
        y = list(raw_elev) + [raw_elev[0]]
        cs = CubicSpline(x, y, bc_type='periodic')
        return [float(cs(i)) for i in range(n)]
    else:
        x = list(range(n))
        cs = CubicSpline(x, raw_elev, bc_type='natural')
        return [float(cs(i)) for i in range(n)]


LAYER_COLORS = {
    "road": QColor(80, 180, 255),
    "curb": QColor(255, 120, 60),
    "wall": QColor(160, 160, 160),
}
LAYER_COLORS_DIM = {k: QColor(v.red(), v.green(), v.blue(), 80) for k, v in LAYER_COLORS.items()}
START_COLOR = QColor(60, 220, 60)

# -- Map tile constants --

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
if platform_utils.IS_WINDOWS:
    _cache_base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
else:
    _cache_base = os.path.join(os.path.expanduser("~"), ".cache")
TILE_CACHE_DIR = os.path.join(_cache_base, "ac-track-manager", "tiles")
MAP_ZOOM_DEFAULT = 14
MAP_RADIUS = 3  # 7x7 = 49 tiles


def _latlon_to_tile(lat, lon, zoom):
    """Convert lat/lon to slippy-map tile coordinates."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(x, n - 1)), max(0, min(y, n - 1))


def _tile_to_latlon(tx, ty, zoom):
    """Convert tile coordinates to lat/lon (top-left corner)."""
    n = 2 ** zoom
    lon = tx / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ty / n)))
    return math.degrees(lat_rad), lon


class TileFetcher(QThread):
    """Background thread for downloading map tiles."""

    tile_ready = pyqtSignal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._running = True
        self._pending = set()
        # Clean up old provider-based cache dirs (v3.0.0 → v3.1.0 migration)
        for old_dir in ("satellite", "street"):
            old_path = os.path.join(TILE_CACHE_DIR, old_dir)
            if os.path.isdir(old_path):
                shutil.rmtree(old_path, ignore_errors=True)

    def request_tile(self, z, x, y):
        key = (z, x, y)
        if key not in self._pending:
            self._pending.add(key)
            self._queue.put(key)

    def run(self):
        while self._running:
            try:
                key = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            z, x, y = key
            cache_path = os.path.join(TILE_CACHE_DIR, str(z), str(x), f"{y}.png")
            if not os.path.isfile(cache_path):
                url = TILE_URL.format(z=z, x=x, y=y)
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "AC-Track-Manager/1.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = resp.read()
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, "wb") as f:
                        f.write(data)
                except Exception:
                    self._pending.discard(key)
                    continue
            self._pending.discard(key)
            self.tile_ready.emit(z, x, y)

    def stop(self):
        self._running = False
        self.wait(2000)


class TrackLayer:
    """Single layer of control points (road / curb / wall)."""

    def __init__(self, name, layer_type, closed, points=None):
        self.name = name
        self.layer_type = layer_type  # "road" | "curb" | "wall"
        self.closed = closed
        self.points = points or []
        self.visible = True
        self.elevation = []

    def to_dict(self):
        d = {
            "name": self.name,
            "type": self.layer_type,
            "closed": self.closed,
            "points": [list(p) for p in self.points],
        }
        if self.elevation:
            d["elevation"] = list(self.elevation)
        return d

    @classmethod
    def from_dict(cls, d):
        layer = cls(d["name"], d["type"], d.get("closed", d["type"] == "road"),
                     [list(p) for p in d.get("points", [])])
        layer.elevation = d.get("elevation", [])
        return layer


class TrackCanvas(QWidget):
    """Interactive 2D canvas for editing multi-layer track control points."""

    points_changed = pyqtSignal()

    POINT_RADIUS = 6
    HOVER_RADIUS = 10
    GRAB_RADIUS = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layers = []           # list[TrackLayer]
        self._active_layer_idx = -1
        self._start_pos = None      # [x, y] or None
        self._start_dir = 90.0      # degrees
        self._edit_mode = "points"  # "points" | "start" | "move"
        self._move_drag = False
        self._move_last = None
        self._rotate_drag = False
        self._rotate_last_angle = 0.0

        # Map tile state
        self._map_center = None     # (lat, lon) or None
        self._map_visible = True
        self._map_opacity = 0.5
        self._map_image = None      # pre-composed QImage of all tiles
        self._map_world_origin = (0.0, 0.0)  # world coords of image top-left
        self._map_world_size = (0.0, 0.0)    # world (w, h) of image
        self._tile_fetcher = TileFetcher(self)
        self._tile_fetcher.tile_ready.connect(self._on_tile_ready)
        self._tile_fetcher.start()
        self._compose_timer = QTimer(self)
        self._compose_timer.setSingleShot(True)
        self._compose_timer.setInterval(200)
        self._compose_timer.timeout.connect(self._compose_map_image)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(300)
        self._reload_timer.timeout.connect(self._request_map_tiles)

        self._scale = 3.0
        self._offset = QPointF(0, 0)
        self._hover_idx = -1
        self._drag_idx = -1
        self._pan_active = False
        self._pan_start = None
        self._offset_start = None
        self._start_drag = False
        self.setMouseTracking(True)
        self.setMinimumSize(400, 400)
        self.setFocusPolicy(Qt.StrongFocus)

        # Road edge cache for snap & road strip
        self._road_hw = 3.0  # half road width
        self._road_edges_dirty = True
        self._road_left_edge = []
        self._road_right_edge = []
        self._snap_preview = None
        self.points_changed.connect(self._invalidate_road_edges)

    # -- Public API --

    def set_layers(self, layers):
        self._layers = layers
        self._active_layer_idx = 0 if layers else -1
        self._road_edges_dirty = True
        self._road_left_edge = []
        self._road_right_edge = []
        self._snap_preview = None
        self._auto_fit()
        self.update()

    def get_layers(self):
        return self._layers

    def add_layer(self, layer):
        self._layers.append(layer)
        self._active_layer_idx = len(self._layers) - 1
        self.update()

    def remove_layer(self, idx):
        if 0 <= idx < len(self._layers):
            self._layers.pop(idx)
            if self._active_layer_idx >= len(self._layers):
                self._active_layer_idx = len(self._layers) - 1
            self.update()

    def set_active_layer(self, idx):
        if 0 <= idx < len(self._layers):
            self._active_layer_idx = idx
            self._hover_idx = -1
            self._drag_idx = -1
            self._snap_preview = None
            self.update()

    def get_active_layer(self):
        if 0 <= self._active_layer_idx < len(self._layers):
            return self._layers[self._active_layer_idx]
        return None

    def set_start(self, pos, direction):
        self._start_pos = list(pos) if pos else None
        self._start_dir = direction if direction is not None else 90.0
        self.update()

    def get_start(self):
        return self._start_pos, self._start_dir

    def set_map_center(self, lat, lon):
        self._map_center = (lat, lon)
        self._request_map_tiles()

    def get_map_center(self):
        return self._map_center

    def set_map_visible(self, visible):
        self._map_visible = visible
        self.update()

    def set_map_opacity(self, opacity):
        self._map_opacity = opacity
        self.update()

    def set_edit_mode(self, mode):
        self._edit_mode = mode
        self._drag_idx = -1
        self._hover_idx = -1
        self._start_drag = False
        self._snap_preview = None
        self.update()

    def set_road_width(self, width):
        self._road_hw = width / 2.0
        self._road_edges_dirty = True
        self._resnap_curb_points()
        self.update()

    def _invalidate_road_edges(self):
        self._road_edges_dirty = True
        self._snap_preview = None
        # Re-snap curb points when road geometry changes
        active = self.get_active_layer()
        if active and active.layer_type == "road":
            self._resnap_curb_points()

    def _ensure_road_edges(self):
        if not self._road_edges_dirty:
            return
        self._road_edges_dirty = False
        self._road_left_edge = []
        self._road_right_edge = []
        road = None
        for layer in self._layers:
            if layer.layer_type == "road" and layer.closed and len(layer.points) >= 3:
                road = layer
                break
        if not road:
            return
        spline = interpolate_centerline(road.points, pts_per_seg=20)
        n = len(spline)
        if n < 2:
            return
        hw = self._road_hw
        for i in range(n):
            x0, y0 = spline[i]
            x1, y1 = spline[(i + 1) % n]
            tx, ty = x1 - x0, y1 - y0
            length = math.hypot(tx, ty)
            if length < 1e-9:
                continue
            nx, ny = -ty / length, tx / length
            self._road_left_edge.append((x0 + nx * hw, y0 + ny * hw))
            self._road_right_edge.append((x0 - nx * hw, y0 - ny * hw))

    def _nearest_road_edge_point(self, wx, wy):
        self._ensure_road_edges()
        if not self._road_left_edge:
            return None
        best_d2 = float('inf')
        best = None
        for ex, ey in self._road_left_edge:
            d2 = (ex - wx) ** 2 + (ey - wy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best = (ex, ey)
        for ex, ey in self._road_right_edge:
            d2 = (ex - wx) ** 2 + (ey - wy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best = (ex, ey)
        return best

    def _resnap_curb_points(self):
        self._ensure_road_edges()
        if not self._road_left_edge:
            return
        for layer in self._layers:
            if layer.layer_type != "curb" or not layer.points:
                continue
            for pt in layer.points:
                snapped = self._nearest_road_edge_point(pt[0], pt[1])
                if snapped:
                    pt[0], pt[1] = snapped

    # -- Coordinate transforms --

    def world_to_pixel(self, wx, wy):
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        px = cx + wx * self._scale + self._offset.x()
        py = cy - wy * self._scale + self._offset.y()
        return px, py

    def pixel_to_world(self, px, py):
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        wx = (px - cx - self._offset.x()) / self._scale
        wy = -(py - cy - self._offset.y()) / self._scale
        return wx, wy

    def _auto_fit(self):
        all_pts = []
        for layer in self._layers:
            all_pts.extend(layer.points)
        if not all_pts:
            self._offset = QPointF(0, 0)
            self._scale = 3.0
            return
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        margin = 40
        w = max(self.width() - margin * 2, 100)
        h = max(self.height() - margin * 2, 100)
        self._scale = min(w / span_x, h / span_y)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        self._offset = QPointF(-center_x * self._scale, center_y * self._scale)

    # -- Hit test --

    def _point_at(self, px, py):
        layer = self.get_active_layer()
        if not layer:
            return -1
        for i, p in enumerate(layer.points):
            ppx, ppy = self.world_to_pixel(p[0], p[1])
            if (ppx - px) ** 2 + (ppy - py) ** 2 < self.GRAB_RADIUS ** 2:
                return i
        return -1

    def _track_centroid(self):
        """Compute centroid of all layer points."""
        all_pts = [pt for layer in self._layers for pt in layer.points]
        if not all_pts:
            return 0.0, 0.0
        cx = sum(p[0] for p in all_pts) / len(all_pts)
        cy = sum(p[1] for p in all_pts) / len(all_pts)
        return cx, cy

    # -- Mouse events --

    def mousePressEvent(self, event):
        px, py = event.x(), event.y()
        # Pan: middle button or shift+left
        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier
        ):
            self._pan_active = True
            self._pan_start = QPointF(px, py)
            self._offset_start = QPointF(self._offset)
            return

        if self._edit_mode == "points":
            self._mouse_press_points(event, px, py)
        elif self._edit_mode == "start":
            self._mouse_press_start(event, px, py)
        elif self._edit_mode == "move":
            if event.button() == Qt.LeftButton:
                self._move_drag = True
                self._move_last = (px, py)
            elif event.button() == Qt.RightButton:
                self._rotate_drag = True
                cx, cy = self._track_centroid()
                cpx, cpy = self.world_to_pixel(cx, cy)
                self._rotate_last_angle = math.atan2(py - cpy, px - cpx)

    def _mouse_press_points(self, event, px, py):
        layer = self.get_active_layer()
        if not layer:
            return
        if event.button() == Qt.LeftButton:
            idx = self._point_at(px, py)
            if idx >= 0:
                self._drag_idx = idx
            else:
                wx, wy = self.pixel_to_world(px, py)
                if layer.layer_type == "curb":
                    snapped = self._nearest_road_edge_point(wx, wy)
                    if snapped:
                        wx, wy = snapped
                layer.points.append([wx, wy])
                layer.elevation = []
                self._drag_idx = len(layer.points) - 1
                self.points_changed.emit()
                self.update()
        elif event.button() == Qt.RightButton:
            idx = self._point_at(px, py)
            if idx >= 0:
                layer.points.pop(idx)
                layer.elevation = []
                self._hover_idx = -1
                self.points_changed.emit()
                self.update()

    def _mouse_press_start(self, event, px, py):
        if event.button() == Qt.LeftButton:
            wx, wy = self.pixel_to_world(px, py)
            self._start_pos = [wx, wy]
            self.points_changed.emit()
            self.update()
        elif event.button() == Qt.RightButton:
            if self._start_pos:
                self._start_drag = True

    def mouseReleaseEvent(self, event):
        if self._edit_mode == "points" and self._drag_idx >= 0:
            layer = self.get_active_layer()
            if layer:
                layer.elevation = []
            self._drag_idx = -1
            self.points_changed.emit()
        if self._edit_mode == "move" and (self._move_drag or self._rotate_drag):
            self._move_drag = False
            self._move_last = None
            self._rotate_drag = False
            self.points_changed.emit()
        if self._pan_active:
            self._pan_active = False
            self._reload_timer.start()
        self._start_drag = False

    def mouseMoveEvent(self, event):
        px, py = event.x(), event.y()
        if self._pan_active and self._pan_start:
            dx = px - self._pan_start.x()
            dy = py - self._pan_start.y()
            self._offset = QPointF(self._offset_start.x() + dx, self._offset_start.y() + dy)
            self.update()
            return
        if self._edit_mode == "move" and self._move_drag and self._move_last:
            wx_old, wy_old = self.pixel_to_world(self._move_last[0], self._move_last[1])
            wx_new, wy_new = self.pixel_to_world(px, py)
            dx = wx_new - wx_old
            dy = wy_new - wy_old
            for layer in self._layers:
                for pt in layer.points:
                    pt[0] += dx
                    pt[1] += dy
            if self._start_pos:
                self._start_pos[0] += dx
                self._start_pos[1] += dy
            self._move_last = (px, py)
            self.update()
            return
        if self._edit_mode == "move" and self._rotate_drag:
            cx, cy = self._track_centroid()
            cpx, cpy = self.world_to_pixel(cx, cy)
            angle = math.atan2(py - cpy, px - cpx)
            da = angle - self._rotate_last_angle
            cos_a, sin_a = math.cos(da), math.sin(da)
            for layer in self._layers:
                for pt in layer.points:
                    rx, ry = pt[0] - cx, pt[1] - cy
                    pt[0] = cx + rx * cos_a - ry * sin_a
                    pt[1] = cy + rx * sin_a + ry * cos_a
            if self._start_pos:
                rx, ry = self._start_pos[0] - cx, self._start_pos[1] - cy
                self._start_pos[0] = cx + rx * cos_a - ry * sin_a
                self._start_pos[1] = cy + rx * sin_a + ry * cos_a
                self._start_dir += math.degrees(da)
            self._rotate_last_angle = angle
            self.update()
            return
        if self._edit_mode == "points":
            self._mouse_move_points(px, py)
        elif self._edit_mode == "start" and self._start_drag and self._start_pos:
            wx, wy = self.pixel_to_world(px, py)
            self._start_dir = math.degrees(math.atan2(wy - self._start_pos[1], wx - self._start_pos[0]))
            self.update()

    def _mouse_move_points(self, px, py):
        layer = self.get_active_layer()
        if not layer:
            return
        if self._drag_idx >= 0:
            wx, wy = self.pixel_to_world(px, py)
            if layer.layer_type == "curb":
                snapped = self._nearest_road_edge_point(wx, wy)
                if snapped:
                    wx, wy = snapped
            layer.points[self._drag_idx] = [wx, wy]
            if layer.layer_type == "road":
                self._road_edges_dirty = True
            self.update()
            return
        old_hover = self._hover_idx
        self._hover_idx = self._point_at(px, py)
        old_snap = self._snap_preview
        if layer.layer_type == "curb" and self._hover_idx < 0:
            wx, wy = self.pixel_to_world(px, py)
            self._snap_preview = self._nearest_road_edge_point(wx, wy)
        else:
            self._snap_preview = None
        if old_hover != self._hover_idx or old_snap != self._snap_preview:
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        px, py = event.x(), event.y()
        wx, wy = self.pixel_to_world(px, py)
        self._scale *= factor
        self._scale = max(0.1, min(self._scale, 200.0))
        new_px, new_py = self.world_to_pixel(wx, wy)
        self._offset += QPointF(px - new_px, py - new_py)
        self.update()
        self._reload_timer.start()

    # -- Paint --

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Background
        p.fillRect(self.rect(), QColor(35, 35, 42))

        # Map tiles
        self._draw_map_tiles(p)

        # Grid
        self._draw_grid(p)

        # All visible layers
        for i, layer in enumerate(self._layers):
            if layer.visible:
                self._draw_layer(p, layer, i == self._active_layer_idx)

        # Snap preview ghost point
        if self._snap_preview:
            sx, sy = self.world_to_pixel(self._snap_preview[0], self._snap_preview[1])
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.setBrush(QBrush(QColor(255, 160, 40, 120)))
            p.drawEllipse(QPointF(sx, sy), 7, 7)

        # Start marker
        self._draw_start_marker(p)

        p.end()

    def _latlon_to_world(self, lat, lon):
        """Convert lat/lon to world coordinates (meters from map center)."""
        if not self._map_center:
            return 0.0, 0.0
        clat, clon = self._map_center
        wx = (lon - clon) * 111320.0 * math.cos(math.radians(clat))
        wy = (lat - clat) * 111320.0
        return wx, wy

    def _world_to_latlon(self, wx, wy):
        """Convert world coordinates (meters from map center) to lat/lon."""
        if not self._map_center:
            return 0.0, 0.0
        clat, clon = self._map_center
        lat = clat + wy / 111320.0
        lon = clon + wx / (111320.0 * math.cos(math.radians(clat)))
        return lat, lon

    def _tile_zoom(self):
        """Compute tile zoom level matching the current canvas scale."""
        if not self._map_center:
            return MAP_ZOOM_DEFAULT
        cos_lat = math.cos(math.radians(self._map_center[0]))
        val = 40075017.0 * cos_lat * self._scale / 256.0
        if val <= 1:
            return MAP_ZOOM_DEFAULT
        z = int(round(math.log2(val)))
        return max(1, min(z, 19))

    def _request_map_tiles(self):
        """Request tiles covering the current visible area."""
        if not self._map_center:
            return
        clat, clon = self._map_center
        cos_lat = math.cos(math.radians(clat))
        view_wx, view_wy = self.pixel_to_world(self.width() / 2, self.height() / 2)
        view_lat = clat + view_wy / 111320.0
        view_lon = clon + view_wx / (111320.0 * cos_lat)
        z = self._tile_zoom()
        cx, cy = _latlon_to_tile(view_lat, view_lon, z)
        for dx in range(-MAP_RADIUS, MAP_RADIUS + 1):
            for dy in range(-MAP_RADIUS, MAP_RADIUS + 1):
                self._tile_fetcher.request_tile(z, cx + dx, cy + dy)
        self._compose_map_image()

    def _compose_map_image(self):
        """Compose downloaded tiles into a single QImage."""
        if not self._map_center:
            return
        clat, clon = self._map_center
        cos_lat = math.cos(math.radians(clat))
        view_wx, view_wy = self.pixel_to_world(self.width() / 2, self.height() / 2)
        view_lat = clat + view_wy / 111320.0
        view_lon = clon + view_wx / (111320.0 * cos_lat)
        z = self._tile_zoom()
        cx, cy = _latlon_to_tile(view_lat, view_lon, z)
        grid = 2 * MAP_RADIUS + 1
        tile_px = 256
        img = QImage(grid * tile_px, grid * tile_px, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        loaded = 0
        for dx in range(-MAP_RADIUS, MAP_RADIUS + 1):
            for dy in range(-MAP_RADIUS, MAP_RADIUS + 1):
                tx, ty = cx + dx, cy + dy
                cache_path = os.path.join(TILE_CACHE_DIR, str(z), str(tx), f"{ty}.png")
                if os.path.isfile(cache_path):
                    tile_img = QImage(cache_path)
                    if not tile_img.isNull():
                        ix = (dx + MAP_RADIUS) * tile_px
                        iy = (dy + MAP_RADIUS) * tile_px
                        p.drawImage(ix, iy, tile_img)
                        loaded += 1
        p.end()
        self._map_image = img if loaded > 0 else None

        # Compute world coords of the composed image corners
        tx_min = cx - MAP_RADIUS
        ty_min = cy - MAP_RADIUS
        tx_max = cx + MAP_RADIUS + 1
        ty_max = cy + MAP_RADIUS + 1
        lat_tl, lon_tl = _tile_to_latlon(tx_min, ty_min, z)
        lat_br, lon_br = _tile_to_latlon(tx_max, ty_max, z)
        wx_tl, wy_tl = self._latlon_to_world(lat_tl, lon_tl)
        wx_br, wy_br = self._latlon_to_world(lat_br, lon_br)
        self._map_world_origin = (wx_tl, wy_tl)
        self._map_world_size = (wx_br - wx_tl, wy_br - wy_tl)
        self.update()

    def _draw_map_tiles(self, painter):
        if not self._map_center or not self._map_visible or self._map_image is None:
            return
        ox, oy = self._map_world_origin
        sw, sh = self._map_world_size
        px_tl_x, px_tl_y = self.world_to_pixel(ox, oy)
        px_br_x, px_br_y = self.world_to_pixel(ox + sw, oy + sh)
        target = QRectF(px_tl_x, px_tl_y, px_br_x - px_tl_x, px_br_y - px_tl_y)
        painter.setOpacity(self._map_opacity)
        painter.drawImage(target, self._map_image)
        painter.setOpacity(1.0)

    def _on_tile_ready(self, z, x, y):
        # Debounce: restart timer on each tile arrival
        self._compose_timer.start()

    def _draw_grid(self, p):
        target_px = 60
        raw_step = target_px / max(self._scale, 0.01)
        mag = 10 ** math.floor(math.log10(max(raw_step, 0.01)))
        for nice in [1, 2, 5, 10]:
            step = mag * nice
            if step * self._scale >= target_px:
                break

        p.setPen(QPen(QColor(55, 55, 60), 1))
        w, h = self.width(), self.height()
        wl, wt = self.pixel_to_world(0, 0)
        wr, wb = self.pixel_to_world(w, h)
        min_x = math.floor(min(wl, wr) / step) * step
        max_x = math.ceil(max(wl, wr) / step) * step
        min_y = math.floor(min(wt, wb) / step) * step
        max_y = math.ceil(max(wt, wb) / step) * step

        x = min_x
        while x <= max_x:
            px, _ = self.world_to_pixel(x, 0)
            p.drawLine(int(px), 0, int(px), h)
            x += step
        y = min_y
        while y <= max_y:
            _, py = self.world_to_pixel(0, y)
            p.drawLine(0, int(py), w, int(py))
            y += step

        ax, _ = self.world_to_pixel(0, 0)
        _, ay = self.world_to_pixel(0, 0)
        p.setPen(QPen(QColor(100, 50, 50), 1))
        p.drawLine(int(ax), 0, int(ax), h)
        p.setPen(QPen(QColor(50, 100, 50), 1))
        p.drawLine(0, int(ay), w, int(ay))

    def _draw_layer(self, p, layer, is_active):
        color = LAYER_COLORS.get(layer.layer_type, QColor(200, 200, 200))
        dim_color = LAYER_COLORS_DIM.get(layer.layer_type, QColor(200, 200, 200, 80))

        # Draw road strip (semi-transparent width polygon)
        pts = layer.points
        if layer.layer_type == "road" and layer.closed and len(pts) >= 3:
            self._ensure_road_edges()
            left = self._road_left_edge
            right = self._road_right_edge
            if left and right:
                poly = QPolygonF()
                for lx, ly in left:
                    px_l, py_l = self.world_to_pixel(lx, ly)
                    poly.append(QPointF(px_l, py_l))
                for rx, ry in reversed(right):
                    px_r, py_r = self.world_to_pixel(rx, ry)
                    poly.append(QPointF(px_r, py_r))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(80, 180, 255, 30)))
                p.drawPolygon(poly)
                # Edge lines
                edge_alpha = 100 if is_active else 40
                edge_pen = QPen(QColor(80, 180, 255, edge_alpha), 1)
                p.setPen(edge_pen)
                for edge in (left, right):
                    for i in range(len(edge) - 1):
                        x0, y0 = self.world_to_pixel(edge[i][0], edge[i][1])
                        x1, y1 = self.world_to_pixel(edge[i + 1][0], edge[i + 1][1])
                        p.drawLine(int(x0), int(y0), int(x1), int(y1))
                    # Close loop
                    if len(edge) >= 2:
                        x0, y0 = self.world_to_pixel(edge[-1][0], edge[-1][1])
                        x1, y1 = self.world_to_pixel(edge[0][0], edge[0][1])
                        p.drawLine(int(x0), int(y0), int(x1), int(y1))

        # Draw spline
        if layer.closed and len(pts) >= 3:
            spline = interpolate_centerline(pts, pts_per_seg=20)
            if len(spline) >= 2:
                pen = QPen(color if is_active else dim_color, 2 if is_active else 1)
                p.setPen(pen)
                for i in range(len(spline)):
                    x0, y0 = self.world_to_pixel(spline[i][0], spline[i][1])
                    x1, y1 = self.world_to_pixel(spline[(i + 1) % len(spline)][0], spline[(i + 1) % len(spline)][1])
                    p.drawLine(int(x0), int(y0), int(x1), int(y1))
        elif not layer.closed and len(pts) >= 2:
            spline = interpolate_open(pts, pts_per_seg=20)
            if len(spline) >= 2:
                pen = QPen(color if is_active else dim_color, 2 if is_active else 1)
                p.setPen(pen)
                for i in range(len(spline) - 1):
                    x0, y0 = self.world_to_pixel(spline[i][0], spline[i][1])
                    x1, y1 = self.world_to_pixel(spline[i + 1][0], spline[i + 1][1])
                    p.drawLine(int(x0), int(y0), int(x1), int(y1))
        elif len(pts) >= 2:
            pen = QPen((color if is_active else dim_color), 1, Qt.DashLine)
            p.setPen(pen)
            for i in range(len(pts) - 1):
                x0, y0 = self.world_to_pixel(pts[i][0], pts[i][1])
                x1, y1 = self.world_to_pixel(pts[i + 1][0], pts[i + 1][1])
                p.drawLine(int(x0), int(y0), int(x1), int(y1))

        # Draw control points
        for i, pt in enumerate(pts):
            px, py = self.world_to_pixel(pt[0], pt[1])
            if is_active:
                is_hover = (i == self._hover_idx)
                r = self.POINT_RADIUS + 2 if is_hover else self.POINT_RADIUS
                pt_color = QColor(255, 200, 60) if is_hover else color
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setBrush(QBrush(pt_color))
                p.drawEllipse(QPointF(px, py), r, r)
                p.setPen(QColor(200, 200, 200))
                p.setFont(QFont("Monospace", 7))
                p.drawText(int(px + r + 3), int(py - r), str(i))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(dim_color))
                p.drawEllipse(QPointF(px, py), 3, 3)

    def _draw_start_marker(self, p):
        if not self._start_pos:
            return
        sx, sy = self.world_to_pixel(self._start_pos[0], self._start_pos[1])
        r = 10
        p.setPen(QPen(START_COLOR, 2))
        p.setBrush(QBrush(QColor(60, 220, 60, 80)))
        p.drawEllipse(QPointF(sx, sy), r, r)
        # Direction arrow
        rad = math.radians(self._start_dir)
        arrow_len = 25
        ax = sx + arrow_len * math.cos(rad)
        # Flip y because screen y is inverted
        ay = sy - arrow_len * math.sin(rad)
        p.setPen(QPen(START_COLOR, 3))
        p.drawLine(int(sx), int(sy), int(ax), int(ay))
        # Arrowhead
        head = 8
        for angle_off in [150, -150]:
            hr = math.radians(self._start_dir + angle_off)
            hx = ax + head * math.cos(hr)
            hy = ay - head * math.sin(hr)
            p.drawLine(int(ax), int(ay), int(hx), int(hy))


class TrackEditorPanel(QWidget):
    """Tab wrapper for the multi-layer layout editor."""

    map_center_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track_root = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # -- Toolbar --
        toolbar = QHBoxLayout()
        btn_save = QPushButton("Salva")
        btn_save.setStyleSheet(
            "QPushButton { background: #2a6e2a; padding: 6px 16px; }"
            "QPushButton:hover { background: #358535; }"
        )
        btn_save.setToolTip("Salva il layout (layers, partenza, mappa) nel file centerline.json")
        btn_save.clicked.connect(self._save)
        toolbar.addWidget(btn_save)

        btn_load = QPushButton("Ricarica")
        btn_load.setToolTip("Ricarica il layout dal file centerline.json (scarta modifiche non salvate)")
        btn_load.clicked.connect(self._reload)
        toolbar.addWidget(btn_load)

        toolbar.addSpacing(20)

        # Edit mode radio buttons
        toolbar.addWidget(QLabel("Modalita:"))
        self._mode_group = QButtonGroup(self)
        mode_tooltips = {
            "points": "Modifica i punti di controllo del layer attivo (click sx = aggiungi/trascina, click dx = elimina)",
            "start": "Posiziona il punto di partenza (click sx) e ruota la direzione (click dx + trascina)",
            "move": "Click sx + trascina = sposta pista, Click dx + trascina = ruota pista",
        }
        for mode_id, label in [("points", "Punti"), ("start", "Partenza"), ("move", "Sposta")]:
            rb = QRadioButton(label)
            rb.setToolTip(mode_tooltips[mode_id])
            self._mode_group.addButton(rb)
            rb.setProperty("mode_id", mode_id)
            if mode_id == "points":
                rb.setChecked(True)
            toolbar.addWidget(rb)
        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        toolbar.addStretch()
        self.status_label = QLabel("")
        toolbar.addWidget(self.status_label)
        main_layout.addLayout(toolbar)

        # -- Body: canvas + sidebar --
        body = QHBoxLayout()

        self.canvas = TrackCanvas()
        self.canvas.points_changed.connect(self._on_points_changed)
        body.addWidget(self.canvas, stretch=1)

        # -- Sidebar --
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(4, 0, 0, 0)

        layers_header = QHBoxLayout()
        layers_header.addWidget(QLabel("LAYERS"))
        layers_header.addWidget(make_info_label("Gestisci i layer del tracciato: road (carreggiata), curb (cordoli), wall (barriere)"))
        layers_header.addStretch()
        sidebar.addLayout(layers_header)

        # Add layer buttons
        add_row = QHBoxLayout()
        btn_add_road = QPushButton("+ Road")
        btn_add_road.setStyleSheet("QPushButton { background: #2a5a8a; padding: 4px 8px; }")
        btn_add_road.setToolTip("Aggiungi il layer carreggiata (uno solo per pista, circuito chiuso)")
        btn_add_road.clicked.connect(lambda: self._add_layer("road"))
        add_row.addWidget(btn_add_road)
        btn_add_curb = QPushButton("+ Curb")
        btn_add_curb.setStyleSheet("QPushButton { background: #8a4a1a; padding: 4px 8px; }")
        btn_add_curb.setToolTip("Aggiungi un layer cordolo (polyline aperta lungo il bordo strada)")
        btn_add_curb.clicked.connect(lambda: self._add_layer("curb"))
        add_row.addWidget(btn_add_curb)
        btn_add_wall = QPushButton("+ Wall")
        btn_add_wall.setStyleSheet("QPushButton { background: #5a5a5a; padding: 4px 8px; }")
        btn_add_wall.setToolTip("Aggiungi un layer barriera (polyline aperta, genera muri 3D)")
        btn_add_wall.clicked.connect(lambda: self._add_layer("wall"))
        add_row.addWidget(btn_add_wall)
        sidebar.addLayout(add_row)

        # Layer list
        self.layer_list = QListWidget()
        self.layer_list.setMaximumWidth(220)
        self.layer_list.setMinimumWidth(180)
        self.layer_list.currentRowChanged.connect(self._on_layer_selected)
        sidebar.addWidget(self.layer_list)

        # Layer actions
        action_row = QHBoxLayout()
        btn_rename = QPushButton("Rinomina")
        btn_rename.setToolTip("Rinomina il layer selezionato")
        btn_rename.clicked.connect(self._rename_layer)
        action_row.addWidget(btn_rename)
        btn_delete = QPushButton("Elimina")
        btn_delete.setStyleSheet(
            "QPushButton { background: #6a2a2a; padding: 4px 8px; }"
            "QPushButton:hover { background: #8b3535; }"
        )
        btn_delete.setToolTip("Elimina il layer selezionato e tutti i suoi punti")
        btn_delete.clicked.connect(self._delete_layer)
        action_row.addWidget(btn_delete)
        sidebar.addLayout(action_row)

        # Layer info
        self.chk_visible = QCheckBox("Visibile")
        self.chk_visible.setChecked(True)
        self.chk_visible.setToolTip("Mostra/nascondi il layer selezionato nel canvas")
        self.chk_visible.toggled.connect(self._on_visible_toggled)
        sidebar.addWidget(self.chk_visible)
        self.layer_info_label = QLabel("Punti: 0")
        sidebar.addWidget(self.layer_info_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #555;")
        sidebar.addWidget(sep)

        # Map section
        map_header = QHBoxLayout()
        map_header.addWidget(QLabel("MAPPA"))
        map_header.addWidget(make_info_label(
            "Mappa satellitare come riferimento per disegnare il tracciato.\n"
            "Cerca una località per posizionare la mappa."
        ))
        map_header.addStretch()
        sidebar.addLayout(map_header)

        # Search bar
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Cerca località...")
        self.search_edit.setToolTip("Inserisci il nome del luogo (es. 'Kartodromo di Casaluce')")
        self.search_edit.returnPressed.connect(self._search_location)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self.search_edit, stretch=1)
        btn_search = QPushButton("Cerca")
        btn_search.setStyleSheet(
            "QPushButton { background: #2a5a8a; padding: 4px 10px; }"
            "QPushButton:hover { background: #3570a0; }"
        )
        btn_search.setToolTip("Cerca la località e centra la mappa")
        btn_search.clicked.connect(self._search_location)
        search_row.addWidget(btn_search)
        sidebar.addLayout(search_row)

        # Search suggestions dropdown
        self._suggest_list = QListWidget()
        self._suggest_list.setMaximumHeight(150)
        self._suggest_list.setStyleSheet(
            "QListWidget { background: #2a2a30; border: 1px solid #555; font-size: 9pt; }"
            "QListWidget::item { padding: 4px; }"
            "QListWidget::item:hover { background: #3a3a45; }"
        )
        self._suggest_list.hide()
        self._suggest_list.itemClicked.connect(self._on_suggest_clicked)
        sidebar.addWidget(self._suggest_list)
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(500)
        self._suggest_timer.timeout.connect(self._fetch_suggestions)
        self._suggest_results = []

        # Visibility checkbox
        self.chk_map_visible = QCheckBox("Mostra mappa")
        self.chk_map_visible.setChecked(True)
        self.chk_map_visible.setToolTip("Mostra/nascondi la mappa di sfondo nel canvas")
        self.chk_map_visible.toggled.connect(self._on_map_visible_changed)
        sidebar.addWidget(self.chk_map_visible)

        # Opacity slider
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacità:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 80)
        self.opacity_slider.setValue(50)
        self.opacity_slider.setToolTip("Regola la trasparenza della mappa (20%-80%)")
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self.opacity_slider)
        sidebar.addLayout(opacity_row)

        # Coordinate label
        self.coord_label = QLabel("")
        self.coord_label.setStyleSheet("color: #888; font-size: 8pt;")
        sidebar.addWidget(self.coord_label)

        # --- Elevation section ---
        sep_elev = QFrame()
        sep_elev.setFrameShape(QFrame.HLine)
        sep_elev.setFrameShadow(QFrame.Sunken)
        sidebar.addWidget(sep_elev)
        sidebar.addWidget(QLabel("<b>Elevation</b>"))

        self.btn_fetch_elev = QPushButton("Fetch Elevation (SRTM)")
        self.btn_fetch_elev.setToolTip(
            "Scarica i dati di elevazione SRTM per tutti i punti di controllo")
        self.btn_fetch_elev.clicked.connect(self._on_fetch_elevation)
        sidebar.addWidget(self.btn_fetch_elev)

        self.elev_stats_label = QLabel("")
        self.elev_stats_label.setStyleSheet("color: #aaa; font-size: 8pt;")
        sidebar.addWidget(self.elev_stats_label)

        self.btn_reset_elev = QPushButton("Reset Elevation")
        self.btn_reset_elev.setToolTip(
            "Rimuovi i dati di elevazione da tutti i layer (pista piatta)")
        self.btn_reset_elev.clicked.connect(self._on_reset_elevation)
        sidebar.addWidget(self.btn_reset_elev)

        sidebar.addStretch()
        body.addLayout(sidebar)
        main_layout.addLayout(body, stretch=1)

    def load_track(self, track_root, config=None):
        self._track_root = track_root

        # Extract road_width from config (or read track_config.json for _reload)
        if config is None:
            cfg_path = os.path.join(track_root, "track_config.json")
            if os.path.isfile(cfg_path):
                with open(cfg_path, encoding="utf-8") as f:
                    config = json.load(f)
            else:
                config = {}
        geo = config.get("geometry", {})
        road_width = geo.get("road_width", _def_geo.get("road_width", 6.0))
        self.canvas.set_road_width(road_width)

        cl_path = os.path.join(track_root, "centerline.json")
        try:
            data = load_centerline_v2(cl_path)
            layers = [TrackLayer.from_dict(d) for d in data.get("layers", [])]
            self.canvas.set_layers(layers)

            # Start
            start = data.get("start")
            if start and start.get("position"):
                self.canvas.set_start(start["position"], start.get("direction", 90.0))
            else:
                self.canvas.set_start(None, 90.0)

            # Map center
            mc = data.get("map_center")
            if mc and len(mc) == 2:
                self.canvas.set_map_center(mc[0], mc[1])
                self.coord_label.setText(f"{mc[0]:.4f}, {mc[1]:.4f}")
            else:
                self.canvas._map_center = None
                self.coord_label.setText("")

            # Restore search text
            self.search_edit.setText(data.get("map_search") or "")

            self._refresh_layer_list()
            n_layers = len(layers)
            total_pts = sum(len(l.points) for l in layers)
            flash_status(self.status_label, f"Caricato: {n_layers} layer, {total_pts} punti", "#55cc55")
        except Exception as e:
            self.canvas.set_layers([])
            flash_status(self.status_label, f"Errore: {e}", "#ff5555")

    def _save(self):
        if not self._track_root:
            flash_status(self.status_label, "Nessuna pista selezionata", "#e8a838")
            return
        layers = self.canvas.get_layers()
        start_pos, start_dir = self.canvas.get_start()
        mc = self.canvas.get_map_center()

        start_data = None
        if start_pos:
            start_data = {"position": start_pos, "direction": start_dir}

        search_text = self.search_edit.text().strip()
        data = {
            "layers": [l.to_dict() for l in layers],
            "start": start_data,
            "map_center": list(mc) if mc else None,
            "map_search": search_text or None,
        }
        cl_path = os.path.join(self._track_root, "centerline.json")
        try:
            save_centerline_v2(cl_path, data)
            total_pts = sum(len(l.points) for l in layers)
            flash_status(self.status_label, f"Salvato: {len(layers)} layer, {total_pts} punti", "#55cc55")
        except Exception as e:
            flash_status(self.status_label, f"Errore: {e}", "#ff5555")

    def _reload(self):
        if self._track_root:
            self.load_track(self._track_root)

    def _add_layer(self, layer_type):
        layers = self.canvas.get_layers()
        # Max 1 road
        if layer_type == "road" and any(l.layer_type == "road" for l in layers):
            QMessageBox.warning(self, "Limite", "Puoi avere solo un layer road.")
            return
        # Auto name
        existing = {l.name for l in layers}
        if layer_type == "road":
            name = "road"
        else:
            idx = 1
            while f"{layer_type}_{idx}" in existing:
                idx += 1
            name = f"{layer_type}_{idx}"
        closed = (layer_type == "road")
        layer = TrackLayer(name, layer_type, closed)
        self.canvas.add_layer(layer)
        self._refresh_layer_list()
        self.layer_list.setCurrentRow(len(layers))  # select new layer

    def _delete_layer(self):
        idx = self.layer_list.currentRow()
        if idx < 0:
            return
        layer = self.canvas.get_layers()[idx]
        reply = QMessageBox.question(
            self, "Conferma", f"Eliminare il layer '{layer.name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.canvas.remove_layer(idx)
            self._refresh_layer_list()

    def _rename_layer(self):
        idx = self.layer_list.currentRow()
        if idx < 0:
            return
        layer = self.canvas.get_layers()[idx]
        new_name, ok = QInputDialog.getText(self, "Rinomina Layer", "Nuovo nome:", text=layer.name)
        if ok and new_name.strip():
            layer.name = new_name.strip()
            self._refresh_layer_list()

    def _on_layer_selected(self, row):
        if row >= 0:
            self.canvas.set_active_layer(row)
            self._update_layer_info()

    def _on_visible_toggled(self, checked):
        layer = self.canvas.get_active_layer()
        if layer:
            layer.visible = checked
            self.canvas.update()

    def _on_points_changed(self):
        self._update_layer_info()

    def _on_mode_changed(self, button):
        mode = button.property("mode_id")
        self.canvas.set_edit_mode(mode)

    def _update_layer_info(self):
        layer = self.canvas.get_active_layer()
        if layer:
            kind = "chiuso" if layer.closed else "aperto"
            self.layer_info_label.setText(f"Punti: {len(layer.points)} | {kind}")
            self.chk_visible.setChecked(layer.visible)
        else:
            self.layer_info_label.setText("Punti: 0")

    def _refresh_layer_list(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        prefix_map = {"road": "[R]", "curb": "[C]", "wall": "[W]"}
        for i, layer in enumerate(self.canvas.get_layers()):
            prefix = prefix_map.get(layer.layer_type, "[?]")
            label = f"{prefix} {layer.name} ({len(layer.points)}p)"
            item = QListWidgetItem(label)
            color = LAYER_COLORS.get(layer.layer_type, QColor(200, 200, 200))
            item.setForeground(color)
            self.layer_list.addItem(item)
        self.layer_list.blockSignals(False)
        # Select active layer
        active = self.canvas._active_layer_idx
        if 0 <= active < self.layer_list.count():
            self.layer_list.setCurrentRow(active)
        self._update_layer_info()

    def _search_location(self):
        query = self.search_edit.text().strip()
        if not query:
            return
        try:
            from geopy.geocoders import Nominatim
            geolocator = Nominatim(user_agent="ac-track-manager")
            location = geolocator.geocode(query)
            if location:
                lat, lon = location.latitude, location.longitude
                self.canvas.set_map_center(lat, lon)
                self.coord_label.setText(f"{lat:.4f}, {lon:.4f}")
                self.map_center_changed.emit(lat, lon)
                addr = location.address
                if len(addr) > 40:
                    addr = addr[:40] + "..."
                flash_status(self.status_label, f"Trovato: {addr}", "#55cc55")
            else:
                flash_status(self.status_label, "Località non trovata", "#e8a838")
        except Exception as e:
            flash_status(self.status_label, f"Errore geocoding: {e}", "#ff5555")

    def _on_search_text_changed(self, text):
        if len(text.strip()) >= 3:
            self._suggest_timer.start()
        else:
            self._suggest_list.hide()

    def _fetch_suggestions(self):
        query = self.search_edit.text().strip()
        if len(query) < 3:
            self._suggest_list.hide()
            return
        try:
            from geopy.geocoders import Nominatim
            geolocator = Nominatim(user_agent="ac-track-manager")
            results = geolocator.geocode(query, exactly_one=False, limit=5)
            self._suggest_results = results or []
            self._suggest_list.clear()
            if not self._suggest_results:
                self._suggest_list.hide()
                return
            for loc in self._suggest_results:
                addr = loc.address
                if len(addr) > 60:
                    addr = addr[:60] + "..."
                self._suggest_list.addItem(addr)
            self._suggest_list.show()
        except Exception:
            self._suggest_list.hide()

    def _on_suggest_clicked(self, item):
        idx = self._suggest_list.row(item)
        if 0 <= idx < len(self._suggest_results):
            loc = self._suggest_results[idx]
            lat, lon = loc.latitude, loc.longitude
            self.search_edit.setText(loc.address if len(loc.address) <= 60 else loc.address[:60])
            self.canvas.set_map_center(lat, lon)
            self.coord_label.setText(f"{lat:.4f}, {lon:.4f}")
            self.map_center_changed.emit(lat, lon)
            flash_status(self.status_label, f"Trovato: {item.text()}", "#55cc55")
        self._suggest_list.hide()


    def _on_map_visible_changed(self, checked):
        self.canvas.set_map_visible(checked)

    def _on_opacity_changed(self, value):
        self.canvas.set_map_opacity(value / 100.0)

    def _on_fetch_elevation(self):
        """Fetch SRTM elevation for all layers' control points."""
        map_center = self.canvas._map_center
        if not map_center:
            flash_status(self.status_label,
                         "Nessun map_center — cerca una località prima", "#e8a838")
            return

        layers = self.canvas._layers
        if not layers:
            flash_status(self.status_label, "Nessun layer", "#e8a838")
            return

        total_pts = sum(len(l.points) for l in layers)
        flash_status(self.status_label,
                     f"Fetching SRTM per {total_pts} punti...", "#5599ff")
        QApplication.processEvents()

        failed = 0
        all_smoothed = []
        for layer in layers:
            if not layer.points:
                all_smoothed.append([])
                continue
            raw = _fetch_srtm_elevation(layer.points, map_center)
            n_failed = sum(1 for z in raw if z == 0.0)
            failed += n_failed
            smoothed = _smooth_elevation(raw, layer.closed)
            all_smoothed.append(smoothed)

        # Global minimum across ALL layers for spatial coherence
        all_values = [z for s in all_smoothed for z in s]
        global_min = min(all_values) if all_values else 0.0

        for layer, smoothed in zip(layers, all_smoothed):
            if not smoothed:
                continue
            layer.elevation = [z - global_min for z in smoothed]

        # Update stats label
        road = next((l for l in layers if l.layer_type == "road"), None)
        if road and road.elevation:
            e_min = min(road.elevation)
            e_max = max(road.elevation)
            delta = e_max - e_min
            self.elev_stats_label.setText(
                f"Min: {e_min:.1f}m  Max: {e_max:.1f}m  Δ: {delta:.1f}m")
        else:
            self.elev_stats_label.setText("")

        self._save()
        msg = f"Elevation fetched ({total_pts} punti)"
        if failed:
            msg += f" — {failed} falliti (=0.0m)"
        flash_status(self.status_label, msg, "#55cc55" if not failed else "#e8a838")

    def _update_elevation_stats(self):
        """Update the elevation stats label from cached data."""
        layers = self.canvas._layers if hasattr(self.canvas, '_layers') else []
        road = next((l for l in layers if l.layer_type == "road"), None)
        if road and road.elevation:
            e_min = min(road.elevation)
            e_max = max(road.elevation)
            self.elev_stats_label.setText(
                f"Min: {e_min:.1f}m  Max: {e_max:.1f}m  Δ: {e_max - e_min:.1f}m")
        else:
            self.elev_stats_label.setText("")

    def _on_reset_elevation(self):
        """Clear elevation data from all layers (flat track)."""
        layers = self.canvas._layers
        if not layers:
            return
        for layer in layers:
            layer.elevation = []
        self.elev_stats_label.setText("")
        self._save()
        flash_status(self.status_label, "Elevation rimossa da tutti i layer", "#55cc55")


# ---------------------------------------------------------------------------
# Tab 3: 3D Preview
# ---------------------------------------------------------------------------

class PreviewPanel(QWidget):
    """Tab wrapper for KN5 3D preview using TrackGLWidget."""

    # Collection classification by mesh name
    _COLLECTIONS = [
        ("Track",    lambda n: any(k in n for k in ('ROAD', 'STARTLINE')) and 'GRASS' not in n),
        ("Curbs",    lambda n: 'KERB' in n or 'CURB' in n),
        ("Grass",    lambda n: 'GRASS' in n),
        ("Barriers", lambda n: 'WALL' in n or 'BARRIER' in n),
        ("Ground",   lambda n: 'GROUND' in n or 'SAND' in n),
        ("AC Nodes", lambda n: n.startswith('AC_')),
        ("AdditionalElements", lambda n: 'GANTRY' in n),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track_root = None
        self._config = None
        self._gl_widget = None
        self._kn5_loaded = False
        self._needs_load = False

        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()
        btn_reload = QPushButton("Ricarica KN5")
        btn_reload.setStyleSheet(
            "QPushButton { background: #2a5a8a; padding: 6px 16px; }"
            "QPushButton:hover { background: #3570a0; }"
        )
        btn_reload.setToolTip("Ricarica il modello 3D KN5 dal disco (dopo un nuovo build)")
        btn_reload.clicked.connect(self._reload)
        toolbar.addWidget(btn_reload)

        btn_reset = QPushButton("Reset Camera (R)")
        btn_reset.setToolTip("Riporta la camera alla posizione iniziale centrata sul tracciato")
        btn_reset.clicked.connect(self._reset_camera)
        toolbar.addWidget(btn_reset)

        toolbar.addStretch()
        self.info_label = QLabel("")
        toolbar.addWidget(self.info_label)
        layout.addLayout(toolbar)

        # Main area: splitter with mesh tree + GL
        self.splitter = QSplitter(Qt.Horizontal)

        self.mesh_tree = QTreeWidget()
        self.mesh_tree.setHeaderHidden(True)
        self.mesh_tree.setMaximumWidth(260)
        self.mesh_tree.setStyleSheet("QTreeWidget { font-size: 9pt; }")
        self.mesh_tree.itemChanged.connect(self._on_tree_item_changed)
        self.splitter.addWidget(self.mesh_tree)

        # Placeholder
        self.placeholder = QLabel("Nessun KN5 trovato.\nEsegui Build per generare il modello 3D.")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color: #888; font-size: 11pt;")
        self.splitter.addWidget(self.placeholder)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, stretch=1)

    def load_track(self, track_root, config):
        """Store track info. Actual KN5 loading is deferred to ensure_loaded()."""
        self._track_root = track_root
        self._config = config
        self._kn5_loaded = False
        self._needs_load = True

    def ensure_loaded(self):
        """Load KN5 if not yet loaded. Called when the Preview tab becomes visible."""
        if not self._needs_load:
            return
        self._needs_load = False
        if not self._track_root or not self._config:
            return
        slug = self._config.get("slug", "track")
        kn5_path = os.path.join(self._track_root, f"{slug}.kn5")
        if os.path.isfile(kn5_path):
            self._load_kn5(kn5_path)
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        self._kn5_loaded = False
        self.mesh_tree.clear()
        self.info_label.setText("")
        if self._gl_widget:
            self._gl_widget.hide()
        self.placeholder.show()

    def _load_kn5(self, kn5_path):
        self.info_label.setText("Caricamento KN5...")
        self.info_label.setStyleSheet("color: #e8a838;")
        QApplication.processEvents()

        # Lazy import to avoid hard dependency on OpenGL at module level
        sys.path.insert(0, os.path.join(GENERATOR_DIR, "tools"))
        from track_viewer import parse_kn5, TrackGLWidget

        try:
            textures, materials, meshes = parse_kn5(kn5_path)
        except Exception as e:
            self.info_label.setText(f"Errore: {e}")
            self.info_label.setStyleSheet("")
            self._show_placeholder()
            return

        # Create GL widget lazily
        if not self._gl_widget:
            self._gl_widget = TrackGLWidget()
            self.placeholder.hide()
            self.splitter.addWidget(self._gl_widget)
            self.splitter.setStretchFactor(2, 1)
            # Defer load_scene: GL context needs a paint cycle to initialize
            QTimer.singleShot(50, lambda: self._finish_load(textures, materials, meshes, kn5_path))
            return
        else:
            self.placeholder.hide()
            self._gl_widget.show()

        self._finish_load(textures, materials, meshes, kn5_path)

    @staticmethod
    def _classify_mesh(name):
        """Return collection name for a mesh based on naming convention."""
        n = name.upper()
        for coll_name, match_fn in PreviewPanel._COLLECTIONS:
            if match_fn(n):
                return coll_name
        return "Other"

    def _finish_load(self, textures, materials, meshes, kn5_path):
        self._gl_widget.load_scene(textures, materials, meshes)
        self._kn5_loaded = True

        # Pass road centerline for direction arrows
        if self._track_root:
            cl_path = os.path.join(self._track_root, "centerline.json")
            cl_data = load_centerline_v2(cl_path)
            road_layer = next((l for l in cl_data.get("layers", []) if l["type"] == "road"), None)
            if road_layer and len(road_layer["points"]) >= 3:
                # Interpolate dense road path; 2D (x,y) → 3D (x, -z)
                dense = interpolate_centerline(road_layer["points"], pts_per_seg=20)
                # Convert to 3D coords: layout (x,y) → GL (x, -z)
                path_3d = [(p[0], -p[1]) for p in dense]
                self._gl_widget.set_direction_path(path_3d)

        # Group meshes by collection
        groups = {}  # collection_name -> list of meshes
        for m in meshes:
            coll = self._classify_mesh(m['name'])
            groups.setdefault(coll, []).append(m)

        # Populate tree
        self.mesh_tree.blockSignals(True)
        self.mesh_tree.clear()
        # Ordered: known collections first, then "Other"
        coll_order = [c[0] for c in self._COLLECTIONS] + ["Other"]
        for coll_name in coll_order:
            coll_meshes = groups.get(coll_name)
            if not coll_meshes:
                continue
            coll_tris = sum(m['tri_count'] for m in coll_meshes)
            parent_item = QTreeWidgetItem(self.mesh_tree)
            parent_item.setText(0, f"{coll_name} ({len(coll_meshes)}) [{coll_tris:,} tri]")
            parent_item.setFlags(parent_item.flags() | Qt.ItemIsUserCheckable)
            parent_item.setCheckState(0, Qt.Checked)
            parent_item.setData(0, Qt.UserRole, None)  # collection node
            parent_item.setExpanded(coll_name in ("Track", "AC Nodes"))
            for m in coll_meshes:
                child = QTreeWidgetItem(parent_item)
                child.setText(0, f"{m['name']} ({m['tri_count']} tri)")
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, m['name'])
        self.mesh_tree.blockSignals(False)

        total_tris = sum(m['tri_count'] for m in meshes)
        self.info_label.setStyleSheet("")
        self.info_label.setText(
            f"{os.path.basename(kn5_path)} | "
            f"{len(meshes)} mesh, {total_tris:,} tri"
        )

    def _reload(self):
        if self._track_root and self._config:
            self._needs_load = True
            self.ensure_loaded()

    def _reset_camera(self):
        if self._gl_widget and self._kn5_loaded:
            self._gl_widget.reset_camera()

    def _on_tree_item_changed(self, item, column):
        if not self._gl_widget:
            return
        mesh_name = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked

        if mesh_name is None:
            # Collection parent: propagate to children
            self.mesh_tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                child_name = child.data(0, Qt.UserRole)
                if child_name:
                    self._gl_widget.mesh_visible[child_name] = checked
            self.mesh_tree.blockSignals(False)
        else:
            # Single mesh
            self._gl_widget.mesh_visible[mesh_name] = checked
            # Update parent check state
            parent = item.parent()
            if parent:
                self.mesh_tree.blockSignals(True)
                all_checked = all(
                    parent.child(i).checkState(0) == Qt.Checked
                    for i in range(parent.childCount())
                )
                any_checked = any(
                    parent.child(i).checkState(0) == Qt.Checked
                    for i in range(parent.childCount())
                )
                if all_checked:
                    parent.setCheckState(0, Qt.Checked)
                elif any_checked:
                    parent.setCheckState(0, Qt.PartiallyChecked)
                else:
                    parent.setCheckState(0, Qt.Unchecked)
                self.mesh_tree.blockSignals(False)

        self._gl_widget.update()


# ---------------------------------------------------------------------------
# Tab 4: Build
# ---------------------------------------------------------------------------

class BuildPanel(QWidget):
    """Build pipeline with QProcess, progress bar, and log terminal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._queue = []
        self._current_step = -1
        self._steps = []
        self._track_root = None
        self._config = None
        self._install_after_build = False

        layout = QVBoxLayout(self)

        # Toolbar
        self.toolbar = QHBoxLayout()
        self.btn_build_all = QPushButton("Build All")
        self.btn_build_all.setStyleSheet(
            "QPushButton { background: #2a6e2a; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #358535; }"
        )
        self.btn_build_all.setToolTip("Esegui tutti gli step di build (KN5, mod folder, AI line)")
        self.btn_build_all.clicked.connect(self._build_all)
        self.toolbar.addWidget(self.btn_build_all)

        self.btn_build_install = QPushButton("Build + Install")
        self.btn_build_install.setStyleSheet(
            "QPushButton { background: #1a5e8a; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2570a0; }"
        )
        self.btn_build_install.setToolTip("Esegui build completo e installa nella cartella di Assetto Corsa")
        self.btn_build_install.clicked.connect(self._build_and_install)
        self.toolbar.addWidget(self.btn_build_install)

        self.btn_install = QPushButton("Install")
        self.btn_install.setStyleSheet(
            "QPushButton { background: #6a5a2a; padding: 6px 12px; }"
            "QPushButton:hover { background: #7a6a3a; }"
        )
        self.btn_install.setToolTip("Installa la mod nella cartella di Assetto Corsa (senza rebuild)")
        self.btn_install.clicked.connect(self._install_only)
        self.toolbar.addWidget(self.btn_install)

        self.step_buttons = []
        # Will be populated when a track is loaded

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet(
            "QPushButton { background: #8b2020; padding: 6px 12px; }"
            "QPushButton:hover { background: #a52a2a; }"
        )
        self.btn_stop.setToolTip("Interrompi il processo di build corrente")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)

        self.toolbar.addStretch()
        self.toolbar.addWidget(self.btn_stop)
        layout.addLayout(self.toolbar)

        # Progress
        prog_layout = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        prog_layout.addWidget(self.progress)
        self.status_label = QLabel("Pronto")
        self.status_label.setMinimumWidth(200)
        prog_layout.addWidget(self.status_label)
        layout.addLayout(prog_layout)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(LOG_TERM_STYLE)
        layout.addWidget(self.log)

    def load_track(self, track_root, config):
        """Reconfigure build steps for the selected track."""
        self._track_root = track_root
        # Re-read config from disk (may have been edited via Parameters tab)
        config_path = os.path.join(track_root, "track_config.json")
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass
        self._config = config
        self._steps = build_steps(config, track_root)

        # Remove old step buttons
        for btn in self.step_buttons:
            self.toolbar.removeWidget(btn)
            btn.deleteLater()
        self.step_buttons.clear()

        # Insert new step buttons after Build All
        insert_pos = 1  # after btn_build_all
        for i, (label, _cmd, _env) in enumerate(self._steps):
            btn = QPushButton(f"Step {i + 1}")
            btn.setToolTip(label)
            btn.clicked.connect(lambda checked, idx=i: self._build_single(idx))
            self.toolbar.insertWidget(insert_pos + i, btn)
            self.step_buttons.append(btn)

        self.progress.setRange(0, len(self._steps))
        self.progress.setValue(0)
        self.status_label.setText("Pronto")
        self.log.clear()

    def _append_log(self, text, color="#dcdcdc"):
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(
            f'<span style="color:{color};">{text}</span><br>'
        )
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _set_running(self, running):
        self.btn_build_all.setEnabled(not running)
        self.btn_build_install.setEnabled(not running)
        self.btn_install.setEnabled(not running)
        for btn in self.step_buttons:
            btn.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def _refresh_config(self):
        """Re-read config from disk in case Parameters tab saved changes."""
        if not self._track_root:
            return
        config_path = os.path.join(self._track_root, "track_config.json")
        try:
            with open(config_path, encoding="utf-8") as f:
                self._config = json.load(f)
            self._steps = build_steps(self._config, self._track_root)
        except Exception:
            pass

    def _check_blend_protection(self):
        """Check if .blend was modified manually before regenerating.

        Returns:
            "regenerate" — proceed with init (backup done if needed).
            "skip"       — skip init, build from current .blend.
            None         — user cancelled.
        """
        if not self._config or not self._track_root:
            return "regenerate"
        slug = self._config.get("slug", "track")
        blend_file = os.path.join(self._track_root, f"{slug}.blend")
        centerline_file = os.path.join(self._track_root, "centerline.json")

        # Same mtime check as build_steps — would init be triggered?
        would_init = not os.path.isfile(blend_file)
        if not would_init and os.path.isfile(centerline_file):
            if os.path.getmtime(centerline_file) > os.path.getmtime(blend_file):
                would_init = True
        if not would_init:
            return "regenerate"

        # .blend doesn't exist yet — nothing to protect
        if not os.path.isfile(blend_file):
            return "regenerate"

        modified = is_blend_modified(blend_file)
        if not modified:
            return "regenerate"

        # Show dialog
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Blend modificato")
        msg.setText(
            f"<b>{slug}.blend</b> è stato modificato manualmente.<br>"
            "Rigenerare sovrascriverà le modifiche."
        )
        btn_regen = msg.addButton("Rigenera (con backup)", QMessageBox.AcceptRole)
        btn_skip = msg.addButton("Usa .blend attuale", QMessageBox.ActionRole)
        msg.addButton("Annulla", QMessageBox.RejectRole)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == btn_regen:
            bak = backup_blend(blend_file)
            self._append_log(f"Backup: {os.path.basename(bak)}", "#e8a838")
            return "regenerate"
        elif clicked == btn_skip:
            return "skip"
        return None

    def _build_all(self):
        if not self._steps:
            return
        self._refresh_config()
        result = self._check_blend_protection()
        if result is None:
            return
        force_skip = result == "skip"
        self._steps = build_steps(self._config, self._track_root, force_skip_init=force_skip)
        self.log.clear()
        self.progress.setRange(0, len(self._steps))
        self.progress.setValue(0)
        self._queue = list(range(len(self._steps)))
        self._install_after_build = False
        self._set_running(True)
        self._run_next()

    def _build_single(self, idx):
        if not self._steps:
            return
        self.log.clear()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._queue = [idx]
        self._install_after_build = False
        self._set_running(True)
        self._run_next()

    def _build_and_install(self):
        if not self._steps:
            return
        self._refresh_config()
        result = self._check_blend_protection()
        if result is None:
            return
        force_skip = result == "skip"
        self._steps = build_steps(self._config, self._track_root, force_skip_init=force_skip)
        self.log.clear()
        self.progress.setRange(0, len(self._steps) + 1)
        self.progress.setValue(0)
        self._queue = list(range(len(self._steps)))
        self._install_after_build = True
        self._set_running(True)
        self._run_next()

    def _install_only(self):
        if not self._track_root:
            return
        self.log.clear()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._queue = []
        self._install_after_build = False
        self._set_running(True)
        self._run_install()

    def _run_next(self):
        if not self._queue:
            self._on_all_done()
            return

        step_idx = self._queue.pop(0)
        self._current_step = step_idx
        label, cmd, env_extra = self._steps[step_idx]

        self._append_log(f"{'=' * 60}", "#5599ff")
        self._append_log(f"  Step {step_idx + 1}: {label}", "#5599ff")
        self._append_log(f"{'=' * 60}", "#5599ff")
        self.status_label.setText(f"Step {step_idx + 1}: {label}")

        self._process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        # venv is inside each track project, not the generator
        if platform_utils.IS_WINDOWS:
            venv_bin = os.path.join(self._track_root, ".venv", "Scripts")
        else:
            venv_bin = os.path.join(self._track_root, ".venv", "bin")
        env.insert("PATH", venv_bin + platform_utils.path_separator() + env.value("PATH"))
        env.insert("TRACK_ROOT", self._track_root)
        for k, v in env_extra.items():
            env.insert(k, v)
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(self._track_root)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_step_finished)

        program = cmd[0]
        args = cmd[1:]
        self._process.start(program, args)

    def _on_stdout(self):
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._append_log(line, "#dcdcdc")

    def _on_stderr(self):
        data = self._process.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._append_log(line, "#e8a838")

    def _on_step_finished(self, exit_code, _exit_status):
        step_idx = self._current_step
        label = self._steps[step_idx][0] if 0 <= step_idx < len(self._steps) else "?"

        if exit_code == 0:
            self._append_log(f"  v {label} completed", "#55cc55")
            done = len(self._steps) - len(self._queue)
            if self.progress.maximum() == len(self._steps):
                self.progress.setValue(done)
            else:
                self.progress.setValue(1)
        else:
            self._append_log(f"  x {label} failed (exit code {exit_code})", "#ff5555")
            self._queue.clear()
            self._set_running(False)
            self.status_label.setText(f"Error at step {step_idx + 1}")
            return

        self._process = None
        self._run_next()

    def _on_all_done(self):
        self._append_log("", "#55cc55")
        self._append_log("Build completed!", "#55cc55")

        if not self._config or not self._track_root:
            self._set_running(False)
            self.status_label.setText("Build completed!")
            return

        slug = self._config.get("slug", "track")
        has_reverse = self._config.get("layouts", {}).get("reverse", False)

        # Copy KN5 files to mod folder
        mod_dir = os.path.join(self._track_root, "mod", slug)
        os.makedirs(mod_dir, exist_ok=True)

        kn5_files = [f"{slug}.kn5"]
        if has_reverse:
            kn5_files.append(f"{slug}_reverse.kn5")

        for kn5_name in kn5_files:
            src = os.path.join(self._track_root, kn5_name)
            dst = os.path.join(mod_dir, kn5_name)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                self._append_log(f"{kn5_name} copied to mod/", "#55cc55")

        # Create distributable zip
        builds_dir = os.path.join(self._track_root, "builds")
        os.makedirs(builds_dir, exist_ok=True)
        zip_path = os.path.join(builds_dir, f"{slug}.zip")
        if os.path.isdir(mod_dir):
            self._append_log("Creating distributable zip...", "#dcdcdc")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for dirpath, _dirnames, filenames in os.walk(mod_dir):
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        arcname = os.path.join(slug, os.path.relpath(full, mod_dir))
                        zf.write(full, arcname)
            size_kb = os.path.getsize(zip_path) / 1024
            self._append_log(f"builds/{slug}.zip ({size_kb:.0f} KB)", "#55cc55")

        # Run install if requested
        if getattr(self, "_install_after_build", False):
            self._install_after_build = False
            self._run_install()
        else:
            self._set_running(False)
            self.status_label.setText("Build completed!")

    def _run_install(self):
        """Run the centralized install.py script."""
        install_py = os.path.join(GENERATOR_DIR, "install.py")
        if not os.path.isfile(install_py):
            self._append_log("install.py not found!", "#ff5555")
            self._set_running(False)
            return

        self._append_log("", "#5599ff")
        self._append_log(f"{'=' * 60}", "#5599ff")
        self._append_log("  Installing to Assetto Corsa...", "#5599ff")
        self._append_log(f"{'=' * 60}", "#5599ff")
        self.status_label.setText("Installing...")

        self._process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("TRACK_ROOT", self._track_root)
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(GENERATOR_DIR)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_install_finished)
        self._process.start(sys.executable, [install_py])

    def _on_install_finished(self, exit_code, _exit_status):
        self._process = None
        if exit_code == 0:
            self._append_log("", "#55cc55")
            self._append_log("Install completed!", "#55cc55")
            self.status_label.setText("Install completed!")
            self.progress.setValue(self.progress.maximum())
        else:
            self._append_log(f"Install failed (exit code {exit_code})", "#ff5555")
            self.status_label.setText("Install failed!")
        self._set_running(False)

    def _stop(self):
        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()
        self._queue.clear()
        self._set_running(False)
        self.status_label.setText("Stopped")
        self._append_log("Build stopped by user.", "#ff5555")


# ---------------------------------------------------------------------------
# Dashboard (landing page)
# ---------------------------------------------------------------------------

_APP_VERSION = "3.1.0"
_GITHUB_REPO = "https://github.com/KinG-InFeT/blender-assetto-corsa-track-generator"

_TRACK_REPOS = [
    ("Kartodromo di Casaluce", "casaluce-track",
     "https://github.com/KinG-InFeT/casaluce-track"),
    ("Pista Caudina", "montesarchio-track-ac-mod",
     "https://github.com/KinG-InFeT/montesarchio-track-ac-mod"),
    ("Touch and Go", "touch-and-go-martina-franca-track",
     "https://github.com/KinG-InFeT/touch-and-go-martina-franca-track"),
]

_BADGE_LABEL_CSS = (
    "background: #333; color: #ddd; font-size: 9pt; font-weight: bold;"
    "padding: 3px 6px; border-top-left-radius: 4px; border-bottom-left-radius: 4px;"
)


def _make_badge(label_text, value_text, value_color, link=None):
    """Return a QWidget styled as a shields.io-like badge, optionally clickable."""
    widget = QWidget()
    h = QHBoxLayout(widget)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)

    lbl = QLabel(label_text)
    lbl.setStyleSheet(_BADGE_LABEL_CSS)
    h.addWidget(lbl)

    val = QLabel(value_text)
    if link:
        val = QLabel(f'<a href="{link}" style="color: #fff; text-decoration: none;">{value_text}</a>')
        val.setOpenExternalLinks(True)
    val.setStyleSheet(
        f"background: {value_color}; color: #fff; font-size: 9pt; font-weight: bold;"
        "padding: 3px 8px; border-top-right-radius: 4px; border-bottom-right-radius: 4px;"
    )
    h.addWidget(val)
    return widget


class DashboardPanel(QWidget):
    """Landing page shown when no track is selected."""

    track_cloned = pyqtSignal(str)  # emits cloned folder path

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(3)

        title = QLabel("Track Manager Hub")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 28pt; font-weight: bold; color: #ccc;")
        layout.addWidget(title)

        subtitle = QLabel("Seleziona una pista dalla lista per iniziare")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 12pt; color: #777; margin-top: 12px;")
        layout.addWidget(subtitle)

        layout.addStretch(1)

        # Track repos section
        tracks_label = QLabel("PISTE DISPONIBILI")
        tracks_label.setAlignment(Qt.AlignCenter)
        tracks_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #888;")
        layout.addWidget(tracks_label)
        layout.addSpacing(6)

        for name, repo_name, url in _TRACK_REPOS:
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(_make_badge("track", name, "#c9510c", url))
            row.addSpacing(8)
            btn = QPushButton("Clone")
            btn.setStyleSheet(
                "QPushButton { background: #2a6e2a; padding: 4px 12px; font-size: 9pt; font-weight: bold; }"
                "QPushButton:hover { background: #358535; }"
            )
            btn.setToolTip(f"Clona {repo_name} nella cartella del progetto")
            btn.clicked.connect(lambda checked, u=url, r=repo_name: self._clone_repo(u, r))
            row.addWidget(btn)
            row.addStretch()
            layout.addLayout(row)
            layout.addSpacing(4)

        layout.addStretch(2)

        # Project badges row
        badges = QHBoxLayout()
        badges.addStretch()
        badges.addWidget(_make_badge("version", _APP_VERSION, "#0969da"))
        badges.addSpacing(8)
        badges.addWidget(_make_badge("license", "Apache 2.0", "#2ea043"))
        badges.addSpacing(8)
        badges.addWidget(_make_badge("github", "blender-assetto-corsa-track-generator", "#6e40c9", _GITHUB_REPO))
        badges.addStretch()
        layout.addLayout(badges)

        layout.addSpacing(16)

    def _clone_repo(self, url, repo_name):
        parent_dir = os.path.dirname(GENERATOR_DIR)
        dest = os.path.join(parent_dir, repo_name)
        if os.path.isdir(dest):
            QMessageBox.warning(
                self, "Cartella esistente",
                f"La cartella <b>{repo_name}</b> esiste già.<br>"
                f"<code>{dest}</code>",
            )
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()
            result = subprocess.run(
                ["git", "clone", url, dest],
                capture_output=True, text=True,
            )
            QApplication.restoreOverrideCursor()
            if result.returncode != 0:
                QMessageBox.critical(
                    self, "Errore clone",
                    f"git clone fallito:\n{result.stderr.strip()}",
                )
                return
            QMessageBox.information(
                self, "Clone completato",
                f"<b>{repo_name}</b> clonato con successo.",
            )
            self.track_cloned.emit(dest)
        except FileNotFoundError:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "Errore",
                "git non trovato. Installalo e riprova.",
            )


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class TrackManagerHub(QMainWindow):
    """Main window with track sidebar + tabbed central area."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Track Manager Hub v{_APP_VERSION}")
        self.resize(1280, 720)
        screen = QApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            self.move(rect.x() + (rect.width() - 1280) // 2,
                      rect.y() + (rect.height() - 720) // 2)

        # Discover tracks
        parent_dir = os.path.dirname(GENERATOR_DIR)
        self._tracks = discover_tracks(parent_dir)

        # Central splitter
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # --- Left sidebar ---
        sidebar = QWidget()
        sidebar.setMaximumWidth(220)
        sidebar.setMinimumWidth(180)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)

        btn_dashboard = QPushButton("Dashboard")
        btn_dashboard.setStyleSheet(
            "QPushButton { background: #444; padding: 8px 12px; font-weight: bold; font-size: 10pt; }"
            "QPushButton:hover { background: #555; }"
        )
        btn_dashboard.setToolTip("Torna alla schermata principale")
        btn_dashboard.clicked.connect(self._show_dashboard)
        sidebar_layout.addWidget(btn_dashboard)

        lbl = QLabel("TRACKS")
        lbl.setStyleSheet("font-weight: bold; font-size: 11pt; padding: 4px;")
        sidebar_layout.addWidget(lbl)

        self.track_list = QListWidget()
        self.track_list.setStyleSheet(
            "QListWidget { font-size: 10pt; }"
            "QListWidget::item { padding: 6px; }"
            "QListWidget::item:selected { background: #2a82da; }"
        )
        self._populate_track_list()
        self.track_list.currentItemChanged.connect(self._on_track_selected)
        sidebar_layout.addWidget(self.track_list)

        # Buttons below track list
        btn_new = QPushButton("+ Nuova Pista")
        btn_new.setStyleSheet(
            "QPushButton { background: #2a6e2a; padding: 6px 10px; font-weight: bold; }"
            "QPushButton:hover { background: #358535; }"
        )
        btn_new.setToolTip("Crea un nuovo progetto pista con struttura e file di configurazione")
        btn_new.clicked.connect(self._create_new_track)
        sidebar_layout.addWidget(btn_new)

        btn_refresh = QPushButton("Ricarica lista")
        btn_refresh.setStyleSheet(
            "QPushButton { background: #3a3a3a; padding: 6px 10px; }"
            "QPushButton:hover { background: #4a4a4a; }"
        )
        btn_refresh.setToolTip("Ricarica la lista delle piste trovate nella cartella parent")
        btn_refresh.clicked.connect(self._refresh_tracks)
        sidebar_layout.addWidget(btn_refresh)

        # Info box
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #555;")
        sidebar_layout.addWidget(sep)

        self.info_box = QLabel("")
        self.info_box.setWordWrap(True)
        self.info_box.setStyleSheet("font-size: 9pt; color: #aaa; padding: 4px;")
        sidebar_layout.addWidget(self.info_box)
        sidebar_layout.addStretch()

        splitter.addWidget(sidebar)

        # --- Right area: stacked (dashboard / tabs) ---
        self.stack = QStackedWidget()

        # Page 0: Dashboard
        self.dashboard = DashboardPanel()
        self.dashboard.track_cloned.connect(self._on_track_cloned)
        self.stack.addWidget(self.dashboard)

        # Page 1: Tabs
        tabs_container = QWidget()
        tabs_layout = QVBoxLayout(tabs_container)
        tabs_layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        tabs_layout.addWidget(self.tabs)

        # Tab 1: Parameters
        self.params_panel = ParametersPanel()
        self.tabs.addTab(self.params_panel, "Parametri")

        # Tab 2: Layout Editor
        self.editor_panel = TrackEditorPanel()
        self.editor_panel.map_center_changed.connect(self._on_map_center_changed)
        self.tabs.addTab(self.editor_panel, "Layout Editor")

        # Tab 3: Build
        self.build_panel = BuildPanel()
        self.tabs.addTab(self.build_panel, "Build")

        # Tab 4: Preview
        self.preview_panel = PreviewPanel()
        self.tabs.addTab(self.preview_panel, "Preview")

        self.stack.addWidget(tabs_container)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Loading overlay
        self._loading_overlay = QLabel("Caricamento...", self)
        self._loading_overlay.setAlignment(Qt.AlignCenter)
        self._loading_overlay.setStyleSheet(
            "background: rgba(30, 30, 30, 200); color: #ddd;"
            "font-size: 14pt; font-weight: bold; border-radius: 8px;"
        )
        self._loading_overlay.hide()

        self.statusBar().showMessage("Pronto")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Start on dashboard (no track selected)
        self.tabs.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)

    def closeEvent(self, event):
        self.editor_panel.canvas._tile_fetcher.stop()
        super().closeEvent(event)

    def _populate_track_list(self):
        self.track_list.blockSignals(True)
        self.track_list.clear()
        for t in self._tracks:
            name = t["config"].get("info", {}).get("name", t["dir"])
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, t)
            self.track_list.addItem(item)
        self.track_list.blockSignals(False)

    def _show_dashboard(self):
        self.track_list.blockSignals(True)
        self.track_list.setCurrentRow(-1)
        self.track_list.blockSignals(False)
        self.stack.setCurrentIndex(0)
        self.info_box.setText("")
        self.statusBar().showMessage("Pronto")

    def _refresh_tracks(self):
        parent_dir = os.path.dirname(GENERATOR_DIR)
        self._tracks = discover_tracks(parent_dir)
        self._populate_track_list()

    def _on_track_cloned(self, cloned_path):
        """Refresh track list and select the newly cloned track."""
        self._refresh_tracks()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            t = item.data(Qt.UserRole)
            if t["path"] == cloned_path:
                self.track_list.setCurrentRow(i)
                return

    def _create_new_track(self):
        parent_dir = os.path.dirname(GENERATOR_DIR)
        existing_slugs = {t["config"].get("slug", t["dir"]) for t in self._tracks}
        existing_slugs |= {t["dir"] for t in self._tracks}
        dlg = NewTrackDialog(parent_dir, existing_slugs, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        result = dlg.get_result()
        track_path = result["path"]
        slug = result["slug"]
        name = result["name"]

        # Create directory structure
        os.makedirs(track_path, exist_ok=True)
        tex_dst = os.path.join(track_path, "textures")
        os.makedirs(tex_dst, exist_ok=True)

        # Copy default textures from generator
        tex_src = os.path.join(GENERATOR_DIR, "textures")
        if os.path.isdir(tex_src):
            for fn in os.listdir(tex_src):
                src = os.path.join(tex_src, fn)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(tex_dst, fn))

        # Create track_config.json from defaults
        config = dict(_DEFAULTS)
        config["slug"] = slug
        config["info"] = {"name": name}
        config["layouts"] = {"reverse": False}
        with open(os.path.join(track_path, "track_config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        # Create empty centerline.json v2
        with open(os.path.join(track_path, "centerline.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 2, "layers": [], "start": None, "map_center": None}, f, indent=2)

        # Create .gitignore
        with open(os.path.join(track_path, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(".venv/\nmod/\nbuilds/\n*.kn5\n*_reverse.blend\n")

        # Copy default cover.png from generator
        default_cover = os.path.join(GENERATOR_DIR, "cover.png")
        if os.path.isfile(default_cover):
            shutil.copy2(default_cover, os.path.join(track_path, "cover.png"))

        # Create venv and install dependencies
        self._loading_overlay.setText("Creazione ambiente virtuale...")
        self._show_loading()
        venv_dir = os.path.join(track_path, ".venv")
        try:
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            if platform_utils.IS_WINDOWS:
                pip = os.path.join(venv_dir, "Scripts", "pip")
            else:
                pip = os.path.join(venv_dir, "bin", "pip")
            req = os.path.join(GENERATOR_DIR, "requirements.txt")
            self._loading_overlay.setText("Installazione dipendenze...")
            QApplication.processEvents()
            subprocess.run([pip, "install", "-r", req], check=True)
        except Exception as e:
            if platform_utils.IS_WINDOWS:
                venv_hint = (
                    f"cd {track_path}\n"
                    "python -m venv .venv\n"
                    f".venv\\Scripts\\pip install -r "
                    f"{os.path.join(GENERATOR_DIR, 'requirements.txt')}"
                )
            else:
                venv_hint = (
                    f"cd {track_path} && python3 -m venv .venv && "
                    f".venv/bin/pip install -r "
                    f"{os.path.join(GENERATOR_DIR, 'requirements.txt')}"
                )
            QMessageBox.warning(self, "Attenzione",
                                f"Errore nella creazione del venv:\n{e}\n\n"
                                f"Puoi crearlo manualmente con:\n{venv_hint}")
        self._loading_overlay.setText("Caricamento...")
        self._hide_loading()

        # Refresh and select new track
        self._refresh_tracks()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            t = item.data(Qt.UserRole)
            if t["path"] == track_path:
                self.track_list.setCurrentRow(i)
                break

        # Switch to Layout Editor tab
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(f"Nuova pista creata: {name}")

    def _on_map_center_changed(self, lat, lon):
        widget = self.params_panel._widgets.get("info.geotags")
        if widget:
            widget.setText(f"{lat:.4f}, {lon:.4f}")

    def _on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.preview_panel:
            self.preview_panel.ensure_loaded()

    def _show_loading(self):
        self._loading_overlay.setGeometry(self.centralWidget().geometry())
        self._loading_overlay.raise_()
        self._loading_overlay.show()
        QApplication.processEvents()

    def _hide_loading(self):
        self._loading_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._loading_overlay.isVisible():
            self._loading_overlay.setGeometry(self.centralWidget().geometry())

    def _on_track_selected(self, current, _previous=None):
        if not current:
            return
        self.stack.setCurrentIndex(1)
        self._show_loading()

        track = current.data(Qt.UserRole)
        config = track["config"]
        track_root = track["path"]
        config_path = os.path.join(track_root, "track_config.json")

        # Update info box
        slug = config.get("slug", "?")
        info = config.get("info", {})
        name = info.get("name", slug)
        city = info.get("city", "")
        province = info.get("province", "")
        length = info.get("length", "?")
        has_rev = config.get("layouts", {}).get("reverse", False)
        rev_str = "Yes" if has_rev else "No"

        lines = [
            f"slug: {slug}",
            f"{name}",
        ]
        if city:
            loc = city
            if province:
                loc += f" ({province})"
            lines.append(loc)
        lines.append(f"length: {length} m")
        lines.append(f"reverse: {rev_str}")
        self.info_box.setText("\n".join(lines))

        # Update tabs
        self.params_panel.load_track(config_path)
        self.editor_panel.load_track(track_root, config)

        # Auto-set map from geotags or default to Naples
        if not self.editor_panel.canvas.get_map_center():
            lat, lon = 40.8518, 14.2681  # Naples default
            geotags = info.get("geotags")
            if isinstance(geotags, list) and len(geotags) >= 2:
                try:
                    lat, lon = float(geotags[0]), float(geotags[1])
                except (ValueError, TypeError):
                    pass
            self.editor_panel.canvas.set_map_center(lat, lon)
            self.editor_panel.coord_label.setText(f"{lat:.4f}, {lon:.4f}")

        self.preview_panel.load_track(track_root, config)
        if self.tabs.currentWidget() is self.preview_panel:
            self.preview_panel.ensure_loaded()
        self.build_panel.load_track(track_root, config)

        self._hide_loading()
        self.statusBar().showMessage(f"Track: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    window = TrackManagerHub()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
