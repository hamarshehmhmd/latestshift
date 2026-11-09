"""Read GitHub Actions workflow files, expand job matrices and resolve runs-on labels."""
from __future__ import annotations

import itertools
import os
import re
from dataclasses import dataclass, field

from .yamlload import LMap, load

AFFECTED_LABEL = "ubuntu-latest"
MAX_COMBOS = 256
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
MATRIX_REF = re.compile(r"^matrix((?:\.[A-Za-z0-9_-]+|\[['\"][^'\"]+['\"]\])+)$")


def scalar(v) -> str:
    """Render a YAML scalar the way the runner would pass it to an action input."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _lookup(combo: dict, path: str):
    parts = re.findall(r"\.([A-Za-z0-9_-]+)|\[['\"]([^'\"]+)['\"]\]", path)
    cur = combo
    for a, b in parts:
        key = a or b
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def substitute(value, combo: dict):
    """Replace ${{ matrix.x }} references using one matrix combination.

    Returns the substituted string, or the value itself when it is not a string.
    A whole-value reference to a list/dict returns that object."""
    if not isinstance(value, str) or "${{" not in value:
        return value
    whole = EXPR.fullmatch(value.strip())
    if whole:
        m = MATRIX_REF.match(whole.group(1))
        if m:
            got = _lookup(combo, m.group(1))
            if got is not None:
                return got if isinstance(got, (list, dict)) else scalar(got)
        return value

    def rep(m):
        ref = MATRIX_REF.match(m.group(1))
        if ref:
            got = _lookup(combo, ref.group(1))
            if got is not None and not isinstance(got, (list, dict)):
                return scalar(got)
        return m.group(0)

    return EXPR.sub(rep, value)


def deep_substitute(obj, combo):
    if isinstance(obj, dict):
        out = type(obj)() if isinstance(obj, LMap) else {}
        if isinstance(obj, LMap):
            out.lines, out.line = obj.lines, obj.line
        for k, v in obj.items():
            out[k] = deep_substitute(v, combo)
        return out
    if isinstance(obj, list):
        return [deep_substitute(v, combo) for v in obj]
    if isinstance(obj, str):
        s = substitute(obj, combo)
        if isinstance(s, str) and hasattr(obj, "line") and not hasattr(s, "line"):
            from .yamlload import LStr
            n = LStr(s)
            n.line = obj.line
            return n
        return s
    return obj


def expand_matrix(matrix) -> tuple[list[dict], str | None]:
    """Expand a strategy.matrix into combinations, following GitHub's include/exclude rules.

    Returns (combos, note). A matrix given as an expression cannot be expanded offline."""
    if matrix is None:
        return [{}], None
    if isinstance(matrix, str):
        return [{}], "matrix is an expression (" + matrix.strip()[:60] + "); values unknown offline"
    if not isinstance(matrix, dict):
        return [{}], "matrix has an unexpected shape"
    note = None
    base_keys = [k for k in matrix if k not in ("include", "exclude")]
    axes = []
    for k in base_keys:
        v = matrix[k]
        if isinstance(v, list):
            axes.append([(k, x) for x in v])
        else:
            axes.append([(k, v)])
            if isinstance(v, str) and "${{" in v:
                note = f"matrix.{k} is an expression; values unknown offline"
    combos = [dict(c) for c in itertools.product(*axes)] if base_keys else []
    if len(combos) > MAX_COMBOS:
        combos = combos[:MAX_COMBOS]
        note = f"matrix truncated to {MAX_COMBOS} combinations"
    excl = matrix.get("exclude") or []
    if isinstance(excl, list):
        def excluded(c):
            for e in excl:
                if isinstance(e, dict) and e and all(scalar(c.get(k)) == scalar(v) for k, v in e.items()):
                    return True
            return False
        combos = [c for c in combos if not excluded(c)]
    incl = matrix.get("include") or []
    if isinstance(incl, list):
        originals = [dict(c) for c in combos]
        for e in incl:
            if not isinstance(e, dict):
                continue
            matched = False
            for i, orig in enumerate(originals):
                # include extends a combination when it does not overwrite any original matrix value
                if all(k not in orig or scalar(orig[k]) == scalar(v) for k, v in e.items() if k in base_keys):
                    combos[i].update({k: v for k, v in e.items() if k not in base_keys or k not in orig})
                    matched = True
            if not matched:
                combos.append(dict(e))
                originals.append(dict(e))
    if not combos:
        combos = [{}]
    return combos, note


def labels_of(runs_on) -> list[str]:
    if runs_on is None:
        return []
    if isinstance(runs_on, str):
        return [runs_on.strip()]
    if isinstance(runs_on, list):
        return [scalar(x).strip() for x in runs_on]
    if isinstance(runs_on, dict):
        lab = runs_on.get("labels")
        out = labels_of(lab)
        if runs_on.get("group"):
            out.append("group:" + scalar(runs_on["group"]))
        return out
    return [scalar(runs_on)]


@dataclass
class JobVariant:
    """One concrete matrix combination of a job."""
    workflow: str
    job_id: str
    name: str
    combo: dict
    labels: list
    job: dict
    line: int
    steps: list = field(default_factory=list)

    @property
    def affected(self) -> bool:
        return AFFECTED_LABEL in self.labels

    @property
    def unresolved(self) -> bool:
        return any("${{" in l for l in self.labels)

    @property
    def combo_label(self) -> str:
        if not self.combo:
            return ""
        return ", ".join(f"{k}={scalar(v)}" for k, v in self.combo.items() if not isinstance(v, (dict, list)))


@dataclass
class Workflow:
    path: str
    rel: str
    data: dict
    text: str
    error: str | None = None


def find_workflows(root: str) -> list[Workflow]:
    wdir = os.path.join(root, ".github", "workflows")
    paths = []
    if os.path.isfile(root) and root.endswith((".yml", ".yaml")):
        paths = [root]
        root = os.path.dirname(root)
    elif os.path.isdir(wdir):
        paths = sorted(
            os.path.join(wdir, f) for f in os.listdir(wdir) if f.endswith((".yml", ".yaml"))
        )
    out = []
    for p in paths:
        rel = os.path.relpath(p, root if os.path.isdir(root) else os.path.dirname(p))
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        try:
            data = load(text)
            err = None if isinstance(data, dict) else "not a workflow mapping"
        except Exception as e:  # yaml errors carry a line number in the message
            data, err = None, f"YAML error: {str(e).splitlines()[0]}"
        out.append(Workflow(p, rel, data if isinstance(data, dict) else {}, text, err))
    return out


def job_variants(wf: Workflow) -> tuple[list[JobVariant], list[str]]:
    notes = []
    jobs = wf.data.get("jobs") if isinstance(wf.data, dict) else None
    if not isinstance(jobs, dict):
        return [], notes
    out = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        line = jobs.line_of(job_id) if isinstance(jobs, LMap) else 0
        if "uses" in job and "runs-on" not in job:
            notes.append(f"{wf.rel}: job '{job_id}' calls a reusable workflow ({scalar(job['uses'])}); scan that repo too")
            continue
        strategy = job.get("strategy") if isinstance(job.get("strategy"), dict) else {}
        combos, note = expand_matrix(strategy.get("matrix"))
        if note:
            notes.append(f"{wf.rel}: job '{job_id}': {note}")
        for combo in combos:
            j = deep_substitute(job, combo)
            labels = labels_of(j.get("runs-on"))
            steps = j.get("steps") if isinstance(j.get("steps"), list) else []
            raw_steps = job.get("steps") if isinstance(job.get("steps"), list) else []
            lines = getattr(raw_steps, "lines", [])
            for i, s in enumerate(steps):
                if isinstance(s, dict):
                    s["__line__"] = lines[i] if i < len(lines) else line
            name = scalar(job.get("name") or job_id)
            out.append(JobVariant(wf.rel, str(job_id), name, combo, labels, j, line, steps))
    return out, notes
