#!/usr/bin/env python3
"""Cross-platform CLI build for Assetto Corsa track mod.

Reads slug and layout config from TRACK_ROOT/track_config.json.
Runs a 6-step pipeline (default + reverse) or 3-step (single layout).

Usage:
    Linux:   TRACK_ROOT=/path/to/track python build_cli.py [--install] [--force-init]
    Windows: set TRACK_ROOT=C:\\path\\to\\track&& python build_cli.py [--install] [--force-init]
"""

import os
import shutil
import subprocess
import sys
import json

GENERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.environ.get("TRACK_ROOT", os.getcwd())

# Ensure scripts/ is on the path for platform_utils and blend_meta
sys.path.insert(0, os.path.join(GENERATOR_DIR, "scripts"))
import platform_utils
from blend_meta import is_blend_modified, backup_blend


def load_config():
    """Load track_config.json and return (slug, has_reverse)."""
    config_path = os.path.join(ROOT_DIR, "track_config.json")
    if not os.path.isfile(config_path):
        print(f"Error: track_config.json not found in {ROOT_DIR}")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    slug = config.get("slug", "track")
    has_reverse = config.get("layouts", {}).get("reverse", False)
    return slug, has_reverse


def main():
    slug, has_reverse = load_config()

    blend_file = os.path.join(ROOT_DIR, f"{slug}.blend")
    centerline_file = os.path.join(ROOT_DIR, "centerline.json")
    blender = platform_utils.find_blender()
    init_blend_py = os.path.join(GENERATOR_DIR, "scripts", "init_blend.py")

    # Determine if .blend needs (re)generation
    needs_init = not os.path.isfile(blend_file)
    if not needs_init and os.path.isfile(centerline_file):
        blend_mtime = os.path.getmtime(blend_file)
        cl_mtime = os.path.getmtime(centerline_file)
        if cl_mtime > blend_mtime:
            needs_init = True
            print(f"  centerline.json is newer than {slug}.blend — regenerating")

    # Blend protection: detect manual modifications before overwriting
    if needs_init and os.path.isfile(blend_file):
        modified = is_blend_modified(blend_file)
        if modified:
            if "--force-init" in sys.argv:
                bak = backup_blend(blend_file)
                print(f"  Backup: {os.path.basename(bak)}")
            else:
                print()
                print(f"  WARNING: {slug}.blend has been modified manually.")
                print(f"  Regenerating will overwrite your changes.")
                print()
                print(f"  [r] Regenerate (backup + overwrite)")
                print(f"  [s] Skip init (build from current .blend)")
                print(f"  [a] Abort")
                print()
                choice = input("  Choice [r/s/a]: ").strip().lower()
                if choice == "r":
                    bak = backup_blend(blend_file)
                    print(f"  Backup: {os.path.basename(bak)}")
                elif choice == "s":
                    needs_init = False
                    print("  Skipping init — building from current .blend")
                else:
                    print("  Aborted.")
                    sys.exit(0)

    # Compute venv path from ROOT_DIR (not GENERATOR_DIR)
    if platform_utils.IS_WINDOWS:
        venv_py = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
    else:
        venv_py = os.path.join(ROOT_DIR, ".venv", "bin", "python3")

    # Verify venv exists
    if not os.path.isfile(venv_py):
        print(f"Error: venv Python not found at {venv_py}")
        print("Create it first:")
        if platform_utils.IS_WINDOWS:
            print(f"  cd {ROOT_DIR}")
            print("  python -m venv .venv")
            print("  .venv\\Scripts\\pip install -r requirements.txt")
        else:
            print(f"  cd {ROOT_DIR}")
            print("  python3 -m venv .venv")
            print("  .venv/bin/pip install -r requirements.txt")
        sys.exit(1)

    env_base = dict(os.environ, TRACK_ROOT=ROOT_DIR)
    reverse_env = dict(env_base, TRACK_REVERSE="1")
    reverse_blend = os.path.join(ROOT_DIR, f"{slug}_reverse.blend")

    if has_reverse:
        steps = [
            ("Export KN5", [
                blender, "--background", blend_file,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "export_kn5.py"),
            ], env_base),
            ("Mod folder", [
                venv_py, os.path.join(GENERATOR_DIR, "scripts", "setup_mod_folder.py"),
            ], env_base),
            ("AI line CW", [
                blender, "--background", blend_file,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "generate_ai_line.py"),
            ], env_base),
            ("Reverse blend", [
                blender, "--background", blend_file,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "create_reverse_blend.py"),
            ], env_base),
            ("Export KN5 reverse", [
                blender, "--background", reverse_blend,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "export_kn5.py"),
            ], reverse_env),
            ("AI line CCW", [
                blender, "--background", reverse_blend,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "generate_ai_line.py"),
            ], reverse_env),
        ]
    else:
        steps = [
            ("Export KN5", [
                blender, "--background", blend_file,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "export_kn5.py"),
            ], env_base),
            ("Mod folder", [
                venv_py, os.path.join(GENERATOR_DIR, "scripts", "setup_mod_folder.py"),
            ], env_base),
            ("AI line", [
                blender, "--background", blend_file,
                "--python", os.path.join(GENERATOR_DIR, "scripts", "generate_ai_line.py"),
            ], env_base),
        ]

    # Prepend init_blend step if .blend needs (re)generation
    if needs_init:
        steps.insert(0, ("Init Blend", [
            blender, "--background", "--python", init_blend_py,
        ], env_base))

    # Label steps with numbering
    total = len(steps)
    steps = [(f"{i+1}/{total} - {lbl}", cmd, env)
             for i, (lbl, cmd, env) in enumerate(steps)]

    print(f"=== {slug} - Build mod ===")
    print()

    for label, cmd, env in steps:
        print(f"[{label}] Running...")
        result = subprocess.run(cmd, cwd=ROOT_DIR, env=env)
        if result.returncode != 0:
            print(f"[{label}] FAILED (exit code {result.returncode})")
            sys.exit(1)
        print(f"[{label}] Done.")
        print()

    # Copy KN5 files to mod folder
    mod_dir = os.path.join(ROOT_DIR, "mod", slug)
    os.makedirs(mod_dir, exist_ok=True)

    kn5_files = [f"{slug}.kn5"]
    if has_reverse:
        kn5_files.append(f"{slug}_reverse.kn5")

    for kn5_name in kn5_files:
        src = os.path.join(ROOT_DIR, kn5_name)
        dst = os.path.join(mod_dir, kn5_name)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            print(f"  {kn5_name} copied to mod/")

    # Create distributable zip
    import zipfile
    builds_dir = os.path.join(ROOT_DIR, "builds")
    os.makedirs(builds_dir, exist_ok=True)
    zip_path = os.path.join(builds_dir, f"{slug}.zip")
    if os.path.isdir(mod_dir):
        print("Creating distributable zip...")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(mod_dir):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    arcname = os.path.join(slug, os.path.relpath(full, mod_dir))
                    zf.write(full, arcname)
        size_kb = os.path.getsize(zip_path) / 1024
        print(f"  builds/{slug}.zip ({size_kb:.0f} KB)")

    print()
    print("=== Build completed! ===")
    print()
    if has_reverse:
        print("Layouts: default (CW) + reverse (CCW)")
    else:
        print("Layout: single (CW)")

    # Install if requested
    if "--install" in sys.argv:
        print()
        install_py = os.path.join(GENERATOR_DIR, "install.py")
        if os.path.isfile(install_py):
            print("=== Installing... ===")
            print()
            result = subprocess.run(
                [sys.executable, install_py],
                env=dict(os.environ, TRACK_ROOT=ROOT_DIR),
            )
            if result.returncode != 0:
                print("Install FAILED")
                sys.exit(1)
        else:
            print(f"Warning: install.py not found at {install_py}")
    else:
        if platform_utils.IS_WINDOWS:
            print(f'To install: set TRACK_ROOT={ROOT_DIR}&& python install.py')
        else:
            print(f"To install: TRACK_ROOT={ROOT_DIR} python install.py")

    print(f"To share:   send builds/{slug}.zip")


if __name__ == "__main__":
    main()
