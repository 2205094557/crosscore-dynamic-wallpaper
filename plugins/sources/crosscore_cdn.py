"""交错战线 CDN 来源插件。

实测协议（2026-11）：
    基础地址  https://cdn.megagamelog.com/cross/release/{platform}/curr/
    Custom 资产  Custom/{key}            立绘/Spine 预制体（UnityFS，带伪文件头）
    语音资产     sounds/cv/...             CriWare ACB（见 core/voice.py）

注意：镜像清单里的 `curr_1` 通道只有 Custom 资源；`curr` 通道同时提供
Custom 与 sounds。两条通道的 Custom 资源一致，统一用 `curr` 即可。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.registry import SourcePlugin

ROOT = Path(__file__).resolve().parents[2]
METADATA = ROOT / "resources" / "metadata"

PLATFORM = os.environ.get("CROSSCORE_CDN_PLATFORM", "android")
CHANNEL = "curr"
CDN_BASE = f"https://cdn.megagamelog.com/cross/release/{PLATFORM}/{CHANNEL}/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) crosscore-wallpaper"
TIMEOUT = 120
RETRIES = 3


def asset_url(key: str) -> str:
    """CDN key（Custom/... 或 sounds/...）→ 完整 URL。
    官方资源名可能带空格等怪字符（如 `..._zues04 _spine`），必须 URL 编码。"""
    return CDN_BASE + urllib.parse.quote(key.lstrip("/"))


def _download_file(url: str, dest: Path, cancel_event=None, emit=None,
                   expected_min: int = 1024) -> bool:
    """带重试与取消的原子下载；返回是否成功。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, RETRIES + 1):
        if cancel_event is not None and cancel_event.is_set():
            tmp.unlink(missing_ok=True)
            return False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                last_pct = -10
                with open(tmp, "wb") as f:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("cancelled")
                        chunk = resp.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if emit and total:
                            pct = done * 100 // total
                            if pct - last_pct >= 10:
                                last_pct = pct
                                emit(f"下载中 {pct}%")
            size = tmp.stat().st_size
            if size < expected_min:
                raise ValueError(f"下载内容过小: {size} B")
            os.replace(tmp, dest)
            return True
        except InterruptedError:
            tmp.unlink(missing_ok=True)
            return False
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as ex:
            tmp.unlink(missing_ok=True)
            if emit:
                emit(f"下载失败（{attempt}/{RETRIES}）：{ex}")
            if attempt == RETRIES:
                return False
            time.sleep(1.5 * attempt)
    return False


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


class CrossCoreSource(SourcePlugin):
    id = "crosscore"
    name = "交错战线官方 CDN"

    def list_bundles(self):
        path = METADATA / "skins.json"
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("skins", []) if isinstance(data, dict) else []

    def download(self, skin: dict, out_dir, cancel_event=None, emit=None) -> bool:
        stype = str(skin.get("type") or "")
        painting = str(skin.get("painting") or "").lower()
        if not painting:
            raise ValueError("装束缺少 painting 标识")

        key = skin.get("spine_key") if stype == "spine" else skin.get("draw_key")
        if not key:
            raise ValueError(f"该装束没有 {stype} 资源键")
        dest = Path(out_dir) / stype / painting / "asset.bin"
        _remove(dest.parent)
        if emit:
            emit(f"下载 {Path(key).name}", painting)
        ok = _download_file(asset_url(key), dest, cancel_event=cancel_event, emit=emit)
        if not ok:
            return False

        # Spine 装束额外取一张静态立绘作封面/图鉴缩略图（存在才下载）
        if stype == "spine" and skin.get("draw_key"):
            draw_dest = dest.parent / "draw.bin"
            _download_file(asset_url(skin["draw_key"]), draw_dest,
                           cancel_event=cancel_event, emit=None, expected_min=1024)

        # 立绘表情差分：与主图同目录，供静态导出的可选叠加（存在才下载）
        if stype == "static":
            face_key = f"{key}_face"
            if face_key in _asset_index():
                face_dest = dest.parent / "face.bin"
                _download_file(asset_url(face_key), face_dest,
                               cancel_event=cancel_event, emit=None, expected_min=512)
        (dest.parent / "source.json").write_text(
            json.dumps({"key": key, "type": stype, "painting": painting}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True


_INDEX_CACHE: set[str] | None = None


def _asset_index() -> set[str]:
    """asset_list.txt 的 key 集合（懒加载，供可选资源探测）。"""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    path = METADATA / "asset_list.txt"
    try:
        keys = {line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
    except Exception:  # noqa: BLE001
        keys = set()
    _INDEX_CACHE = keys
    return keys
