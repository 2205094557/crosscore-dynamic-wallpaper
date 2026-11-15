"""静态立绘壁纸导出插件：把标准 painting.png 产物导出为 WE web 壁纸。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExporterPlugin
from core.wallpaper import (
    WALLPAPER_LAYOUT,
    bg_css_for_skin,
    export_defaults,
    generate_preview_image,
    layout_storage_key,
    project_dir_name,
    project_json,
    render_template,
)


def _identity(skin: dict) -> str:
    return str(
        skin.get("character")
        or skin.get("ship")
        or skin.get("name")
        or skin.get("id")
        or skin.get("mbcc")
        or "character"
    )


class WallpaperStaticExporter(ExporterPlugin):
    id = "wallpaper_static"
    name = "静态立绘壁纸导出"

    def export(self, skin: dict, options: dict, out_dir: str) -> str:
        identity = _identity(skin)
        display_name = str(skin.get("name") or skin.get("painting") or "Static")
        proj = Path(out_dir) / project_dir_name(identity, display_name)
        assets = proj / "assets"
        assets.mkdir(parents=True, exist_ok=False)

        asset = skin.get("asset") or {}
        src_dir = Path(options["root"]) / "resources" / str(asset.get("dir") or "")
        image = str(asset.get("image") or "painting.png")
        source = src_dir / image
        if not source.is_file():
            raise FileNotFoundError(f"静态立绘不存在: {source}")
        if not WALLPAPER_LAYOUT.is_file():
            raise FileNotFoundError(f"壁纸布局运行时不存在: {WALLPAPER_LAYOUT}")

        shutil.copy2(source, assets / image)
        shutil.copy2(WALLPAPER_LAYOUT, proj / "wallpaper-layout.js")

        html = render_template(
            "wallpaper_static.html",
            IMAGE="assets/" + image,
            LAYOUT_STORAGE_KEY=json.dumps(layout_storage_key(skin, "static"), ensure_ascii=False),
            BG_CSS=bg_css_for_skin(
                skin, options.get("root"), options.get("bg", "monet"), options.get("bgColor")
            ),
            **export_defaults(options),
        )
        (proj / "index.html").write_text(html, encoding="utf-8")
        (proj / "project.json").write_text(
            json.dumps(
                project_json(
                    f"{identity} · {display_name}",
                    scale=options.get("scale", 100),
                    offset_x=options.get("offsetX", 0),
                    offset_y=options.get("offsetY", 0),
                    alignment=options.get("alignment", 0),
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # 静态立绘原图就是完整立绘，直接合成封面；取色失败时用固定渐变。
        generate_preview_image(
            proj,
            image_file=source,
            bg=options.get("bg", "monet"),
            bg_color=options.get("bgColor"),
        )
        return str(proj)
