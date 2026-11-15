"""Spine 动态立绘提取：prefabs_spine_* → character.json/atlas/png。

游戏使用 Spine 4.2（Unity spine-unity SkeletonGraphic）。资源包里同时带
角色骨架与特效骨架，取体积最大的 skeleton JSON 作为角色本体。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExtractorPlugin
from core.unityfs import largest_texture, load_bundle, texture_to_png


def _asset_path(bundle_dir: Path) -> Path:
    for name in ("asset.bin", "bundle.bin"):
        path = bundle_dir / name
        if path.is_file():
            return path
    for path in sorted(bundle_dir.iterdir()):
        if path.is_file() and path.stat().st_size > 1024:
            return path
    raise FileNotFoundError(f"目录中没有资源包: {bundle_dir}")


def _text_of(text_asset) -> str:
    text = text_asset.m_Script
    return text.decode("utf-8", "replace") if isinstance(text, bytes) else text


class CrossCoreSpineExtractor(ExtractorPlugin):
    id = "spine"
    name = "交错战线 Spine 提取"

    def extract(self, bundle_path: str, out_dir: str) -> dict:
        bundle_dir = Path(bundle_path)
        out = Path(out_dir)
        source = _asset_path(bundle_dir)

        env = load_bundle(source)
        ui_transform = None
        texts: dict[str, str] = {}
        for obj in env.objects:
            if obj.type.name == "RectTransform" and ui_transform is None:
                rect = obj.read()
                try:
                    go = rect.m_GameObject.read()
                except Exception:  # noqa: BLE001
                    go = None
                if go is not None and go.m_Name == "pos":
                    anchored = rect.m_AnchoredPosition
                    scale = rect.m_LocalScale
                    ui_transform = {
                        "pos": [float(anchored.x), float(anchored.y)],
                        "scale": float(scale.x) or 1.0,
                    }
            if obj.type.name == "TextAsset":
                data = obj.read()
                texts[data.m_Name] = _text_of(data)

        atlas_name = next((name for name in texts if name.lower().endswith(".atlas")), None)
        json_names = [
            name for name, text in texts.items()
            if text.lstrip().startswith("{") and '"skeleton"' in text[:400]
        ]
        if not atlas_name or not json_names:
            raise ValueError(f"资源包内没有 Spine 资源: {source}（texts={list(texts)[:5]}）")
        skeleton_name = max(json_names, key=lambda name: len(texts[name]))
        skeleton = json.loads(texts[skeleton_name])
        atlas_text = texts[atlas_name]

        texture = largest_texture(env)
        if texture is None:
            raise ValueError(f"资源包内没有 Spine 贴图: {source}")

        out.mkdir(parents=True, exist_ok=True)
        page = atlas_text.splitlines()[0].strip()
        atlas_text = atlas_text.replace(page, "character.png", 1)
        (out / "character.json").write_text(
            json.dumps(skeleton, ensure_ascii=False), encoding="utf-8"
        )
        (out / "character.atlas").write_text(atlas_text, encoding="utf-8")
        width, height = texture_to_png(texture, out / "character.png")

        animations = skeleton.get("animations") or {}
        skins = skeleton.get("skins") or {}
        meta = {
            "version": str((skeleton.get("skeleton") or {}).get("spine") or ""),
            "size": [
                (skeleton.get("skeleton") or {}).get("width"),
                (skeleton.get("skeleton") or {}).get("height"),
            ],
            "animations": list(animations.keys()) if isinstance(animations, dict)
            else [item.get("name") for item in animations],
            "skins": list(skins.keys()) if isinstance(skins, dict)
            else [item.get("name") for item in skins],
            "atlas_page": page,
            "texture": {"width": width, "height": height},
            "source_key": source.name,
            "ui": ui_transform,
        }
        (out / "spine.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        shutil.copy2(source, out / "asset.bin")

        # 附带的静态立绘（来源插件顺手下载）→ cover.png：封面与图鉴缩略图
        cover = bundle_dir / "draw.bin"
        if cover.is_file():
            try:
                cover_env = load_bundle(cover)
                cover_tex = largest_texture(cover_env)
                if cover_tex is not None:
                    texture_to_png(cover_tex, out / "cover.png")
            except Exception as ex:  # noqa: BLE001
                print(f"[spine] 封面立绘提取失败（忽略）: {ex}", flush=True)

        return {"type": "spine", **meta}
