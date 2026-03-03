#!/usr/bin/env python3
"""Cross-platform Assetto Corsa mod installer.

Reads track info from TRACK_ROOT/track_config.json.
Installs: track mod, Content Manager, Custom Shaders Patch, fonts.

Usage:
    TRACK_ROOT=/path/to/track python install.py
    python install.py /path/to/track
"""

import glob
import json
import os
import shutil
import sys
import tempfile

GENERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(GENERATOR_DIR, "scripts"))
import platform_utils


def _dir_size_str(path):
    """Return human-readable directory size."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    if total >= 1024 * 1024:
        return f"{total / (1024 * 1024):.1f}M"
    return f"{total / 1024:.0f}K"


def _find_zip(patterns):
    """Search for a zip file matching any of the given glob patterns."""
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def _find_in_zip(zip_path, names):
    """Find a file in a zip archive matching any of the given names."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        for entry in zf.namelist():
            basename = os.path.basename(entry)
            if basename in names:
                return entry
    return None


def main():
    # Resolve TRACK_ROOT
    track_root = os.environ.get("TRACK_ROOT", "")
    if not track_root and len(sys.argv) > 1:
        track_root = sys.argv[1]

    if not track_root or not os.path.isdir(track_root):
        print("Error: TRACK_ROOT not set or not a directory.")
        print("Usage: TRACK_ROOT=/path/to/track python install.py")
        print("   or: python install.py /path/to/track")
        sys.exit(1)

    track_root = os.path.abspath(track_root)

    # Read track config
    config_path = os.path.join(track_root, "track_config.json")
    if not os.path.isfile(config_path):
        print(f"Error: track_config.json not found in {track_root}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    slug = config.get("slug", "track")
    track_name = config.get("info", {}).get("name", slug)

    # Find Assetto Corsa directory
    ac_dir = os.environ.get("AC_DIR", "")
    if not ac_dir or not os.path.isdir(ac_dir):
        ac_dir = ""
        for path in platform_utils.ac_search_paths():
            if os.path.isdir(path):
                ac_dir = path
                break

    if not ac_dir:
        print("Error: Assetto Corsa not found. Searched paths:")
        for path in platform_utils.ac_search_paths():
            print(f"  - {path}")
        print()
        if platform_utils.IS_WINDOWS:
            print(r'Set manually: set AC_DIR=C:\path\to\assettocorsa&& python install.py')
        else:
            print("Set manually: AC_DIR=/path/to/assettocorsa python install.py")
        sys.exit(1)

    tracks_dir = os.path.join(ac_dir, "content", "tracks")

    print(f"=== {track_name} - Installazione mod ===")
    print(f"AC trovato in: {ac_dir}")
    print()

    # --- 1. Install track mod ---
    mod_dir = os.path.join(track_root, "mod", slug)
    dest = os.path.join(tracks_dir, slug)

    if not os.path.isdir(mod_dir):
        print(f"Errore: cartella mod non trovata in {mod_dir}")
        print(f"Esegui prima la build: TRACK_ROOT={track_root} python build_cli.py")
        sys.exit(1)

    kn5_file = os.path.join(mod_dir, f"{slug}.kn5")
    if not os.path.isfile(kn5_file):
        print(f"Errore: {slug}.kn5 non trovato nella cartella mod.")
        print("Esegui prima la build.")
        sys.exit(1)

    print(f"[PISTA] Installando {track_name}...")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(mod_dir, dest)
    print(f"[PISTA] {track_name} installata!")

    # Clean AC engine cache
    ac_cache = os.path.join(ac_dir, "cache")
    if os.path.isdir(ac_cache):
        cleaned = 0
        for subdir in ("ai_grids", "ai_payloads"):
            cache_sub = os.path.join(ac_cache, subdir)
            if os.path.isdir(cache_sub):
                for f in os.listdir(cache_sub):
                    if f.startswith(f"{slug}__"):
                        os.remove(os.path.join(cache_sub, f))
                        cleaned += 1
        meshes_dir = os.path.join(ac_cache, "meshes_metadata")
        if os.path.isdir(meshes_dir):
            for f in os.listdir(meshes_dir):
                if f.endswith((".bin", ".tmp")):
                    try:
                        os.remove(os.path.join(meshes_dir, f))
                        cleaned += 1
                    except OSError:
                        pass
        if cleaned > 0:
            print("[CACHE] Pulita cache AC (ai_grids, ai_payloads, meshes_metadata)")

    # Clean Content Manager cache
    cm_data = platform_utils.cm_cache_dir()
    if cm_data and os.path.isdir(cm_data):
        cache_data = os.path.join(cm_data, "Cache.data")
        if os.path.isfile(cache_data):
            try:
                os.remove(cache_data)
            except OSError:
                pass
        backups_dir = os.path.join(cm_data, "Temporary", "Storages Backups")
        if os.path.isdir(backups_dir):
            shutil.rmtree(backups_dir, ignore_errors=True)
        print("[CACHE] Pulita cache Content Manager")
    print()

    # --- 2. Content Manager ---
    print("[CM] Verifica Content Manager...")
    cm_exe = os.path.join(ac_dir, "Content Manager Safe.exe")
    if os.path.isfile(cm_exe):
        print("[CM] Content Manager gia' installato.")
    else:
        # Search for CM zip in addons/ and download dirs
        addons_dir = os.path.join(GENERATOR_DIR, "addons")
        search_patterns = [
            os.path.join(addons_dir, "ContentManager*.zip"),
            os.path.join(addons_dir, "content-manager*.zip"),
        ]
        for dl_dir in platform_utils.download_dir_candidates():
            search_patterns.append(os.path.join(dl_dir, "ContentManager*.zip"))

        cm_zip = _find_zip(search_patterns)

        if not cm_zip:
            print("[CM] Zip non trovato, scarico da acstuff.ru...")
            os.makedirs(addons_dir, exist_ok=True)
            cm_zip = os.path.join(addons_dir, "ContentManager.zip")
            if platform_utils.download_file("https://acstuff.ru/app/latest.zip", cm_zip):
                print(f"[CM] Scaricato: {cm_zip}")
            else:
                print("[CM] ATTENZIONE: download fallito")
                cm_zip = None

        if cm_zip and os.path.isfile(cm_zip):
            print(f"[CM] Trovato archivio Content Manager: {cm_zip}")
            tmp_dir = tempfile.mkdtemp()
            try:
                if platform_utils.extract_zip(cm_zip, tmp_dir):
                    cm_entry = _find_in_zip(cm_zip, ("Content Manager.exe", "ContentManager.exe"))
                    if cm_entry:
                        extracted = os.path.join(tmp_dir, cm_entry)
                        if os.path.isfile(extracted):
                            shutil.copy2(extracted, cm_exe)
                            print(f"[CM] Content Manager installato in: {cm_exe}")
                        else:
                            print("[CM] ATTENZIONE: Content Manager.exe non trovato nell'archivio")
                    else:
                        print("[CM] ATTENZIONE: Content Manager.exe non trovato nell'archivio")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # Set Content Manager as Steam launcher
    if os.path.isfile(cm_exe):
        ac_launcher = os.path.join(ac_dir, "AssettoCorsa.exe")
        ac_backup = os.path.join(ac_dir, "AssettoCorsa_original.exe")
        if os.path.isfile(ac_launcher) and not os.path.isfile(ac_backup):
            shutil.copy2(ac_launcher, ac_backup)
            print("[CM] Backup launcher originale: AssettoCorsa_original.exe")
        if os.path.isfile(ac_launcher):
            shutil.copy2(cm_exe, ac_launcher)
            print("[CM] Content Manager impostato come launcher di Steam")
    print()

    # --- 3. Custom Shaders Patch (CSP) ---
    print("[CSP] Verifica Custom Shaders Patch...")
    csp_dll = os.path.join(ac_dir, "dwrite.dll")
    csp_ext = os.path.join(ac_dir, "extension")
    if os.path.isfile(csp_dll) and os.path.isdir(csp_ext):
        print("[CSP] CSP gia' installato.")
    else:
        addons_dir = os.path.join(GENERATOR_DIR, "addons")
        search_patterns = [
            os.path.join(addons_dir, "lights-patch*.zip"),
            os.path.join(addons_dir, "csp*.zip"),
        ]
        for dl_dir in platform_utils.download_dir_candidates():
            search_patterns.append(os.path.join(dl_dir, "lights-patch*.zip"))

        csp_zip = _find_zip(search_patterns)

        if not csp_zip:
            print("[CSP] Zip non trovato, scarico da acstuff.club...")
            os.makedirs(addons_dir, exist_ok=True)
            csp_zip = os.path.join(addons_dir, "lights-patch-v0.2.11.zip")
            if platform_utils.download_file("https://acstuff.club/patch/?get=0.2.11", csp_zip):
                print(f"[CSP] Scaricato: {csp_zip}")
            else:
                print("[CSP] ATTENZIONE: download fallito")
                csp_zip = None

        if csp_zip and os.path.isfile(csp_zip):
            print(f"[CSP] Trovato archivio CSP: {csp_zip}")
            tmp_dir = tempfile.mkdtemp()
            try:
                if platform_utils.extract_zip(csp_zip, tmp_dir):
                    dwrite = os.path.join(tmp_dir, "dwrite.dll")
                    if os.path.isfile(dwrite):
                        shutil.copy2(dwrite, ac_dir)
                        print("[CSP] dwrite.dll copiato")
                    ext_dir = os.path.join(tmp_dir, "extension")
                    if os.path.isdir(ext_dir):
                        dest_ext = os.path.join(ac_dir, "extension")
                        if os.path.isdir(dest_ext):
                            shutil.rmtree(dest_ext)
                        shutil.copytree(ext_dir, dest_ext)
                        print("[CSP] cartella extension/ copiata")
                    print("[CSP] Custom Shaders Patch installato!")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    print()

    # --- 4. Fonts (required for CM/CSP on Linux) ---
    print("[FONT] Verifica font di sistema...")
    fonts_dir = os.path.join(ac_dir, "content", "fonts", "system")
    verdana = os.path.join(fonts_dir, "verdana.ttf")
    segoeui = os.path.join(fonts_dir, "segoeui.ttf")
    if os.path.isfile(verdana) and os.path.isfile(segoeui):
        print("[FONT] Font gia' installati.")
    else:
        addons_dir = os.path.join(GENERATOR_DIR, "addons")
        search_patterns = [os.path.join(addons_dir, "ac-fonts.zip")]
        for dl_dir in platform_utils.download_dir_candidates():
            search_patterns.append(os.path.join(dl_dir, "ac-fonts.zip"))

        fonts_zip = _find_zip(search_patterns)

        if not fonts_zip:
            print("[FONT] Zip non trovato, scarico da acstuff.club...")
            os.makedirs(addons_dir, exist_ok=True)
            fonts_zip = os.path.join(addons_dir, "ac-fonts.zip")
            if platform_utils.download_file("https://acstuff.club/u/blob/ac-fonts.zip", fonts_zip):
                print(f"[FONT] Scaricato: {fonts_zip}")
            else:
                print("[FONT] ATTENZIONE: download fallito")
                fonts_zip = None

        if fonts_zip and os.path.isfile(fonts_zip):
            print(f"[FONT] Trovato archivio font: {fonts_zip}")
            fonts_dest = os.path.join(ac_dir, "content", "fonts")
            os.makedirs(fonts_dest, exist_ok=True)
            if platform_utils.extract_zip(fonts_zip, fonts_dest):
                print("[FONT] Font installati (verdana.ttf, segoeui.ttf)")
    print()

    # --- Summary ---
    print("=== Riepilogo Installazione ===")
    print()
    print("Pista:")
    if os.path.isdir(dest):
        size = _dir_size_str(dest)
        print(f"  [OK] {slug} ({size})")
    else:
        print(f"  [--] {slug} (non installata)")
    print()

    print("Componenti aggiuntivi:")
    if os.path.isfile(cm_exe):
        ac_backup = os.path.join(ac_dir, "AssettoCorsa_original.exe")
        if os.path.isfile(ac_backup):
            print("  [OK] Content Manager (attivo come launcher Steam)")
        else:
            print("  [OK] Content Manager (installato)")
    else:
        print("  [--] Content Manager (non installato)")

    if os.path.isfile(csp_dll):
        print("  [OK] Custom Shaders Patch")
    else:
        print("  [--] Custom Shaders Patch (non installato)")

    if os.path.isfile(verdana):
        print("  [OK] Font di sistema")
    else:
        print("  [--] Font di sistema (non installati)")
    print()

    print("Avvia Assetto Corsa da Steam: Content Manager partira' automaticamente.")
    print(f"Seleziona '{track_name}' dal menu piste.")


if __name__ == "__main__":
    main()
