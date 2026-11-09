"""Rewrite ubuntu-latest to an explicit label (default ubuntu-24.04) in workflow files."""
from __future__ import annotations

import difflib
import os
import re

from .workflow import find_workflows

TOKEN = re.compile(r"(?<![\w.-])ubuntu-latest(?![\w.-])")


def _rewrite_line(line: str, to: str) -> str:
    # leave comments alone: split off a trailing `# ...` that is not inside quotes
    code, comment = line, ""
    quote = None
    for i, ch in enumerate(line):
        if ch in "'\"" and (quote is None or quote == ch):
            quote = None if quote else ch
        elif ch == "#" and quote is None and (i == 0 or line[i - 1] in " \t"):
            code, comment = line[:i], line[i:]
            break
    return TOKEN.sub(to, code) + comment


def pin(root: str, to: str = "ubuntu-24.04", files=None):
    """Return a list of (path, old_text, new_text) for files that change."""
    out = []
    for wf in find_workflows(root):
        if files and wf.rel not in files and os.path.basename(wf.rel) not in files:
            continue
        new = "".join(_rewrite_line(l, to) for l in wf.text.splitlines(keepends=True))
        if new != wf.text:
            out.append((wf.path, wf.rel, wf.text, new))
    return out


def unified(changes) -> str:
    chunks = []
    for _, rel, old, new in changes:
        chunks.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           fromfile="a/" + rel, tofile="b/" + rel, n=1))
    return "".join(chunks)


def write(changes):
    for path, _, _, new in changes:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
