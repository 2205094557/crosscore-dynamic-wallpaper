# -*- mode: python ; coding: utf-8 -*-
"""交错战线壁纸工具桌面打包：onedir + 运行时代码 + 少量图鉴数据。"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)


def existing_data(source: str, destination: str) -> list[tuple[str, str]]:
    path = ROOT / source
    return [(str(path), destination)] if path.exists() else []


datas: list[tuple[str, str]] = []
datas += existing_data("templates", "templates")
datas += existing_data("frontend/dist", "frontend/dist")
datas += existing_data("plugins", "plugins")
datas += existing_data("core", "core")
# tools/ 里同时带 build_local_index、vgmstream 可执行文件与 DLL（语音解码必需）
datas += existing_data("tools", "tools")
datas += existing_data("resources/config.json", "resources")

METADATA_FILES = (
    "characters.json",
    "skins.json",
    "voices.json",
    "asset_list.txt",
)
for filename in METADATA_FILES:
    datas += existing_data(f"resources/metadata/{filename}", "resources/metadata")

binaries = []
hiddenimports = [
    "core.registry",
    "core.metadata",
    "core.library",
    "core.palette",
    "core.we_integration",
    "core.wallpaper",
    "core.voice",
    "core.unityfs",
    "plugins.sources.crosscore_cdn",
    "plugins.extractors.crosscore_spine",
    "plugins.extractors.crosscore_static",
    "plugins.exporters.wallpaper_spine",
    "plugins.exporters.wallpaper_static",
    "requests",
    "PIL",
    "coloredlogs",
    "humanfriendly",
]

# UnityPy 带原生解码器，必须整包收集
package_datas, package_bins, package_hidden = collect_all("UnityPy")
datas += package_datas
binaries += package_bins
hiddenimports += package_hidden


a = Analysis(
    ["app_pack.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_path = ROOT / "resources" / "icon.ico"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="crosscore-wallpaper",
    icon=str(icon_path) if icon_path.exists() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="crosscore-wallpaper",
)
