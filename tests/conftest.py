import os
import textwrap

import pytest


@pytest.fixture
def repo(tmp_path):
    """Write a workflow (and optional extra files) into a temp repo and return its path."""
    def make(workflow: str, name="ci.yml", files=None):
        wd = tmp_path / ".github" / "workflows"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / name).write_text(textwrap.dedent(workflow))
        for rel, content in (files or {}).items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content))
        return str(tmp_path)
    return make
