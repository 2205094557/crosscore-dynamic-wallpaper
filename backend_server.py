"""本地后端 API 服务：资源下载/提取/导出/取色等接口，供前端（浏览器/桌面）调用。

启动: python backend_server.py [端口]   （默认 8766）

M0 里程碑说明：本项目为 交错战线 动态壁纸工具的空壳骨架——
后端可启动、路由齐备，但资源来源插件（plugins/sources）尚未注册，
下载接口统一返回 501 占位错误；元数据同步指向 tools/build_dw_metadata.py
占位脚本（由后续里程碑提供）。
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from core.library import Library  # noqa: E402
from core.locks import named_lock  # noqa: E402
from core.metadata import Metadata  # noqa: E402
from core.palette import extract_palette, fallback_palette, find_palette_source, mode_css  # noqa: E402
from core.wallpaper import bg_css  # noqa: E402
from core.registry import Registry  # noqa: E402
from core.we_integration import apply_wallpaper  # noqa: E402
from core.applog import setup_logging  # noqa: E402

registry = Registry(ROOT / "plugins").discover()
metadata = Metadata(ROOT / "resources" / "metadata")
library = Library(ROOT / "resources" / "library.db")
config = Config(ROOT / "resources" / "config.json")
logger = setup_logging()

# ---- 下载阶段事件推送（SSE）：前端右下角卡片实时显示 下载/提取/同步 等阶段 ----
_EVENT_QUEUES: list[queue.Queue] = []
_EVENT_LOCK = threading.Lock()


def _broadcast(name: str, data: dict) -> None:
    payload = f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _EVENT_LOCK:
        for q in _EVENT_QUEUES:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def _emit_stage(stage: str, **extra) -> None:
    _broadcast("stage", {"stage": stage, **extra})

_META_MTIMES: dict[str, float] = {}
# 批量下载取消：前端带 download_id 发起下载，点取消时置位对应 Event，
# 来源插件在下载循环中按块检查该事件，能快速停止正在进行的下载。
_CANCEL_LOCK = threading.Lock()
_CANCEL_EVENTS: dict[str, threading.Event] = {}


def _register_cancel(download_id: str) -> threading.Event:
    ev = threading.Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[download_id] = ev
    return ev


def _unregister_cancel(download_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(download_id, None)


def cancel_download(download_id: str) -> bool:
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(download_id)
    if ev is not None:
        ev.set()
        return True
    return False

# 本地索引与提取产物是共享文件，并发下载时多线程同时写会损坏/丢数据，必须串行化。
# 用进程内命名锁（打包版模块可能多实例，threading.Lock 不共享）。
_INDEX_LOCK = named_lock("dw_local_index")
# run_tool 与 live2d 提取器都会改写全局 sys.argv，必须共用同一把锁（与提取器同名）。
_TOOL_LOCK = named_lock("dw_sysargv")

# 元数据构建/同步占位脚本：由后续里程碑提供（BWiki 图鉴同步 + CDN 版本检查）。
METADATA_TOOL = "sync_crosscore_metadata"


def ensure_fresh_metadata() -> None:
    """每个请求前调用：元数据 JSON 被外部工具/脚本改动（mtime 变化）时自动重载，
    保证界面看到的数据和后端 API（下载/导出/取色）永远一致。"""
    changed = False
    for name in ("characters.json", "skins.json", "local_skins.json"):
        p = ROOT / "resources" / "metadata" / name
        mt = p.stat().st_mtime if p.exists() else 0.0
        if _META_MTIMES.get(name) != mt:
            _META_MTIMES[name] = mt
            changed = True
    if changed:
        metadata.reload()
        refresh_library()


def refresh_library() -> None:
    library.import_metadata(metadata.characters(), metadata.skins(), metadata.local_skins())


refresh_library()


def get_config() -> dict:
    """返回当前配置（含代理设置）。"""
    return {"ok": True, "config": config.data}


def set_config(patch: dict) -> dict:
    """更新配置并立即生效（代理 / 退出清理开关）。"""
    patch = patch or {}
    if "proxy" in patch:
        config.set("proxy", (patch.get("proxy") or "").strip())
    if "clean_bundles_on_exit" in patch:
        config.set("clean_bundles_on_exit", bool(patch.get("clean_bundles_on_exit")))
    return {"ok": True, "config": config.data}


def resolve_skin(character: str, bundle: str, name: str | None = None) -> dict | None:
    # bundle 在元数据里可能落盘为 null，API 侧统一为 ""，两侧归一化后再比较
    want_bundle = bundle or ""
    hits = [
        s for s in metadata.skins()
        if s.get("character") == character and (s.get("bundle") or "") == want_bundle
    ]
    if not hits:
        return None
    if name is None:
        return hits[0]
    for s in hits:
        if s.get("name") == name:
            return s
    # 名字对不上（占位名/旧名）：仅当 character+bundle 唯一候选时容忍，
    # 避免“同 key 多装束”误配。
    return hits[0] if len(hits) == 1 else None


def downloaded_with_painting() -> list[dict]:
    out = []
    for s in library.downloaded_skins():
        skin = resolve_skin(s["character"], s["bundle"], s.get("name"))
        out.append({**s, "painting": (skin or {}).get("painting", "")})
    return out


def skin_files(skin: dict) -> list[Path]:
    """返回该装束对应的本地 bundle 与提取产物路径（generic：live2d / static）。"""
    painting = (skin.get("painting") or "").lower()
    stype = skin.get("type", "")
    b = ROOT / "resources" / "bundles"
    e = ROOT / "resources" / "extracted"
    paths: list[Path] = []
    if stype == "live2d":
        paths += [b / "live2d" / painting, e / "live2d" / painting]
    elif stype in ("static", "spine"):
        paths += [b / stype / painting, e / stype / painting]
    return [p for p in paths if p.exists()]


def delete_skin_files(character: str, bundle: str, name: str | None = None) -> dict:
    skin = resolve_skin(character, bundle, name)
    if not skin:
        return {"ok": False, "error": "装束不存在"}
    removed = [str(p) for p in skin_files(skin)]
    for p in skin_files(skin):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink(missing_ok=True)
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()
    return {"ok": True, "removed": removed}


def delete_skins_batch(items: list[dict]) -> dict:
    """批量删除装束：只删文件，最后重建一次索引。

    逐个调用 delete_skin_files 时每个装束都会触发一次 build_local_index
    （全量扫描 extracted 目录），全选删除 N 个装束就是 N 次全量重建。
    批量版把索引重建从 O(N) 降到 1 次。
    """
    removed: list[str] = []
    failed: list[dict] = []
    for it in items or []:
        try:
            character = it.get("character", "")
            bundle = it.get("bundle", "")
            name = it.get("name")
            skin = resolve_skin(character, bundle, name)
            if not skin:
                failed.append({"character": character, "bundle": bundle, "name": name, "error": "装束不存在"})
                continue
            for p in skin_files(skin):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file():
                    p.unlink(missing_ok=True)
                removed.append(str(p))
        except Exception as e:  # noqa: BLE001
            failed.append({"character": it.get("character"), "error": str(e)})

    # 统一重建一次索引，保证索引与内存一致
    try:
        run_tool("build_local_index", timeout=120)
        metadata.reload()
        refresh_library()
    except Exception as e:  # noqa: BLE001
        print(f"[batch-delete] 索引重建失败: {e}")
    return {
        "ok": not failed,
        "removed_count": len(removed),
        "failed": failed,
        "failed_count": len(failed),
    }


def clear_all_downloads() -> dict:
    removed = []
    for sub in ("bundles", "extracted"):
        d = ROOT / "resources" / sub
        if d.is_dir():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed.append(str(child))
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()
    return {"ok": True, "removed": removed}


def _bundle_paths(stype: str, painting: str) -> list[Path]:
    """该装束（部件）的原始下载包路径。

    只返回本装束自身的 bundle 文件；来源插件管理的共享资源
    永远不在清理范围里。目录布局按 generic 约定 bundles/{type}/{painting}。
    """
    b = ROOT / "resources" / "bundles"
    painting = painting.lower()
    paths: list[Path] = []
    if stype == "live2d":
        paths += [b / "live2d" / painting]
    elif stype == "spine":
        paths += [b / "spine" / painting]
    elif stype == "static":
        paths += [b / "static" / painting, b / "static" / f"{painting}_tex"]
        paths += sorted(b.glob(f"static/{painting}_*_tex"))
    return [p for p in paths if p.exists()]


def cleanup_processed_bundles() -> dict:
    """关闭软件时自动清理「已提取完成」装束的原始下载包（bundle）。

    下载包只在 下载→提取 阶段使用；提取产物（resources/extracted）才是
    预览/导出/应用的数据源，删除下载包不影响任何已提取装束。
    遍历 local_skins.json（提取成功的事实来源，build_local_index 只收录
    产物存在的装束）逐个删除其 bundle；未提取/提取失败的 bundle 不在索引里，
    自然保留，方便下次重试。
    受配置 clean_bundles_on_exit 控制，可在设置页关闭。
    """
    if not config.get("clean_bundles_on_exit", True):
        return {"ok": True, "skipped": True, "removed": [], "kept": 0}
    removed: list[str] = []
    for loc in metadata.local_skins():
        stype = loc.get("type", "")
        painting = (loc.get("painting") or "").lower()
        if not painting or stype not in ("live2d", "static"):
            continue
        for p in _bundle_paths(stype, painting):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed.append(str(p))
            except Exception as e:  # noqa: BLE001
                print(f"[cleanup] 删除失败 {p}: {e}")
    if removed:
        print(f"[cleanup] 退出清理：删除 {len(removed)} 个已提取装束的下载包（保留提取产物）", flush=True)
    return {"ok": True, "removed": removed, "kept": 0}


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _thread_scoped_stream(real, tid: int, buf):
    """把 sys.stdout/stderr 换成“按线程分流”的对象：工具线程的 print 进 StringIO，
    其他线程（并发 API 请求）的输出仍落真实流。

    contextlib.redirect_stdout 是进程级替换，长工具（元数据同步）运行期间
    其他请求线程的 [api] 日志会被一并吸进工具缓冲区，混进响应里的 steps.output。
    """
    class _Scoped:
        def write(self, s):
            if threading.get_ident() == tid:
                buf.write(s)
            else:
                try:
                    real.write(s)
                except Exception:  # noqa: BLE001
                    pass
            return len(s)

        def flush(self):
            if threading.get_ident() != tid:
                try:
                    real.flush()
                except Exception:  # noqa: BLE001
                    pass

        def isatty(self):
            return False

        def reconfigure(self, **kwargs):
            # 工具脚本常在入口调用 sys.stdout.reconfigure(...)；分流对象透传为 no-op，
            # 避免工具在进程内执行时因流缺少该方法而崩溃
            pass

        @property
        def encoding(self):
            return getattr(real, "encoding", None)

    return _Scoped()


def run_tool(name: str, args: list[str] | None = None, timeout: int = 600) -> tuple[int, bytes, bytes]:
    """进程内运行 tools/ 下的脚本模块（打包后无法再用 sys.executable 起子进程）。

    tools 模块没有 __init__.py，按文件路径动态加载，避免引入包结构改动。
    注意：进程内执行意味着 timeout 只是约定上限，无法真正中断卡死的工具；
    工具自身的网络超时/重试才是兜底。
    """
    with _TOOL_LOCK:
        import importlib.util
        import io

        path = ROOT / "tools" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"_pack_tool_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old_argv = sys.argv
        sys.argv = [name] + (args or [])
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        tid = threading.get_ident()
        try:
            sys.stdout = _thread_scoped_stream(real_out, tid, buf_out)
            sys.stderr = _thread_scoped_stream(real_err, tid, buf_err)
            try:
                rc = mod.main() or 0
            finally:
                sys.stdout = real_out
                sys.stderr = real_err
        finally:
            sys.argv = old_argv
        return (
            rc,
            buf_out.getvalue().encode("utf-8", "ignore"),
            buf_err.getvalue().encode("utf-8", "ignore"),
        )


def finish_metadata_update() -> None:
    """同步完成后重建本地索引并刷新内存元数据/资源库。"""
    run_tool("build_local_index", timeout=120)
    metadata.reload()
    refresh_library()


def run_metadata_update() -> dict:
    """元数据更新：重新下载社区 Lua 配置/资源清单并全量探测 CDN，再重建本地索引。

    必须带 `--refresh`：游戏更新后 cfg 配置表与清单缓存都是旧的，不加参数
    `_download` 会直接跳过已存在的缓存文件，新角色/新装束永远进不了图鉴。
    全量探测约 5000 个 CDN 候选 key（通常 5~10 分钟），SSE 会持续推送阶段文字。
    """
    report: dict = {"ok": True, "steps": {}}
    _emit_stage("正在下载最新配置与 CDN 清单…")
    tool_path = ROOT / "tools" / f"{METADATA_TOOL}.py"
    if not tool_path.exists():
        report["ok"] = False
        report["steps"]["build"] = {
            "ok": False,
            "output": "",
            "error": f"tools/{METADATA_TOOL}.py 不存在：元数据同步不可用",
        }
        _emit_stage("元数据构建脚本缺失")
        return report
    _run_update_step(report, "build", METADATA_TOOL, ["--refresh"], "正在同步配置并探测 CDN…")
    _emit_stage("正在重建本地索引…")
    try:
        finish_metadata_update()
    except Exception as e:  # noqa: BLE001
        report["steps"]["rebuild"] = {
            "ok": False,
            "output": "",
            "error": f"{type(e).__name__}: {e}"[:800],
        }
        report["ok"] = False
    _emit_stage("元数据更新完成" if report["ok"] else "元数据更新失败")
    return report


def run_wiki_sync() -> dict:
    """图鉴同步 = 元数据全量更新（游戏更新后用这个入口拿新角色/新装束）。"""
    return run_metadata_update()


def _run_update_step(report: dict, key: str, tool: str, args: list[str], stage: str) -> None:
    """跑一个元数据更新步骤：先推 SSE 阶段事件（前端按钮下方实时显示），
    异常也收敛进步骤记录，不让单步崩溃把整个请求打成 500/无响应。"""
    _emit_stage(stage)
    try:
        rc, out, err = run_tool(tool, args)
    except Exception as e:  # noqa: BLE001
        rc, out, err = -1, b"", f"{type(e).__name__}: {e}".encode("utf-8", "replace")
    report["steps"][key] = {
        "ok": rc == 0,
        "output": out.decode("utf-8", "ignore")[-3000:],
        "error": err.decode("utf-8", "ignore")[-800:],
    }
    if not report["steps"][key]["ok"]:
        report["ok"] = False


def local_skin_asset(character: str, bundle: str, name: str | None = None) -> dict | None:
    hits = [
        loc for loc in metadata.local_skins()
        if loc.get("character") == character and loc.get("bundle") == bundle
    ]
    if not hits:
        return None
    if name is None:
        return hits[0].get("asset")
    for loc in hits:
        if loc.get("name") == name:
            return loc.get("asset")
    return hits[0].get("asset") if len(hits) == 1 else None


ALIGN_MAP = {
    "center": 0,
    "left-top": 1,
    "right-top": 2,
    "left-bottom": 3,
    "right-bottom": 4,
}


def _export_project(character: str, bundle: str, name: str | None, options: dict) -> str:
    """生成 WE 壁纸项目，返回项目目录。"""
    skin = resolve_skin(character, bundle, name)
    if not skin:
        raise ValueError(f"装束不存在: {character}/{bundle}")
    asset = local_skin_asset(character, bundle, name)
    if not asset:
        raise ValueError("该装束尚未下载，请先下载后再导出")
    stype = skin.get("type", "")
    if stype not in ("live2d", "static", "spine"):
        raise ValueError(f"装束类型 {stype} 不支持导出壁纸")
    exporter = registry.get(
        "exporters",
        {
            "live2d": "wallpaper_live2d",
            "static": "wallpaper_static",
            "spine": "wallpaper_spine",
        }[stype],
    )
    if exporter is None:
        raise ValueError("导出插件未注册")
    opts = options or {}
    try:
        proj = exporter.export(
            {**skin, "asset": asset},
            {
                "root": str(ROOT),
                "bg": opts.get("bg", "monet"),
                "bgColor": opts.get("bgColor"),
                "scale": int(opts.get("scale", 100) or 100),
                "offsetX": int(opts.get("offsetX", 0) or 0),
                "offsetY": int(opts.get("offsetY", 0) or 0),
                "alignment": ALIGN_MAP.get(str(opts.get("alignment", "center")), 0),
                "animation": opts.get("animation", ""),
                "animations": opts.get("animations") or [],
                # Live2D 面板开关（前端导出选项透传，预览页勾选的状态同步到 WE 侧默认值）
                "voice": bool(opts.get("voice", True)),
                "intro": bool(opts.get("intro", True)),
                "interact": bool(opts.get("interact", True)),
                "track": bool(opts.get("track", True)),
                # Spine 壁纸的语音/互动面板（导出器只读取存在的键）
                "subtitle": bool(opts.get("subtitle", True)),
                "volume": int(opts.get("volume", 80) or 80),
                "idleInterval": int(opts.get("idleInterval", 60) or 60),
                "speed": int(opts.get("speed", 100) or 100),
                "intro": bool(opts.get("intro", True)),
            },
            str(ROOT / "resources" / "wallpapers"),
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"导出失败: {e}") from e
    # 封面：前端截取的完整立绘帧优先写成 preview.jpg（背景由后端按导出样式重绘），
    # 保证 WE 封面就是完整立绘。失败时保留导出器生成的兜底封面，绝不留缺失引用。
    preview_data = opts.get("previewImage")
    if preview_data:
        try:
            from core.wallpaper import preview_palette, write_preview_from_data_url

            colors = [
                color for color in (opts.get("previewColors") or [])
                if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)
            ]
            palette = colors or preview_palette({**skin, "asset": asset}, ROOT)
            write_preview_from_data_url(
                Path(proj),
                str(preview_data),
                palette=palette,
                bg=opts.get("bg", "monet"),
                bg_color=opts.get("bgColor"),
            )
        except Exception as ex:  # noqa: BLE001
            print(f"[preview] 写入封面失败: {ex}", flush=True)
    return proj


def export_skin(character: str, bundle: str, name: str | None, options: dict) -> dict:
    """生成 WE 壁纸项目并打开所在文件夹（手动拖入 Wallpaper Engine）。"""
    try:
        proj = _export_project(character, bundle, name, options)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        os.startfile(str(Path(proj)))  # noqa: S606
        opened = True
    except Exception as e:  # noqa: BLE001
        opened = False
        return {
            "ok": True,
            "project": proj,
            "opened": False,
            "error": f"导出成功但打开文件夹失败: {e}",
        }
    return {
        "ok": True,
        "project": proj,
        "opened": opened,
        "message": "导出完成，已打开项目文件夹，请把 index.html 拖入 Wallpaper Engine",
    }


def export_static_image(character: str, bundle: str, name: str | None = None) -> dict:
    """把静态立绘原图导出为普通图片到 resources/exports/ 并打开文件夹。"""
    skin = resolve_skin(character, bundle, name)
    if not skin:
        return {"ok": False, "error": f"装束不存在: {character}/{bundle}"}
    if skin.get("type") != "static":
        return {"ok": False, "error": "仅静态立绘支持导出图片"}
    asset = local_skin_asset(character, bundle, name)
    if not asset:
        return {"ok": False, "error": "该装束尚未下载，请先下载"}
    src = ROOT / "resources" / asset["dir"] / asset.get("image", "painting.png")
    if not src.exists():
        return {"ok": False, "error": f"未找到立绘文件: {src}"}
    out_dir = ROOT / "resources" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "".join(ch for ch in f"{character}_{skin.get('name', '')}" if ch not in '<>:"/\\|?*').rstrip(" .")
    dest = out_dir / f"{name or 'painting'}.png"
    shutil.copy2(src, dest)
    try:
        os.startfile(str(out_dir))  # noqa: S606
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    return {
        "ok": True,
        "path": str(dest),
        "opened": opened,
        "message": f"图片已导出：{dest.name}",
    }


def export_image_data(
    character: str, bundle: str, name: str | None, data_url: str, index: int | None = None
) -> dict:
    """把前端截取的当前预览帧（PNG dataURL）保存为图片并打开文件夹。"""
    import base64

    if not data_url or "," not in data_url:
        return {"ok": False, "error": "缺少图像数据"}
    try:
        png = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "图像数据解析失败"}
    if not png.startswith(b"\x89PNG"):
        return {"ok": False, "error": "仅支持 PNG 截图"}
    out_dir = ROOT / "resources" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    if index:
        safe_character = "".join(ch for ch in character if ch not in '<>:"/\\|?*').rstrip(" .")
        clean = f"{safe_character or 'character'}-{int(index)}"
    else:
        clean = "".join(ch for ch in f"{character}_{name or bundle}" if ch not in '<>:"/\\|?*').rstrip(" .")
    dest = out_dir / f"{clean or 'capture'}.png"
    dest.write_bytes(png)
    try:
        os.startfile(str(out_dir))  # noqa: S606
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    return {"ok": True, "path": str(dest), "opened": opened, "message": f"图片已导出：{dest.name}"}


def _cleanup_stale_exports(keep: str) -> int:
    """清理 resources/wallpapers 下与本次导出同角色同装束的旧项目目录。

    每次「导出并应用」成功后，删除同名壁纸（目录名同前缀 {character}_{name}_）的
    旧导出——resources/wallpapers 是待应用的中转区，同壁纸只留最新一份即可；
    不同名壁纸（其他角色/装束）全部保留。keep 为本次项目路径（绝对或相对均可）。

    目录名格式：{character}_{name}_{uuid8}，前缀 = 去掉末尾 _{uuid8} 的部分。
    """
    wp = ROOT / "resources" / "wallpapers"
    if not wp.is_dir():
        return 0
    keep_path = Path(keep).resolve()
    keep_name = keep_path.name
    prefix = re.sub(r"_[0-9a-f]{8}$", "", keep_name)
    removed = 0
    for child in wp.iterdir():
        if not child.is_dir():
            continue
        if child.resolve() == keep_path:
            continue
        # 同角色同装束：目录名去掉 _uuid8 后与本次前缀一致；不同名则保留
        if re.sub(r"_[0-9a-f]{8}$", "", child.name) != prefix:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    if removed:
        print(f"[export] 已清理 {removed} 个同装束旧导出项目目录", flush=True)
    return removed


def apply_skin(character: str, bundle: str, name: str | None, options: dict) -> dict:
    """导出并直接应用到 Wallpaper Engine（新副本 + openWallpaper，绕开编辑器导入路径）。"""
    try:
        proj = _export_project(character, bundle, name, options)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    ok = apply_wallpaper(Path(proj))
    if not ok:
        return {
            "ok": False,
            "project": proj,
            "error": "应用失败：未找到 Wallpaper Engine 安装目录",
        }
    # 应用成功才清理旧导出（失败时保留旧导出，用户可能手动拖入本次项目）
    cleaned = _cleanup_stale_exports(proj)
    return {
        "ok": True,
        "project": proj,
        "cleaned": cleaned,
        "message": "已生成并应用壁纸，可在 Wallpaper Engine 中查看",
    }


def download_skin(skin: dict, cancel_event: threading.Event | None = None) -> dict:
    """通用下载编排（M0 骨架）：解析请求后交给 plugins/sources 注册的来源插件。

    固定骨架：解析来源插件 → 下载原始包 → 按类型分派提取插件 → 重建本地索引
    → 刷新资源库。清单解析/hash 校验/分片下载等细节全部属于来源插件的职责，
    本函数不内置任何具体游戏的资源路径或类型分发规则。

    当前尚未注册任何来源插件（后续里程碑提供），统一返回 501 语义的占位错误。
    """
    source = next(iter(registry.all("sources")), None)
    if source is None:
        return {
            "ok": False,
            "status": 501,
            "error": "资源源未实现：plugins/sources 尚未注册任何来源插件（等待后续里程碑）",
        }

    def cancelled() -> dict:
        return {
            "ok": False,
            "cancelled": True,
            "skin": skin,
            "downloaded": downloaded,
            "error": "下载已取消",
        }

    stype = skin.get("type", "")
    if stype not in ("live2d", "static", "spine"):
        return {"ok": False, "error": f"不支持的资源类型：{stype or '未知'}"}
    extractor = registry.get("extractors", stype)
    if extractor is None:
        return {"ok": False, "error": f"未注册 {stype} 提取插件"}
    painting = (skin.get("painting") or skin.get("name") or "").lower()
    if not painting:
        return {"ok": False, "error": "装束缺少资源标识（painting）"}

    bundles_dir = ROOT / "resources" / "bundles"
    extracted_dir = ROOT / "resources" / "extracted"
    downloaded: list[dict] = []

    # 1) 下载：来源插件负责清单解析与原始包下载（含取消检查、阶段推送）
    _emit_stage("正在下载资源", detail=painting)
    try:
        try:
            ok = source.download(
                skin, str(bundles_dir),
                cancel_event=cancel_event,
                emit=lambda s, d="": _emit_stage(s, detail=d),
            )
        except TypeError:
            # 来源插件只实现基础签名 download(bundle, out_dir)
            ok = source.download(skin, str(bundles_dir))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skin": skin, "error": f"来源插件下载失败：{e}"}
    downloaded.append({"path": painting, "ok": bool(ok)})

    if cancel_event is not None and cancel_event.is_set():
        return cancelled()

    # 2) 提取：按装束类型分派通用提取插件（generic 目录约定 bundles/{type}/{painting}）
    if ok:
        _emit_stage("正在提取资源", detail=painting)
        try:
            if stype in ("live2d", "spine"):
                extractor.extract(
                    str(bundles_dir / stype / painting),
                    str(extracted_dir / stype / painting),
                )
            else:
                extractor.extract(
                    str(bundles_dir / "static" / painting),
                    str(bundles_dir / "static" / f"{painting}_tex"),
                    str(extracted_dir / "static" / painting),
                )
        except Exception as e:  # noqa: BLE001
            # 提取失败不整单判失败：bundle 已下载，用户可重试提取
            print(f"[download] {painting} 提取失败: {e}")

        # 2.5) 点击热区配置：预览页与导出器共用（原始 UI 坐标，运行时按 prefab pos/scale 换算）
        if stype == "spine" and skin.get("actions"):
            try:
                (extracted_dir / stype / painting / "actions.json").write_text(
                    json.dumps(skin["actions"], ensure_ascii=False), encoding="utf-8"
                )
            except Exception as e:  # noqa: BLE001
                print(f"[download] {painting} 写入 actions.json 失败: {e}")

        # 2.6) 类型切换清理：装束补数据后从 static 变 spine（或反向）时，同 painting
        #      的另一类型旧产物会让本地索引出现双条目（mock/api 按 painting 取第一条），
        #      下载成功后删掉旧类型目录，保证索引用新类型
        other = "static" if stype == "spine" else "spine"
        stale_dir = extracted_dir / other / painting
        if stale_dir.exists():
            shutil.rmtree(stale_dir, ignore_errors=True)

        # 3) 语音：仅动态立绘需要（点击互动/待机台词）；静态图没有互动，不下载语音
        if stype == "spine" and skin.get("voice_id"):
            try:
                from core.voice import ensure_voice

                _emit_stage("正在准备语音", detail=painting)
                ensure_voice(
                    skin,
                    emit=lambda s, d="": _emit_stage(s, detail=d),
                    cancel_event=cancel_event,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[download] {painting} 语音准备失败: {e}")

    if cancel_event is not None and cancel_event.is_set():
        return cancelled()

    # 3) 重建本地索引并刷新资源库
    _emit_stage("正在同步本地索引")
    with _INDEX_LOCK:
        run_tool("build_local_index", timeout=120)
        metadata.reload()
        refresh_library()
    result: dict = {"ok": bool(ok), "skin": skin, "downloaded": downloaded}
    if not ok:
        result["error"] = "资源下载失败"
    return result


def open_wallpapers_dir() -> dict:
    """打开软件自己的壁纸输出目录（resources/wallpapers，存放导出的待用壁纸项目）。"""
    d = ROOT / "resources" / "wallpapers"
    d.mkdir(parents=True, exist_ok=True)
    os.startfile(str(d))  # noqa: S606
    return {"ok": True, "path": str(d)}


def clean_exports() -> dict:
    """清理导出的壁纸项目与截图（resources/wallpapers + resources/exports）。"""
    removed = []
    for sub in ("wallpapers", "exports"):
        d = ROOT / "resources" / sub
        if d.is_dir():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed.append(str(child))
    return {"ok": True, "removed": len(removed)}


class Handler(BaseHTTPRequestHandler):
    def _query(self, key: str, default: str = "") -> str:
        """解析查询串 ?key=value。"""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        for part in qs.split("&"):
            k, _, v = part.partition("=")
            if k == key:
                return v
        return default

    def _send(self, status: int, payload: dict) -> None:
        # 诊断日志：记录前端每次 API 调用的请求与响应（真实窗口排障用）
        try:
            print(
                f"[api] {self.command} {self.path} -> {status} "
                f"{json.dumps(payload, ensure_ascii=False)[:300]}",
                flush=True,
            )
        except Exception:  # noqa: BLE001
            pass
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self) -> None:
        """SSE 长连接：把下载/提取/同步等阶段事件推送给前端。"""
        q: queue.Queue = queue.Queue(maxsize=200)
        with _EVENT_LOCK:
            _EVENT_QUEUES.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"event: connected\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=30)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # 心跳保活
                    self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _EVENT_LOCK:
                if q in _EVENT_QUEUES:
                    _EVENT_QUEUES.remove(q)

    def _is_local_request(self) -> bool:
        """只允许本机页面调用后端：Host 必须为本机地址，且 Origin（浏览器跨站请求必带）
        若存在也必须是本机。这样即使网页被浏览器打开的其他恶意网站请求，也会被拒绝，
        避免恶意网页借本机 API 删除下载/触发下载占满磁盘。"""
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        origin = self.headers.get("Origin") or ""
        if not origin:
            return True  # 同源请求 / 非浏览器客户端（curl、pywebview 等）
        from urllib.parse import urlparse

        return (urlparse(origin).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")

    def _guard_local(self) -> bool:
        """非本机请求时直接返回 403，返回 False 表示调用方应停止处理。"""
        if self._is_local_request():
            return True
        self._send(403, {"ok": False, "error": "forbidden: 仅允许本机调用"})
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        self._send(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        try:
            ensure_fresh_metadata()
            if self.path.startswith("/api/events"):
                self._stream_events()
                return
            if self.path.startswith("/api/health"):            self._send(200, {"ok": True, "plugins": registry.summary()})
            elif self.path.startswith("/api/library/downloaded"):
                self._send(200, {"ok": True, "skins": downloaded_with_painting()})
            elif self.path.startswith("/api/config"):
                self._send(200, get_config())
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception:  # noqa: BLE001
            logger.exception("GET %s failed", self.path)
            try:
                self._send(500, {"ok": False, "error": "internal error"})
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard_local():
            return
        try:
            ensure_fresh_metadata()
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path.startswith("/api/"):
                print(
                    f"[api] POST {self.path} body={json.dumps(data, ensure_ascii=False)[:200]}",
                    flush=True,
                )
            if self.path.startswith("/api/download/cancel"):
                cancel_download(data.get("download_id") or "")
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/download"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                download_id = data.get("download_id") or ""
                skin = resolve_skin(character, bundle, name)
                if not skin:
                    self._send(404, {"ok": False, "error": f"装束不存在: {character}/{bundle}"})
                    return
                cancel_event = _register_cancel(download_id) if download_id else None
                try:
                    result = download_skin(skin, cancel_event)
                finally:
                    if download_id:
                        _unregister_cancel(download_id)
                # 未实现来源插件时返回 501，前端可据此提示“功能开发中”
                self._send(501 if result.get("status") == 501 else 200, result)
            elif self.path.startswith("/api/library/delete"):
                self._send(
                    200,
                    delete_skin_files(
                        data.get("character"), data.get("bundle"), data.get("name")
                    ),
                )
            elif self.path.startswith("/api/library/delete-batch"):
                # 批量删除：items=[{character,bundle,name},...]，只重建一次索引（快）
                items = data.get("items") or []
                if not isinstance(items, list) or not items:
                    self._send(400, {"ok": False, "error": "items 为空"})
                else:
                    self._send(200, delete_skins_batch(items))
            elif self.path.startswith("/api/library/cleanup-bundles"):
                self._send(200, cleanup_processed_bundles())
            elif self.path.startswith("/api/library/clear"):
                self._send(200, clear_all_downloads())
            elif self.path.startswith("/api/metadata/update"):
                self._send(200, run_metadata_update())
            elif self.path.startswith("/api/metadata/sync-wiki"):
                self._send(200, run_wiki_sync())
            elif self.path.startswith("/api/config"):
                self._send(200, set_config(data.get("config") or {}))
            elif self.path.startswith("/api/log"):
                # 前端运行时错误上报（排障用）：打印到 stdout，不落盘
                print(f"[fe-error] {json.dumps(data, ensure_ascii=False)[:800]}", flush=True)
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/export-image-data"):
                self._send(
                    200,
                    export_image_data(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("dataUrl") or "",
                        index=data.get("index"),
                    ),
                )
            elif self.path.startswith("/api/export-image"):
                self._send(
                    200,
                    export_static_image(data.get("character"), data.get("bundle"), data.get("name")),
                )
            elif self.path.startswith("/api/export"):
                self._send(
                    200,
                    export_skin(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("options") or {},
                    ),
                )
            elif self.path.startswith("/api/apply"):
                self._send(
                    200,
                    apply_skin(
                        data.get("character"),
                        data.get("bundle"),
                        data.get("name"),
                        data.get("options") or {},
                    ),
                )
            elif self.path.startswith("/api/palette"):
                character = data.get("character")
                bundle = data.get("bundle")
                name = data.get("name")
                mode = data.get("mode") or "auto"
                bg_color = data.get("bgColor")
                skin = resolve_skin(character, bundle, name)
                asset = local_skin_asset(character, bundle, name)
                # 纯色背景只依赖用户选色，不要求素材已下载/可解码：
                # 取色失败时 mode_css 仍会返回显式颜色，其他模式回退固定样式，
                # 避免前端拿到空 CSS 导致「选了颜色但背景不变」。
                src = find_palette_source({**skin, "asset": asset}, ROOT) if skin and asset else None
                palette = extract_palette(src) if src else None
                css = mode_css(mode, palette, solid_color=bg_color) or bg_css(mode)
                self._send(
                    200,
                    {
                        "ok": bool(palette) or (mode == "solid" and bool(bg_color)),
                        "colors": palette or fallback_palette(),
                        "source": str(src) if src else None,
                        "css": css,
                    },
                )
            elif self.path.startswith("/api/open/download-dir"):
                os.startfile(str(ROOT / "resources" / "bundles"))  # noqa: S606
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/open/extracted-dir"):
                os.startfile(str(ROOT / "resources" / "extracted"))  # noqa: S606
                self._send(200, {"ok": True})
            elif self.path.startswith("/api/open/wallpapers-dir"):
                self._send(200, open_wallpapers_dir())
            elif self.path.startswith("/api/library/clean-exports"):
                self._send(200, clean_exports())
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa: BLE001
            logger.exception("POST %s failed", self.path)
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args) -> None:  # noqa: D401
        pass


def main(port: int = 8766) -> None:
    """启动后端 API 服务。

    注意：打包版/桌面版会以线程方式调用 main(8770) 传入端口，
    因此这里**不再**从 sys.argv 读端口（避免被启动参数意外覆盖，
    导致前端连不上后端）。
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"backend API listening on http://127.0.0.1:{port}")
    logger.info("backend API listening on http://127.0.0.1:%s", port)
    server.serve_forever()


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    main(_port)
