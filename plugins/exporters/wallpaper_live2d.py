"""Live2D 壁纸导出插件：生成通用 Wallpaper Engine web 壁纸项目。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExporterPlugin
from core.wallpaper import (
    L2D_APP,
    L2D_CORE,
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


class WallpaperLive2DExporter(ExporterPlugin):
    id = "wallpaper_live2d"
    name = "Live2D 壁纸导出"

    def export(self, skin: dict, options: dict, out_dir: str) -> str:
        identity = _identity(skin)
        display_name = str(skin.get("name") or skin.get("painting") or "Live2D")
        proj = Path(out_dir) / project_dir_name(identity, display_name)
        model_dir = proj / "assets" / "model"
        model_dir.mkdir(parents=True, exist_ok=False)

        asset = skin.get("asset") or {}
        src_dir = Path(options["root"]) / "resources" / str(asset.get("dir") or "")
        model_file = str(asset.get("model") or "")
        if not src_dir.is_dir():
            raise FileNotFoundError(f"Live2D 资源目录不存在: {src_dir}")
        if not model_file or not (src_dir / model_file).is_file():
            raise FileNotFoundError(f"Live2D model3.json 不存在: {src_dir / model_file}")
        if not list(src_dir.rglob("*.moc3")):
            raise FileNotFoundError(f"Live2D 资源缺少 moc3: {src_dir}")

        # 标准提取产物整体复制：model3/moc3/纹理/motions 的相对路径保持不变。
        for source in src_dir.rglob("*"):
            if not source.is_file():
                continue
            target = model_dir / source.relative_to(src_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        for runtime in (L2D_CORE, L2D_APP, WALLPAPER_LAYOUT):
            if not runtime.is_file():
                raise FileNotFoundError(f"壁纸运行时不存在: {runtime}")
        shutil.copy2(L2D_CORE, proj / "live2dcubismcore.min.js")
        shutil.copy2(L2D_APP, proj / "live2d-app.js")
        shutil.copy2(WALLPAPER_LAYOUT, proj / "wallpaper-layout.js")

        track_on = bool(options.get("track", True))
        html = render_template(
            "wallpaper_live2d.html",
            L2D_CONFIG_JSON=json.dumps({"model": "assets/model/" + model_file}, ensure_ascii=False),
            LAYOUT_STORAGE_KEY=json.dumps(layout_storage_key(skin, "live2d"), ensure_ascii=False),
            BG_CSS=bg_css_for_skin(
                skin, options.get("root"), options.get("bg", "monet"), options.get("bgColor")
            ),
            TRACK="true" if track_on else "false",
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
                    l2d=True,
                    track=track_on,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # 兜底封面：正常 UI 导出会由前端提供完整立绘截图并覆盖此文件；
        # CLI/无预览上下文的导出也必须有真实 preview 文件（WE 缺图会持续报错）。
        generate_preview_image(
            proj,
            bg=options.get("bg", "monet"),
            bg_color=options.get("bgColor"),
            title=f"{identity} · {display_name}",
        )
        return str(proj)
