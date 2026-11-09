"""Load YAML while keeping line numbers (1-based) for every mapping key and list item."""
from __future__ import annotations

import yaml


class LMap(dict):
    """dict that remembers the line of each key."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lines: dict = {}
        self.line = 0

    def line_of(self, key, default=0):
        return self.lines.get(key, default or self.line)


class LList(list):
    def __init__(self, *a):
        super().__init__(*a)
        self.lines: list = []
        self.line = 0


class LStr(str):
    line = 0
    style = ""


def _convert(node):
    if isinstance(node, yaml.MappingNode):
        out = LMap()
        out.line = node.start_mark.line + 1
        for k, v in node.value:
            key = _convert(k)
            if isinstance(key, LStr):
                key = str(key)
            elif isinstance(key, bool) and key is True:  # YAML 1.1 turns `on:` into True
                key = "on"
            out[key] = _convert(v)
            out.lines[key] = k.start_mark.line + 1
        return out
    if isinstance(node, yaml.SequenceNode):
        out = LList()
        out.line = node.start_mark.line + 1
        for item in node.value:
            out.append(_convert(item))
            out.lines.append(item.start_mark.line + 1)
        return out
    # scalar: let PyYAML resolve the type (int, float, bool, null, str)
    value = yaml.SafeLoader.construct_scalar(_LOADER, node) if node.tag == "tag:yaml.org,2002:str" else None
    if node.tag == "tag:yaml.org,2002:str":
        s = LStr(value)
        s.line = node.start_mark.line + 1
        s.style = node.style or ""
        return s
    return _LOADER.construct_object(node, deep=True)


_LOADER = yaml.SafeLoader("")


def load(text: str):
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        return None
    return _convert(node)
