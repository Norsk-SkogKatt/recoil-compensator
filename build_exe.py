"""
Build the Recoil Compensator into a standalone EXE using PyInstaller.

Usage:
    python build_exe.py

After building, the final EXE (压枪脚本.exe) is placed in the project root.
All build artifacts (build/, dist/, .spec) are automatically cleaned up.
"""

import os
import shutil
import subprocess
import sys


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(project_root, "源码")
    output_name = "压枪脚本"

    # ── ensure required packages are installed ─────────────────
    # windows-curses provides the 'curses' module, not 'windows_curses'
    required = {
        "pynput": "pynput",
        "curses": "windows-curses",
    }
    missing = []
    for import_name, pkg_name in required.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg_name)

    if missing:
        print(f"[!] 缺少依赖: {', '.join(missing)}")
        print(f"    请执行: pip install {' '.join(missing)}")
        sys.exit(1)

    # ── check PyInstaller ──────────────────────────────────────
    try:
        import PyInstaller  # noqa
    except ImportError:
        print("[!] 缺少 PyInstaller")
        print("    请执行: pip install pyinstaller")
        sys.exit(1)

    # ── build command ──────────────────────────────────────────
    # Use --add-data so all .py files in 源码/ are available at the
    # temporary bundle root (flat), matching the flat imports used
    # in main.py.
    add_data = f"{src_dir}{os.pathsep}."

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", output_name,
        "--add-data", add_data,
        "--clean",
        "--noconfirm",
        "--log-level", "WARN",
        os.path.join(src_dir, "main.py"),
    ]

    print(f"[*] Building {output_name}.exe ...")
    print(f"    Command: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=project_root)

    # ── locate output ──────────────────────────────────────────
    dist_exe = os.path.join(project_root, "dist", f"{output_name}.exe")
    final_exe = os.path.join(project_root, f"{output_name}.exe")

    if os.path.isfile(dist_exe):
        shutil.copy2(dist_exe, final_exe)
        print(f"[✓] EXE 已生成: {final_exe}")
    else:
        print(f"[✗] 构建失败: {dist_exe} 不存在")
        sys.exit(1)

    # ── clean up build artifacts ───────────────────────────────
    for path in [
        os.path.join(project_root, "build"),
        os.path.join(project_root, "dist"),
        os.path.join(project_root, f"{output_name}.spec"),
    ]:
        if os.path.isfile(path):
            os.remove(path)
            print(f"    - 删除: {path}")
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            print(f"    - 删除: {path}")

    print(f"[✓] 构建完成! 清理完毕。")
    print(f"    执行: {final_exe}")
    print(f"    设定檔路径: %APPDATA%\\压枪脚本\\config.json")


if __name__ == "__main__":
    main()
