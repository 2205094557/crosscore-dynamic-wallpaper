"""扫描 resources/extracted → 生成 local_skins.json（本地已提取装束索引）。

产物约定：
  extracted/static/{painting}/painting.png
  extracted/spine/{painting}/character.json + character.atlas + character.png + spine.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "resources" / "metadata"
EXTRACTED = ROOT / "resources" / "extracted"


def _catalog() -> dict[str, dict]:
    path = MD / "skins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(data, dict):
        skins = data.get("skins", [])
    elif isinstance(data, list):
        skins = data
    else:
        skins = []
    return {str(item.get("painting") or "").lower(): item for item in skins}


def build() -> list[dict]:
    catalog = _catalog()
    out: list[dict] = []
    for stype in ("spine", "static"):
        base = EXTRACTED / stype
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            if stype == "static":
                image = folder / "painting.png"
                if not image.is_file():
                    continue
                asset = {
                    "dir": f"extracted/static/{folder.name}",
                    "image": "painting.png",
                }
            else:
                model = folder / "character.json"
                if not model.is_file() or not (folder / "character.atlas").is_file():
                    continue
                asset = {
                    "dir": f"extracted/spine/{folder.name}",
                    "model": "character.json",
                    "atlas": "character.atlas",
                    "image": "character.png",
                }
                meta = folder / "spine.json"
                if meta.is_file():
                    try:
                        asset["animations"] = json.loads(meta.read_text(encoding="utf-8")).get("animations", [])
                    except Exception:  # noqa: BLE001
                        pass
            item = catalog.get(folder.name.lower()) or {}
            out.append({
                "character": item.get("character") or "",
                "bundle": item.get("bundle") or "",
                "name": item.get("name") or folder.name,
                "painting": folder.name.lower(),
                "type": stype,
                "asset": asset,
            })
    return out


def main() -> None:
    payload = build()
    MD.mkdir(parents=True, exist_ok=True)
    path = MD / "local_skins.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    print(f"[index] 本地装束 {len(payload)}", flush=True)


if __name__ == "__main__":
    main()
