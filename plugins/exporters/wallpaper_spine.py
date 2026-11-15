"""Spine 动态立绘壁纸导出：Spine 4.2 运行时 + 待机动画 + 点击互动 + 语音字幕。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.registry import ExporterPlugin
from core.voice import export_voice
from core.wallpaper import (
    ROOT,
    TEMPLATES,
    _vendor_file,
    WALLPAPER_LAYOUT,
    bg_css_for_skin,
    export_defaults,
    generate_preview_image,
    layout_storage_key,
    preview_palette,
    project_dir_name,
    project_json,
    render_template,
)

# 开发版在 frontend/public/vendor，打包版在 frontend/dist/vendor
SPINE_RUNTIME = _vendor_file("spine-webgl.min.js")
SPINE_APP = _vendor_file("spine-app.js")

DEFAULT_OPTIONS = {
    "voice": True,
    "interact": True,
    "subtitle": True,
    "volume": 80,
    "idleInterval": 60,
    "speed": 100,
    "intro": True,
}


def _identity(skin: dict) -> str:
    return str(skin.get("character") or skin.get("name") or skin.get("bundle") or "character")


def _slider(text: str, value: int, lo: int, hi: int, order: int, index: int) -> dict:
    return {
        "text": text,
        "type": "slider",
        "value": int(value),
        "min": lo,
        "max": hi,
        "editable": True,
        "fraction": False,
        "index": index,
        "order": order,
    }


def _bool(text: str, value: bool, order: int, index: int) -> dict:
    return {
        "text": text,
        "type": "bool",
        "value": bool(value),
        "editable": True,
        "index": index,
        "order": order,
    }


def _build_actions(skin: dict, src_dir: Path, voice_config: dict | None) -> dict:
    """CfgSpineAction → 骨骼坐标热区 + 语音文件映射（供运行时点击命中）。"""
    actions = skin.get("actions") or {}
    items = actions.get("items") or []
    audio_map = actions.get("audio") or {}
    if not items:
        return {"items": []}

    ui = {"pos": [0.0, 0.0], "scale": 1.0}
    meta_path = Path(src_dir) / "spine.json"
    if meta_path.is_file():
        try:
            ui = (json.loads(meta_path.read_text(encoding="utf-8")).get("ui") or ui)
        except Exception:  # noqa: BLE001
            pass
    px, py = float(ui["pos"][0]), float(ui["pos"][1])
    scale = float(ui.get("scale") or 1.0) or 1.0

    voice_files: dict[str, str] = {}
    for line in (voice_config or {}).get("lines", []):
        voice_files[line["cue"]] = line["file"]

    out: list[dict] = []
    for item in items:
        name = item.get("sName") or ""
        if not name or name == "in":
            continue
        rects = []
        for area in item.get("areas") or []:
            if not isinstance(area, list) or len(area) < 4:
                continue
            x, y, w, h = (float(area[0]), float(area[1]), float(area[2]), float(area[3]))
            angle = float(area[4]) if len(area) > 4 else 0.0
            rects.append([
                (x - px) / scale, (y - py) / scale,
                w / scale, h / scale, angle,
            ])
        if not rects:
            continue
        audio = []
        for aid in item.get("audioId") or []:
            info = audio_map.get(str(aid)) or {}
            cue = info.get("cue") or ""
            file = voice_files.get(cue)
            if not file:
                continue
            audio.append({"file": file, "text": info.get("text") or ""})
        entry = {
            "name": name,
            "type": item.get("sType"),
            "index": item.get("index"),
            "hideTxt": int(item.get("hideTxt") or 0),
            "rects": rects,
            "audio": audio,
        }
        content = item.get("content") or {}
        if content.get("activation"):
            entry["activation"] = {str(k): int(v) for k, v in content["activation"].items()}
        if content.get("isHide") is not None:
            entry["isHide"] = int(content["isHide"])
        if content.get("changeIdle"):
            change = content["changeIdle"]
            entry["changeIdle"] = {
                "anim": str(change[0]),
                "delay": int(change[1]) if len(change) > 1 else 0,
            }
        if content.get("clicks"):
            entry["clicks"] = [float(x) for x in content["clicks"]]
        if content.get("clickTime"):
            entry["clickTime"] = int(content["clickTime"])
        if item.get("gesture"):
            entry["gesture"] = int(item["gesture"])
        if content.get("gestureDatas"):
            datas = content["gestureDatas"]
            entry["gestureDatas"] = {
                "speed": float(datas.get("speed") or 2.0),
                "limitPerc": float(datas.get("limitPerc") or 1.0),
                "stopTime": float(datas.get("stopTime") or 0),
            }
        out.append(entry)
    return {"items": out}


class WallpaperSpineExporter(ExporterPlugin):
    id = "wallpaper_spine"
    name = "Spine 动态立绘壁纸导出"

    def export(self, skin: dict, options: dict, out_dir: str) -> str:
        for required in (SPINE_RUNTIME, SPINE_APP, WALLPAPER_LAYOUT):
            if not required.is_file():
                raise FileNotFoundError(f"运行时文件缺失: {required}")

        identity = _identity(skin)
        display_name = str(skin.get("name") or skin.get("painting") or "Spine")
        proj = Path(out_dir) / project_dir_name(identity, display_name)
        assets = proj / "assets"
        assets.mkdir(parents=True, exist_ok=False)

        asset = skin.get("asset") or {}
        src_dir = Path(options["root"]) / "resources" / str(asset.get("dir") or "")
        model_name = str(asset.get("model") or "character.json")
        atlas_name = str(asset.get("atlas") or "character.atlas")
        image_name = str(asset.get("image") or "character.png")
        for name, dest_name in (
            (model_name, "character.json"),
            (atlas_name, "character.atlas"),
            (image_name, "character.png"),
        ):
            source = src_dir / name
            if not source.is_file():
                raise FileNotFoundError(f"Spine 产物缺失: {source}")
            shutil.copy2(source, assets / dest_name)

        vendor = proj / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SPINE_RUNTIME, vendor / "spine-webgl.min.js")
        shutil.copy2(SPINE_APP, proj / "spine-app.js")
        shutil.copy2(WALLPAPER_LAYOUT, proj / "wallpaper-layout.js")

        # 语音：按装束的 voice_id 解码/复制互动 + 待机台词
        voice_config = export_voice(skin, assets)
        voice_json = {"lines": [], "base": "assets/voice/"}
        if voice_config:
            voice_json["lines"] = [
                {"cue": item["cue"], "category": item["category"],
                 "text": item["text"], "file": item["file"]}
                for item in voice_config.get("lines", [])
            ]

        # 点击热区：把 CfgSpineAction 的 UI 坐标按 prefab pos/scale 换算成骨骼坐标
        actions_payload = _build_actions(skin, src_dir, voice_config)

        opts = {**DEFAULT_OPTIONS}
        for key in DEFAULT_OPTIONS:
            if key in options:
                opts[key] = options[key]
        opts["volume"] = max(0, min(100, int(opts.get("volume") or 80)))
        opts["idleInterval"] = max(0, min(600, int(opts.get("idleInterval") or 60)))
        opts["speed"] = max(25, min(200, int(opts.get("speed") or 100)))

        cover = src_dir / "cover.png"
        html = render_template(
            "wallpaper_spine.html",
            VOICE_JSON=json.dumps(voice_json, ensure_ascii=False),
            BG_JSON=json.dumps({
                "palette": preview_palette(skin, options.get("root")),
                "mode": options.get("bg", "monet"),
            }, ensure_ascii=False),
            OPTIONS_JSON=json.dumps({
                "voice": bool(opts["voice"]),
                "interact": bool(opts["interact"]),
                "subtitle": bool(opts["subtitle"]),
                "volume": opts["volume"] / 100,
                "idleInterval": opts["idleInterval"],
                "targetFps": 60,
                "speed": opts["speed"],
                "intro": bool(opts["intro"]),
            }, ensure_ascii=False),
            LAYOUT_STORAGE_KEY=json.dumps(layout_storage_key(skin, "spine"), ensure_ascii=False),
            BG_CSS=bg_css_for_skin(
                skin, options.get("root"), options.get("bg", "monet"), options.get("bgColor")
            ),
            **export_defaults(options),
        )
        (assets / "actions.json").write_text(
            json.dumps(actions_payload, ensure_ascii=False), encoding="utf-8"
        )
        (proj / "index.html").write_text(html, encoding="utf-8")

        extra = {
            "voice": _bool("语音", bool(opts["voice"]), 110, 10),
            "interact": _bool("点击互动", bool(opts["interact"]), 111, 11),
            "subtitle": _bool("台词字幕", bool(opts["subtitle"]), 112, 12),
            "volume": _slider("音量", opts["volume"], 0, 100, 105, 5),
            "targetfps": _slider("目标帧率", 60, 15, 200, 104, 4),
            "showareas": _bool("交互区域", bool(options.get("showAreas", False)), 114, 14),
            "opening": _bool("开场动画", bool(opts["intro"]), 113, 13),
        }
        (proj / "project.json").write_text(
            json.dumps(
                project_json(
                    f"{identity} · {display_name}",
                    scale=options.get("scale", 100),
                    offset_x=options.get("offsetX", 0),
                    offset_y=options.get("offsetY", 0),
                    alignment=options.get("alignment", 0),
                    extra_properties=extra,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        generate_preview_image(
            proj,
            image_file=cover if cover.is_file() else None,
            palette=preview_palette(skin, options.get("root")),
            bg=options.get("bg", "monet"),
            bg_color=options.get("bgColor"),
            title=identity,
            subtitle=display_name,
        )
        return str(proj)
