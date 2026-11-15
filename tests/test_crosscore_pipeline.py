"""离线单测：Lua 表解析、伪文件头剥离、元数据产物一致性。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from lua_parse import LuaTableParser  # noqa: E402

from core.unityfs import strip_fake_header  # noqa: E402


class LuaParserTest(unittest.TestCase):
    def test_keyed_table_with_array_and_long_string(self):
        text = (
            '\ufeff_G["X"]={[1001]={["name"]="阿尔卑斯",["tags"]={"a","b"},'
            '["desc"]=[[第一行\n第二行]],["open"]=true,["lv"]=1}}'
        )
        data = LuaTableParser(text).parse()
        self.assertEqual(data[1001]["name"], "阿尔卑斯")
        self.assertEqual(data[1001]["tags"], ["a", "b"])
        self.assertIn("第二行", data[1001]["desc"])
        self.assertTrue(data[1001]["open"])

    def test_local_conf_column_table(self):
        text = (
            "local conf = {\n['names']={'id','key','name'},\n['data']={\n"
            "{'1','alps_1','登录'},\n{'2','alps_2','看板'}\n}\n}"
        )
        data = LuaTableParser(text).parse()
        self.assertEqual(data["data"][0], ["1", "alps_1", "登录"])


class BundleHeaderTest(unittest.TestCase):
    def test_strip_fake_header_uses_second_magic(self):
        fake = b"UnityFS\x00\x00\x00\x00\x08" + b"5.x.x\x00" + b"garbage"
        real = b"UnityFS\x00\x00\x00\x00\x08" + b"2022.3.62f2c1\x00" + b"payload"
        self.assertEqual(strip_fake_header(fake + real), real)

    def test_no_double_header_keeps_full_payload(self):
        bundle = b"UnityFS\x00\x00\x00\x00\x08payload"
        self.assertEqual(strip_fake_header(bundle), bundle)


class MetadataTest(unittest.TestCase):
    def setUp(self):
        self.characters = json.loads(
            (ROOT / "resources/metadata/characters.json").read_text(encoding="utf-8")
        )
        self.skins = json.loads(
            (ROOT / "resources/metadata/skins.json").read_text(encoding="utf-8")
        )
        self.voices = json.loads(
            (ROOT / "resources/metadata/voices.json").read_text(encoding="utf-8")
        )

    def test_characters_is_bare_list(self):
        self.assertIsInstance(self.characters, list)
        self.assertGreater(len(self.characters), 100)
        first = self.characters[0]
        for key in ("name", "role_id", "career", "rarity", "faction"):
            self.assertIn(key, first)

    def test_skin_types_and_unique_spine_keys(self):
        self.assertIsInstance(self.skins, list)
        types = {skin["type"] for skin in self.skins}
        self.assertLessEqual(types, {"spine", "static"})
        keys = [skin["spine_key"] for skin in self.skins if skin.get("spine_key")]
        self.assertEqual(len(keys), len(set(keys)), "同一个 Spine 包不能挂到多个装束上")

    def test_multi_skin_roster(self):
        """商店皮/多装束必须存在：每个角色至少 1 套，且存在 4 套以上的角色。"""
        from collections import Counter

        counts = Counter(skin["character"] for skin in self.skins)
        self.assertGreaterEqual(max(counts.values()), 4)
        self.assertGreater(len(self.skins), len(counts))

    def test_spine_skins_have_draw_or_spine(self):
        for skin in self.skins:
            if skin["type"] == "spine":
                self.assertTrue(skin["spine_key"].startswith("Custom/prefabs_spine_"))

    def test_voice_catalog_categories(self):
        voices = self.voices.get("voices", {})
        self.assertGreater(len(voices), 50)
        categories = {line["category"] for entry in voices.values() for line in entry["lines"]}
        self.assertIn("登录", categories)
        self.assertIn("接触1", categories)
        # 皮肤语音的装束后缀必须已被归一化
        self.assertFalse(any("（" in c or "(" in c for c in categories))


if __name__ == "__main__":
    unittest.main()
