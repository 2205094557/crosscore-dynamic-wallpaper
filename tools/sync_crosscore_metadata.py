"""同步《交错战线》(CrossCore) 图鉴元数据。

数据来源：
  - 游戏 Lua 配置表（社区整理的 Cfg* 表，GitHub: AXiX-official/CrossCoreLuascripts）
  - 官方 CDN 资源清单 ilist.txt（镜像：R2iCecho/Crosscore_Assets_curr_1）

产出 resources/metadata/：
  characters.json  角色表（name/role_id/faction/career/rarity/voice）
  skins.json       装束表（character/bundle/name/painting/type/draw_key/spine_key/voice_id）
  voices.json      语音表（voice_id → 台词行：cue/分类/文本）
  asset_list.txt   CDN 清单缓存（供来源插件判断资源是否存在）

用法：
  python tools/sync_crosscore_metadata.py            # 缺文件时才下载
  python tools/sync_crosscore_metadata.py --refresh  # 强制重新下载配置与清单
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lua_parse import load as load_lua  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
CFG_DIR = MD / "cfg"

JSDELIVR_CFG = "https://cdn.jsdelivr.net/gh/AXiX-official/CrossCoreLuascripts@main/android/cfg/{name}"
ILIST_URL = "https://cdn.jsdelivr.net/gh/R2iCecho/Crosscore_Assets_curr_1@main/lists/ilist.txt"
# 社区维护的「国际服/反和谐」资源清单：里面的 key 是未和谐的变体（如 alps03 对应和谐版 alps031）
INTERNATION_URL = "https://cdn.jsdelivr.net/gh/AXiX-official/CrossCore-Internationalizer@main/ilist.txt"

CFG_FILES = [
    "cfgcharacter.lua",
    "cfgCfgCardRole.lua",
    "cfgCardData.lua",
    "cfgCfgTeamEnum.lua",
    "cfgCfgRoleEnum.lua",
    "cfgCfgCardQuality.lua",
    "cfgCfgRoleQualityEnum.lua",
    "cfgSoundLanguage.lua",
    "cfgSound.lua",
    "cfgSound1.lua",
    "cfgSound2.lua",
    "cfgSound3.lua",
    "cfgCfgSpineAction.lua",
]

SKIN_TYPE_LABEL = {
    None: "基础",
    1: "商店皮肤",
    2: "跃升皮肤",
    3: "同调皮肤",
    4: "形态切换",
}

# 壁纸互动/待机需要的语音分类（Sound.name）
VOICE_CATEGORIES = {
    "看板", "登录", "获得", "CrossCore", "生日祝福",
    "接触1", "接触2", "接触3", "接触4", "接触5", "接触6", "接触7",
    "友好", "喜欢", "挚爱", "心动接触", "愉悦", "不悦",
    "归来", "远征归来", "进入设施", "设施工作",
    "升级", "跃升", "最终跃升", "语气词（开心）", "语气词（讨厌）",
}


def _category(text) -> str:
    """皮肤语音的分类名带装束后缀（如「接触1（夜幕幽冥）」），归一化后再做筛选。"""
    return re.sub(r"[（(][^）)]*[）)]\s*$", "", str(text or "")).strip()


def _sound_rows(data) -> list[dict]:
    """兼容两种导出格式：_G["Sound"] 键值表 与 `local conf = {names,data}` 列式表。"""
    rows: list[dict] = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        names = data.get("names") or []
        for item in data["data"]:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, list) and names:
                rows.append(dict(zip(names, item)))
    elif isinstance(data, dict):
        rows = [v for v in data.values() if isinstance(v, dict)]
    return rows


def _download(url: str, dest: Path, refresh: bool = False) -> None:
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 crosscore-wallpaper"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)
    print(f"[sync] 下载 {dest.name} ({dest.stat().st_size} B)", flush=True)


def _read_ilist(refresh: bool) -> list[str]:
    path = MD / "asset_list.txt"
    _download(ILIST_URL, path, refresh)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _read_internation(refresh: bool) -> set[str]:
    path = MD / "internation_list.txt"
    _download(INTERNATION_URL, path, refresh)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    items = [x.strip() for x in re.split(r"[,\n]", text) if x.strip()]
    return {"Custom/" + x for x in items}


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def _find_key(index: dict[str, str], key: str) -> str | None:
    return index.get(_normalize_key(key))


def _variant_of(key: str, role_id: int) -> tuple[str, int | None] | None:
    """解析 `prefabs_spine_{role}_{variant}_spine` 的变体类型与序号。"""
    prefix = f"Custom/prefabs_spine_{role_id}_"
    if not key.startswith(prefix):
        return None
    tail = key[len(prefix):]
    if tail.endswith("_spine"):
        tail = tail[: -len("_spine")]
    m = re.match(r"skin(\d+)_", tail)          # skin03_machairodus
    if m:
        return "skin", int(m.group(1))
    if tail.startswith("skin_"):                # skin_wolframite03
        m = re.search(r"(\d+)$", tail)
        return "skin", int(m.group(1)) if m else None
    if tail.startswith("break_"):
        return "break", None
    if tail.startswith("synchro_"):
        return "synchro", None
    return None


def assign_spine_keys(records: list[dict], role_id: int, english: str,
                      custom_index: dict[str, str]) -> dict[int, str]:
    """为角色的每个装束分配 CDN 上真实存在的 Spine 包（一个包只归一个装束）。

    匹配优先级：
      1. `cfgcharacter.l2dName`——官方写死的 spine 包名，探测存在就 1:1 直配
         （能覆盖官方拼写错误/空格等启发式永远凑不出的怪名）；
      2. 命名规则启发式（以真实资源核对过）：
        - 基础装束：`prefabs_spine_{soundLanguageName}`，不带 role_id；
        - 跃升：`prefabs_spine_{role}_break_{name}_spine` → skinType=2 的装束；
        - 同调：`prefabs_spine_{role}_synchro_{name}_spine` → skinType=3 的装束；
        - 商店：`prefabs_spine_{role}_skin{NN}_...` / `..._skin_{name}{NN}_spine`，
          NN 是装束在角色记录里的序号（按 id 升序，1 起）。
    匹配不到就不给 spine（壁纸退回静态图），绝不把一个包挂到多个装束上。
    """
    assigned: dict[int, str] = {}
    used_bundles: set[str] = set()

    # 1) l2dName 直配：装束记录自带包名，按 id 升序占坑
    for rec in sorted(records, key=lambda r: int(r.get("id") or 0)):
        if int(rec["id"]) in assigned:
            continue
        for cand in _l2d_spine_candidates(rec, role_id):
            # custom_index 的键是规范化（小写）过的，必须走 _find_key 查找
            key = _find_key(custom_index, cand)
            if key and key not in used_bundles:
                assigned[int(rec["id"])] = key
                used_bundles.add(key)
                break

    # 2) 启发式（只处理还没被 l2dName 认领的装束）
    prefix = f"Custom/prefabs_spine_{role_id}_"
    keys = [k for k in custom_index.values() if k.startswith(prefix)]
    by_ordinal = {index: rec for index, rec in enumerate(records, start=1)}

    for key in keys:
        variant = _variant_of(key, role_id)
        if variant is None:
            continue
        kind, number = variant
        target = None
        if kind in ("break", "synchro"):
            want = 2 if kind == "break" else 3
            hits = [rec for rec in records if rec.get("skinType") == want]
            target = hits[0] if len(hits) == 1 else None
        elif kind == "skin":
            if number and by_ordinal.get(number, {}).get("skinType") == 1:
                target = by_ordinal[number]
            else:
                hits = [rec for rec in records if rec.get("skinType") == 1]
                if len(hits) == 1:
                    target = hits[0]
        if target is None or int(target["id"]) in assigned:
            continue
        assigned[int(target["id"])] = key
        used_bundles.add(key)

    base = next((rec for rec in records if rec.get("skinType") is None), None)
    if base is not None and int(base["id"]) not in assigned:
        sheet = str(base.get("soundLanguageName") or english or "").lower()
        key = _find_key(custom_index, f"Custom/prefabs_spine_{sheet}") if sheet else None
        if key:
            assigned[int(base["id"])] = key
    return assigned


PROBE_CACHE = MD / "probe_cache.json"
CDN_PROBE_BASE = "https://cdn.megagamelog.com/cross/release/android/curr/"
PROBE_WORKERS = 16


def _load_probe_cache() -> dict[str, bool]:
    try:
        return {str(k): bool(v) for k, v in json.loads(PROBE_CACHE.read_text(encoding="utf-8")).items()}
    except Exception:  # noqa: BLE001
        return {}


def _save_probe_cache(cache: dict[str, bool]) -> None:
    PROBE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _probe_one(key: str) -> bool:
    """HEAD 探测 CDN 上是否真的存在该 key（清单镜像可能滞后/缺项）。
    CDN 偶发抽风：同一 key（尤其带空格编码的怪名）会间歇性 404，404 也重试一次。"""
    import urllib.error
    import urllib.parse
    import urllib.request

    # l2dName 这类官方怪名可能带空格（如 `..._zues04 _spine`），必须 URL 编码
    url = CDN_PROBE_BASE + urllib.parse.quote(key)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as ex:
            if ex.code in (403, 404):
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                return False
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def _probe_keys(keys: set[str], refresh: bool = False) -> set[str]:
    from concurrent.futures import ThreadPoolExecutor

    cache = _load_probe_cache()
    pending = sorted(k for k in keys if refresh or k not in cache)
    if pending:
        print(f"[sync] 探测 CDN 资源 {len(pending)} 个（并发 {PROBE_WORKERS}）…", flush=True)
        done = 0
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
            for key, ok in zip(pending, pool.map(_probe_one, pending)):
                cache[key] = bool(ok)
                done += 1
                if done % 500 == 0:
                    print(f"[sync]   探测 {done}/{len(pending)}", flush=True)
        _save_probe_cache(cache)
    return {k for k in keys if cache.get(k)}


def _draw_variants(key: str) -> list[str]:
    bare = key[len("Custom/"):] if key.startswith("Custom/") else key
    m = re.match(r"^(textures_bigs_character_)(.+?)(_draw)(-half)?$", bare)
    if not m:
        return [key]
    pre, stem, draw, half = m.groups()
    names = [f"Custom/{pre}{stem}{draw}{half or ''}"]
    if stem[-1:].isdigit():
        names.append(f"Custom/{pre}{stem[:-1]}{draw}{half or ''}")
    names.append(f"Custom/{pre}{stem}{draw}1{half or ''}")
    if half:
        names.append(f"Custom/{pre}{stem}{draw}")
    names += ["Custom/" + n[len("Custom/"):].lower() for n in list(names)]
    return names


def _pick_internation_draw(draw_key: str, internation: set[str]) -> str | None:
    """在反和谐清单里找该立绘对应的未和谐变体。"""
    for key in _draw_variants(draw_key):
        if key in internation:
            return key
    return None


def _ascii_fold(text: str) -> str:
    """重音折叠：Marée Rouge → mareerouge（é 必须变成 e，而不是被删掉）。"""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _name_tokens(rec: dict, english: str) -> list[str]:
    names: list[str] = []

    def add(value):
        text = re.sub(r"[^a-z0-9]", "", _ascii_fold(value).lower())
        if text and text not in names:
            names.append(text)

    add(english)
    add(rec.get("soundLanguageName"))
    img = str(rec.get("img") or "")
    if img:
        stem = re.sub(r"^[0-9]+_", "", img)
        stem = re.sub(r"_draw(-half)?$", "", stem, flags=re.I)
        add(stem)
        add(re.sub(r"\d+$", "", stem))
    return names


def _l2d_spine_key(rec: dict) -> str | None:
    """cfgcharacter.l2dName 是官方写死的 spine 包名（可能有拼写错误/多余空格，
    如宙斯「棋外绮念」的 `70010_skin_Zues04 _spine`——Zues 拼错且带空格）。
    保守规范化（去首尾空白 + 折叠重音 + 小写，保留内部空格）后直接当 CDN key；
    不含 _spine 的视为非 spine 模型。"""
    name = str(rec.get("l2dName") or "").strip()
    if not name or "_spine" not in name.lower():
        return None
    if name.lower().startswith("custom/"):
        name = name[7:]
    return "Custom/" + _ascii_fold(name).lower()


def _l2d_spine_candidates(rec: dict, role_id: int) -> list[str]:
    """l2dName 衍生的候选 key：字面量 + 换成卡片 role_id 前缀的变体。
    部分皮肤的 l2dName 数字前缀是 cfg id（如 30481_*），CDN 上实际挂在前缀
    不同的角色包下（如 30480_*），两种都探。"""
    raw = _l2d_spine_key(rec)
    if not raw:
        return []
    out = [raw]
    body = raw[len("Custom/"):]
    tail = re.sub(r"^\d+_", "", body)
    alt = f"Custom/prefabs_spine_{role_id}_{tail}"
    if alt != raw:
        out.append(alt)
    return out


def draw_candidates(rec: dict) -> list[str]:
    """立绘 key 大小写不稳定（cfg 里是 1001_Alps01_draw，CDN 上是小写），两种都探。"""
    img = str(rec.get("img") or "")
    if not img:
        return []
    return _draw_variants(f"Custom/textures_bigs_character_{img}")


def spine_candidates(role_id: int, rec: dict, ordinal: int, english: str) -> list[str]:
    """列出该装束可能对应的 CDN Spine key（按可信度排序）。"""
    names = _name_tokens(rec, english)
    if not names:
        return []
    skin_type = rec.get("skinType")
    out: list[str] = []
    if skin_type is None:
        for name in names:
            out += [f"prefabs_spine_{name}"]
        return out
    nn = f"{ordinal:02d}"
    for name in names:
        if skin_type == 1:
            out += [
                f"prefabs_spine_{role_id}_skin_{name}{nn}_spine",
                f"prefabs_spine_{role_id}_skin{nn}_{name}_spine",
                f"prefabs_spine_{role_id}_skin_{name}_spine",
            ]
        elif skin_type == 2:
            out += [
                f"prefabs_spine_{role_id}_break_{name}_spine",
                f"prefabs_spine_{role_id}_break{name}_spine",
            ]
        elif skin_type == 3:
            out += [f"prefabs_spine_{role_id}_synchro_{name}_spine"]
        elif skin_type == 4:
            out += [
                f"prefabs_spine_{role_id}_form_{name}_spine",
                f"prefabs_spine_{role_id}_change_{name}_spine",
            ]
    return out


def build(refresh: bool = False) -> dict:
    for name in CFG_FILES:
        _download(JSDELIVR_CFG.format(name=name), CFG_DIR / name, refresh)
    _read_ilist(refresh)  # 保留清单缓存（来源插件判断表情差分等可选资源用）

    cfg = {name: load_lua(CFG_DIR / name) for name in CFG_FILES}
    teams = {t["id"]: t for t in cfg["cfgCfgTeamEnum.lua"]}
    careers = {t["id"]: t for t in cfg["cfgCfgRoleEnum.lua"]}
    quality = {t["id"]: t for t in cfg["cfgCfgCardQuality.lua"]}
    rarity_letter = {t["id"]: t.get("sName", "") for t in cfg["cfgCfgRoleQualityEnum.lua"]}

    card_roles = cfg["cfgCfgCardRole.lua"]
    card_data = cfg["cfgCardData.lua"]
    characters_raw = cfg["cfgcharacter.lua"]

    by_role: dict[int, list[dict]] = {}
    for rec in characters_raw.values():
        if not isinstance(rec, dict):
            continue
        role = rec.get("role_id")
        if role in card_roles:
            by_role.setdefault(int(role), []).append(rec)

    # 语音表：group(voiceID) → 行
    voice_groups: dict[int, list[dict]] = {}
    for name in ("cfgSound.lua", "cfgSound1.lua", "cfgSound2.lua", "cfgSound3.lua"):
        for row in _sound_rows(cfg[name]):
            if not isinstance(row, dict):
                continue
            group = row.get("group")
            sheet = str(row.get("cue_sheet") or "")
            if not group or not sheet.startswith("cv/"):
                continue  # 只收角色语音，剧情 picture/*.acb 归其它模块
            voice_groups.setdefault(int(group), []).append({
                "cue": row.get("cue_name") or "",
                "category": _category(row.get("name")),
                "type": row.get("type"),
                "text": (row.get("script1") or row.get("script2") or "").strip(),
                "face": row.get("faceIndex"),
                "sheet": sheet,
                "play_time": row.get("playTime"),
            })

    # 清单镜像只覆盖部分资源（商店皮/新皮肤常常缺失），以 CDN HEAD 探测为准
    probe_keys: set[str] = set()
    for role_id, records in by_role.items():
        english = str((card_roles.get(role_id) or {}).get("eName") or "")
        for ordinal, rec in enumerate(sorted(records, key=lambda r: int(r.get("id") or 0)), start=1):
            probe_keys.update(draw_candidates(rec))
            l2d_key = _l2d_spine_key(rec)
            if l2d_key:
                probe_keys.add(l2d_key)
            probe_keys.update(_l2d_spine_candidates(rec, role_id))
            for key in spine_candidates(role_id, rec, ordinal, english):
                probe_keys.add(f"Custom/{key}")
    verified = _probe_keys(probe_keys, refresh=refresh)
    # 注意：_read_internation() 必须只调用一次——写进推导式 if 条件里会按每个
    # 候选 key 执行一遍；refresh=True 时等于重复下载几千次清单，同步永远跑不完。
    internation_list = _read_internation(refresh)
    internation = {k for k in verified if k in internation_list}
    custom_index = {_normalize_key(k): k for k in verified}
    print(f"[sync] CDN 确认资源 {len(verified)} / 候选 {len(probe_keys)}", flush=True)

    # 语音 id → 行（供 Spine 点击动作的 audioId 解析）
    sound_by_id: dict[int, dict] = {}
    for name in ("cfgSound.lua", "cfgSound1.lua", "cfgSound2.lua", "cfgSound3.lua"):
        for row in _sound_rows(cfg[name]):
            try:
                sound_by_id[int(row.get("id"))] = {
                    "cue": row.get("cue_name") or "",
                    "category": _category(row.get("name")),
                    "text": (row.get("script1") or row.get("script2") or "").strip(),
                    "sheet": row.get("cue_sheet") or "",
                }
            except Exception:  # noqa: BLE001
                continue

    # CfgSpineAction：每个模型（CfgCharacter id）的点击热区/动作/语音
    spine_actions: dict[int, dict] = {}
    for model_id, payload in (cfg["cfgCfgSpineAction.lua"] or {}).items():
        if not isinstance(payload, dict):
            continue
        items: list[dict] = []
        audio: dict[str, dict] = {}
        for raw in payload.get("item") or []:
            if not isinstance(raw, dict):
                continue
            item = {key: raw[key] for key in
                    ("index", "sName", "sType", "gesture", "hideTxt", "areas", "content")
                    if key in raw}
            ids: list[str] = []
            for aid in raw.get("audioId") or []:
                try:
                    key = int(aid)
                except Exception:  # noqa: BLE001
                    continue
                row = sound_by_id.get(key)
                if row and row.get("cue"):
                    audio[str(key)] = row
                ids.append(str(key))
            if ids:
                item["audioId"] = ids
            items.append(item)
        if items:
            spine_actions[int(model_id)] = {"items": items, "audio": audio}

    characters: list[dict] = []
    skins: list[dict] = []
    for role_id, card in sorted(card_roles.items()):
        data = card_data.get(role_id) or {}
        display = str(card.get("sAliasName") or card.get("sName") or "").strip()
        if not display:
            continue
        team = teams.get(card.get("sTeam"))
        career = careers.get(data.get("career"))
        q = int(data.get("quality") or 0)
        voice_id = None
        records = sorted(by_role.get(role_id, []), key=lambda r: int(r.get("id") or 0))
        if records and records[0].get("voiceID"):
            voice_id = int(records[0]["voiceID"])

        sheet = None
        for line in voice_groups.get(voice_id or -1, []):
            if line["sheet"].startswith("cv/cv_skin/"):
                continue
            sheet = line["sheet"]
            break
        characters.append({
            "name": display,
            "alias": str(card.get("sAliasName") or "").strip(),
            "title_name": str(card.get("sName") or "").strip(),
            "english_name": str(card.get("eName") or "").strip(),
            "role_id": role_id,
            "number": card.get("number"),
            "faction": (team or {}).get("sName", ""),
            "faction_icon": (team or {}).get("details1", ""),
            "career": (career or {}).get("sName", ""),
            "quality": q,
            "rarity": rarity_letter.get(q, ""),
            "grade": (quality.get(q) or {}).get("sName", ""),
            "voice_id": voice_id,
            "voice_sheet": sheet or "",
            "voice_actor_cn": str(card.get("sSounder_cn") or "").strip(),
            "desc": str(data.get("desc") or "").strip(),
        })

        english = str(card.get("eName") or "").strip()
        spine_map = assign_spine_keys(records, role_id, english, custom_index)
        for ordinal, rec in enumerate(records, start=1):
            skin_id = int(rec.get("id"))
            draw_key = _find_key(custom_index, f"Custom/textures_bigs_character_{rec.get('img', '')}")
            if not draw_key and rec.get("img"):
                # 少数 img 带 -half 后缀，实际资源键可能省略后缀
                draw_key = _find_key(
                    custom_index, f"Custom/textures_bigs_character_{str(rec['img']).replace('-half', '')}"
                )
            spine_key = spine_map.get(int(rec.get("id")))
            if draw_key:
                uncensored = _pick_internation_draw(draw_key, internation)
                if uncensored:
                    draw_key = uncensored
                    painting = draw_key.replace("Custom/", "").lower()
            if not draw_key and not spine_key:
                continue
            skin_voice = int(rec.get("voiceID") or 0) or voice_id
            # 语音包以 Sound 表里该 voiceID 的主 sheet 为准（同组可能混入其它 sheet，
            # 由 core/voice 按 cue 名匹配），这里不猜装束专属包
            sheet_skin = ""
            stype = "spine" if spine_key else "static"
            painting = (draw_key or spine_key).replace("Custom/", "").lower()
            skins.append({
                "character": display,
                "bundle": str(role_id),
                "name": str(rec.get("desc") or SKIN_TYPE_LABEL.get(rec.get("skinType"), "装束")),
                "painting": painting,
                "type": stype,
                "series": SKIN_TYPE_LABEL.get(rec.get("skinType"), "装束"),
                "skin_type": rec.get("skinType"),
                "skin_id": skin_id,
                "draw_key": draw_key or "",
                "spine_key": spine_key or "",
                "voice_id": skin_voice,
                "voice_sheet": sheet_skin or "",
                "image": rec.get("img") or "",
                "actions": spine_actions.get(skin_id),
            })

    # 语音表：只保留壁纸会用到的分类，控制体积
    voices = {}
    for group, lines in voice_groups.items():
        kept: list[dict] = []
        seen: set[str] = set()
        for line in lines:
            if line["category"] not in VOICE_CATEGORIES:
                continue
            if line["cue"] in seen:
                continue  # 同一 cue 在 Sound/Sound1 里重复出现
            seen.add(line["cue"])
            kept.append(line)
        if not kept:
            continue
        voices[str(group)] = {
            "sheet": kept[0]["sheet"],
            "lines": sorted(kept, key=lambda line: (str(line["cue"]))),
        }

    _write_json(MD / "characters.json", characters)
    _write_json(MD / "skins.json", skins)
    _write_json(MD / "voices.json", {"voices": voices})
    print(
        f"[sync] 角色 {len(characters)} / 装束 {len(skins)} "
        f"（动态 {sum(1 for s in skins if s['type'] == 'spine')}） / 语音组 {len(voices)}",
        flush=True,
    )
    return {"characters": len(characters), "skins": len(skins), "voices": len(voices)}


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="强制重新下载配置与清单")
    args = parser.parse_args(argv)
    started = time.time()
    result = build(refresh=args.refresh)
    print(f"[sync] 完成 用时 {time.time() - started:.1f}s", flush=True)
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
