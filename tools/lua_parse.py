"""最小 Lua 数据表解析器（只支持 CrossCore cfg_*.lua 出现的语法子集）。

支持的写法：`_G["X"] = { ... }`、`["key"]=v`、`[123]=v`、`name=v`、裸数组项、
字符串（"..." / '...' / [[...]]）、数字、true/false/nil、尾随逗号。
返回 Python dict / list。
"""

from __future__ import annotations

import re
from pathlib import Path


class LuaTableParser:
    def __init__(self, text: str) -> None:
        self.s = text.lstrip("\ufeff")
        self.i = 0

    def parse(self):
        self._skip_ws()
        m = re.match(r'_G\s*\[\s*"[^"]*"\s*\]\s*=\s*', self.s)
        if not m:
            # cfgSound1..3 以 `local conf = { ... }` 开头
            m = re.match(r"local\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*", self.s)
        if m:
            self.i = m.end()
        return self._parse_value()

    def _skip_ws(self) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif s.startswith("--", self.i):
                nl = s.find("\n", self.i)
                self.i = n if nl < 0 else nl + 1
            else:
                break

    def _expect(self, ch: str) -> None:
        self._skip_ws()
        if self.s[self.i] != ch:
            raise ValueError(f"expected {ch!r} at {self.i}: {self.s[self.i:self.i + 30]!r}")
        self.i += 1

    def _parse_value(self):
        self._skip_ws()
        c = self.s[self.i]
        if c == "{":
            return self._parse_table()
        if c in "\"'":
            return self._parse_string()
        if self.s.startswith("[[", self.i):
            end = self.s.find("]]", self.i + 2)
            text = self.s[self.i + 2:end]
            self.i = end + 2
            return text
        for word, value in (("true", True), ("false", False), ("nil", None)):
            if self.s.startswith(word, self.i):
                self.i += len(word)
                return value
        m = re.match(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", self.s[self.i:])
        if m:
            self.i += m.end()
            text = m.group(0)
            return float(text) if ("." in text or "e" in text.lower()) else int(text)
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", self.s[self.i:])
        if m:
            self.i += m.end()
            return m.group(0)
        raise ValueError(f"unexpected {c!r} at {self.i}: {self.s[self.i:self.i + 30]!r}")

    def _parse_string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                nxt = self.s[self.i + 1]
                mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
                out.append(mapping.get(nxt, nxt))
                self.i += 2
            elif c == quote:
                self.i += 1
                return "".join(out)
            else:
                out.append(c)
                self.i += 1
        raise ValueError("unterminated string")

    def _parse_table(self):
        self._expect("{")
        items: list = []
        keyed: dict = {}
        has_key = False
        while True:
            self._skip_ws()
            if self.i >= len(self.s):
                raise ValueError("unterminated table")
            if self.s[self.i] == "}":
                self.i += 1
                break
            if self.s[self.i] == "[":
                save = self.i
                self.i += 1
                self._skip_ws()
                key = self._parse_string() if self.s[self.i] in "\"'" else self._parse_value()
                self._skip_ws()
                if self.s[self.i] != "]":
                    self.i = save
                    items.append(self._parse_value())
                else:
                    self.i += 1
                    self._skip_ws()
                    self._expect("=")
                    keyed[key] = self._parse_value()
                    has_key = True
            else:
                m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", self.s[self.i:])
                if m:
                    self.i += m.end()
                    keyed[m.group(1)] = self._parse_value()
                    has_key = True
                else:
                    items.append(self._parse_value())
            self._skip_ws()
            if self.i < len(self.s) and self.s[self.i] in ",;":
                self.i += 1
        if not has_key:
            return items
        for index, value in enumerate(items, start=1):
            keyed.setdefault(index, value)
        return keyed


def load(path: str | Path):
    return LuaTableParser(Path(path).read_text(encoding="utf-8-sig")).parse()
