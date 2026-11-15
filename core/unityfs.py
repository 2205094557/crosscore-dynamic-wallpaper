"""交错战线 AssetBundle 读取工具：伪文件头剥离 + 纹理导出。"""

from __future__ import annotations

from pathlib import Path

import UnityPy


def strip_fake_header(data: bytes) -> bytes:
    """官方 Bundle 在真正 UnityFS 之前插了一段假头；从第二个 `UnityFS` 起才是有效包。"""
    first = data.find(b"UnityFS")
    if first < 0:
        raise ValueError("不是 UnityFS 资源包")
    second = data.find(b"UnityFS", first + 1)
    return data[second:] if second > 0 else data[first:]


def load_bundle(path: str | Path):
    """剥头后直接用内存字节打开（避免在 Windows 上留下占用的临时文件句柄）。"""
    return UnityPy.load(strip_fake_header(Path(path).read_bytes()))


def texture_to_png(texture, dest: Path) -> tuple[int, int]:
    """Texture2D → PNG。UnityPy 的 image 已经是正确朝向（不要翻转，实测翻转会错位）。"""
    image = texture.image
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return image.size


def largest_texture(env):
    best = None
    best_area = 0
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        data = obj.read()
        area = int(getattr(data, "m_Width", 0)) * int(getattr(data, "m_Height", 0))
        if area > best_area:
            best, best_area = data, area
    return best
