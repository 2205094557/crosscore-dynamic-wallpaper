"""静态立绘提取：Custom/textures_bigs_character_*_draw → painting.png。"""

from __future__ import annotations

import shutil
from pathlib import Path

from core.registry import ExtractorPlugin
from core.unityfs import largest_texture, load_bundle, texture_to_png


def _find_asset(bundle_dir: Path) -> Path:
    for name in ("asset.bin", "bundle.bin"):
        path = bundle_dir / name
        if path.is_file():
            return path
    candidates = sorted(bundle_dir.glob("*"))
    for path in candidates:
        if path.is_file() and path.stat().st_size > 1024:
            return path
    raise FileNotFoundError(f"目录中没有资源包: {bundle_dir}")


class CrossCoreStaticExtractor(ExtractorPlugin):
    id = "static"
    name = "交错战线静态立绘提取"

    def extract(self, bundle_path: str, *args) -> dict:
        """兼容框架的 (bundle_dir, tex_dir, out_dir) 与简化的 (bundle_dir, out_dir)。"""
        if not args:
            raise ValueError("缺少输出目录")
        out_dir = Path(args[-1])
        bundle_dir = Path(bundle_path)
        source = _find_asset(bundle_dir)

        env = load_bundle(source)
        texture = largest_texture(env)
        if texture is None:
            raise ValueError(f"资源包内没有 Texture2D: {source}")

        out_dir.mkdir(parents=True, exist_ok=True)
        width, height = texture_to_png(texture, out_dir / "painting.png")

        face = bundle_dir / "face.bin"
        if face.is_file():
            try:
                face_env = load_bundle(face)
                face_tex = largest_texture(face_env)
                if face_tex is not None:
                    texture_to_png(face_tex, out_dir / "painting_face.png")
            except Exception as ex:  # noqa: BLE001
                print(f"[static] 表情差分提取失败（忽略）: {ex}", flush=True)

        shutil.copy2(source, out_dir / "asset.bin")
        return {"type": "static", "image": "painting.png", "width": width, "height": height}
