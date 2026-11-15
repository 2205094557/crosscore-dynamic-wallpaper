"""交错战线语音管线：CriWare ACB 下载 → vgmstream 逐条解码 → 壁纸用语音目录。

数据来源：
  - CDN: https://cdn.megagamelog.com/cross/release/android/curr/sounds/{cue_sheet}
  - 台词目录: resources/metadata/voices.json（voice_id → cue/分类/文本，来自游戏 Sound 表）
  - cue 名与 stream 序号不是顺序对应（`alps_1` 可能在第 5 条流），必须用
    vgmstream -m 逐条读 `stream name` 建映射，再按序号解码。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from plugins.sources.crosscore_cdn import _download_file, asset_url

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
VOICE_ROOT = ROOT / "resources" / "voice"
BUNDLE_ROOT = ROOT / "resources" / "bundles" / "voice"
VGMSTREAM_DIR = ROOT / "tools" / "vgmstream"
VGMSTREAM_CLI = VGMSTREAM_DIR / "vgmstream-cli.exe"

# 壁纸会播放的分类（其余战斗/剧情语音不下载，省带宽与体积）
PLAYABLE_CATEGORIES = (
    "看板", "登录", "获得", "CrossCore", "生日",
    "接触1", "接触2", "接触3", "接触4", "接触5", "接触6", "接触7",
    "语气词（开心）", "语气词（讨厌）",
)

_STREAM_COUNT_RE = re.compile(r"stream count:\s*(\d+)")
_STREAM_NAME_RE = re.compile(r"stream name:\s*(.+)")


def _vgmstream(args: list[str]) -> str:
    env = dict(os.environ)
    env["PATH"] = str(VGMSTREAM_DIR) + os.pathsep + env.get("PATH", "")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(VGMSTREAM_CLI), *args],
        capture_output=True, text=True, env=env, timeout=600,
        creationflags=flags, encoding="utf-8", errors="replace",
    )
    return result.stdout or result.stderr


def load_catalog() -> dict:
    path = MD / "voices.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data.get("voices", {}) if isinstance(data, dict) else {}


def _voice_dir(voice_id) -> Path:
    return VOICE_ROOT / str(voice_id)


def _cue_map(acb: Path, cache: Path) -> dict[str, int]:
    """cue 名 → stream 序号；结果缓存，避免每次导出都跑几十次 vgmstream。"""
    if cache.is_file():
        try:
            return {str(k): int(v) for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001
            pass
    meta = _vgmstream(["-m", str(acb)])
    match = _STREAM_COUNT_RE.search(meta)
    count = int(match.group(1)) if match else 0
    mapping: dict[str, int] = {}
    for index in range(1, count + 1):
        text = _vgmstream(["-m", "-s", str(index), str(acb)])
        name = _STREAM_NAME_RE.search(text)
        if name:
            mapping[name.group(1).strip()] = index
    if mapping:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    return mapping


def _decode(acb: Path, index: int, dest: Path) -> bool:
    if dest.is_file() and dest.stat().st_size > 1024:
        return True
    out = _vgmstream(["-o", str(dest), "-s", str(index), str(acb)])
    if dest.is_file() and dest.stat().st_size > 1024:
        return True
    print(f"[voice] 解码失败 {acb.name}#{index}: {out[-300:]}", flush=True)
    dest.unlink(missing_ok=True)
    return False


def _sheet_acb(sheet: str, emit=None, cancel_event=None) -> Path | None:
    acb_name = sheet.replace("/", "_")
    acb = BUNDLE_ROOT / acb_name
    if acb.is_file():
        return acb
    if emit:
        emit(f"下载语音 {Path(sheet).name}", str(sheet))
    ok = _download_file(asset_url(f"sounds/{sheet}"), acb,
                        cancel_event=cancel_event, emit=emit, expected_min=1024)
    return acb if ok else None


def _collect_lines(skin: dict, entry: dict) -> list[dict]:
    """语音行 = 目录里的互动/待机台词 + CfgSpineAction 里点击动作引用的台词。"""
    lines: list[dict] = []
    seen: set[str] = set()
    for line in entry.get("lines", []):
        cue = str(line.get("cue") or "")
        if not cue or cue in seen:
            continue
        seen.add(cue)
        lines.append({
            "cue": cue,
            "category": line.get("category") or "",
            "text": (line.get("text") or "").strip(),
            "sheet": line.get("sheet") or entry.get("sheet") or "",
        })
    for audio in ((skin.get("actions") or {}).get("audio") or {}).values():
        cue = str((audio or {}).get("cue") or "")
        if not cue or cue in seen:
            continue
        seen.add(cue)
        lines.append({
            "cue": cue,
            "category": (audio.get("category") or "").strip(),
            "text": (audio.get("text") or "").strip(),
            "sheet": audio.get("sheet") or entry.get("sheet") or "",
        })
    return lines


def ensure_voice(skin: dict, emit=None, cancel_event=None) -> dict | None:
    """下载并解码该装束需要的语音（含点击动作引用），返回壁纸运行时配置。"""
    voice_id = skin.get("voice_id")
    if not voice_id:
        return None
    catalog = load_catalog()
    entry = catalog.get(str(voice_id)) or {}
    sheet_default = str(entry.get("sheet") or skin.get("voice_sheet") or "")
    lines = _collect_lines(skin, entry) if entry else [
        {**audio, "sheet": audio.get("sheet") or sheet_default}
        for audio in ((skin.get("actions") or {}).get("audio") or {}).values()
        if audio.get("cue")
    ]
    if not lines:
        return None

    out_dir = _voice_dir(voice_id)
    config_path = out_dir / "voice.json"
    expected = {line["cue"] for line in lines}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            cached = {item["cue"]: item for item in config.get("lines", [])}
            # 必须覆盖本次需要的全部 cue（动作新增台词会让旧缓存失效）
            if expected <= set(cached) and all(
                (out_dir / cached[cue]["file"]).is_file() for cue in expected
            ):
                return config
        except Exception:  # noqa: BLE001
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    by_sheet: dict[str, list[dict]] = {}
    for line in lines:
        by_sheet.setdefault(line["sheet"] or sheet_default, []).append(line)

    for sheet, sheet_lines in by_sheet.items():
        if not sheet:
            continue
        acb = _sheet_acb(sheet, emit=emit, cancel_event=cancel_event)
        if acb is None:
            continue
        mapping = _cue_map(acb, BUNDLE_ROOT / f"{acb.stem}.cues.json")
        for line in sheet_lines:
            cue = line["cue"]
            index = mapping.get(cue)
            if not index:
                continue
            wav = f"{cue}.wav"
            if _decode(acb, index, out_dir / wav):
                kept.append({
                    "cue": cue,
                    "category": line.get("category") or "",
                    "text": (line.get("text") or "").strip(),
                    "sheet": sheet,
                    "file": wav,
                    "index": index,
                })
            if cancel_event is not None and cancel_event.is_set():
                return None

    by_category: dict[str, list[str]] = {}
    for item in kept:
        by_category.setdefault(item["category"], []).append(item["cue"])
    config = {
        "voice_id": voice_id,
        "sheets": sorted(by_sheet),
        "lines": kept,
        "categories": by_category,
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    if emit:
        emit(f"语音就绪：{len(kept)} 条", str(voice_id))
    return config


def export_voice(skin: dict, dest_dir: Path, config: dict | None = None) -> dict | None:
    """把语音文件与运行时配置复制进壁纸项目 assets/voice，返回运行时配置。"""
    config = config or ensure_voice(skin)
    if not config:
        return None
    src = _voice_dir(config["voice_id"])
    dest = Path(dest_dir) / "voice"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for item in config.get("lines", []):
        source = src / item["file"]
        if source.is_file():
            shutil.copy2(source, dest / item["file"])
            copied.append(item)
    if not copied:
        return None
    config = {**config, "lines": copied}
    (dest / "voice.json").write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    return config
