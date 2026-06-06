"""PyInstaller 打包脚本"""

import sys
import subprocess
from pathlib import Path


def build() -> None:
    project_root = Path(__file__).parent

    # 用 repr 转义 Windows 反斜杠，避免 f-string 内产生非法转义序列
    project_root_str = repr(str(project_root))
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# 隐藏导入
hidden_imports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "httpx",
    "gradio_client",
    "huggingface_hub",
    "anyio",
    "httpcore",
    "certifi",
]

a = Analysis(
    ["src/main.py"],
    pathex=[{project_root_str}],
    binaries=[],
    datas=[
        ("src/resources/style.qss", "src/resources"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "torch", "tensorflow", "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="IndexTTS-GUI2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''

    spec_file = project_root / "build_exe.spec"
    spec_file.write_text(spec_content, encoding="utf-8")
    print(f"已生成 spec 文件: {spec_file}")

    # 运行 PyInstaller
    print("开始打包...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", str(spec_file)],
        cwd=str(project_root),
    )
    if result.returncode == 0:
        print("打包成功！输出在 dist/ 目录")
    else:
        print(f"打包失败，退出码: {result.returncode}")


if __name__ == "__main__":
    build()
