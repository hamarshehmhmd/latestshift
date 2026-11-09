"""Look at real runs: which image did each ubuntu-latest job get, and does it fail only on 26.04?"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
LOG_HEAD_BYTES = 96 * 1024
IMAGE_RE = re.compile(r"Image:\s*(ubuntu-[\w.]+)")
VERSION_RE = re.compile(r"Image:\s*ubuntu-[\w.]+\s*\n[^\n]*?Version:\s*(\S+)")
OS_RE = re.compile(r"##\[group\]Operating System\s*\n[^\n]*?Ubuntu\s*\n[^\n]*?(\d+\.\d+)")


class ApiError(Exception):
    pass


def token() -> str | None:
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    if shutil.which("gh"):
        try:
            t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            return t or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Minimal GitHub REST client (stdlib only)."""

    def __init__(self, tok: str | None):
        self.tok = tok
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _req(self, url, accept="application/vnd.github+json"):
        r = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "latestshift",
                                                 "X-GitHub-Api-Version": "2022-11-28"})
        if self.tok:
            r.add_header("Authorization", "Bearer " + self.tok)
        return r

    def get_json(self, path: str, params: dict | None = None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with urllib.request.urlopen(self._req(url), timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise ApiError(f"GET {path}: HTTP {e.code} {e.reason}") from None
        except urllib.error.URLError as e:
            raise ApiError(f"GET {path}: {e.reason}") from None

    def get_log_head(self, repo: str, job_id: int) -> str:
        """First ~96 KB of a job log. The API answers with a redirect to blob storage, which must be
        fetched without the GitHub token."""
        url = f"{API}/repos/{repo}/actions/jobs/{job_id}/logs"
        try:
            resp = self.opener.open(self._req(url), timeout=30)
            data = resp.read(LOG_HEAD_BYTES)
            return data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                loc = e.headers["Location"]
                r = urllib.request.Request(loc, headers={"User-Agent": "latestshift",
                                                         "Range": f"bytes=0-{LOG_HEAD_BYTES - 1}"})
                try:
                    with urllib.request.urlopen(r, timeout=30) as resp:
                        return resp.read(LOG_HEAD_BYTES).decode("utf-8", "replace")
                except urllib.error.URLError:
                    return ""
            if e.code in (404, 410):
                return ""  # logs expired or deleted
            raise ApiError(f"job {job_id} log: HTTP {e.code} {e.reason}") from None


def parse_image(log: str) -> tuple[str | None, str | None]:
    log = re.sub(r"^\ufeff?\d{4}-\d\d-\d\dT[\d:.]+Z ", "", log, flags=re.M)
    m = IMAGE_RE.search(log)
    if m:
        v = VERSION_RE.search(log)
        return m.group(1), (v.group(1) if v else None)
    m = OS_RE.search(log)
    if m:
        return "ubuntu-" + m.group(1)[:5], None
    return None, None


@dataclass
class JobRun:
    workflow: str
    name: str
    run_id: int
    attempt: int
    job_id: int
    conclusion: str
    started: str
    url: str
    labels: list
    image: str | None = None
    image_version: str | None = None


@dataclass
class Group:
    workflow: str
    name: str
    runs: list = field(default_factory=list)

    def tally(self):
        t = {}
        for r in self.runs:
            img = (r.image or "unknown").replace("ubuntu-", "")
            k = (img, "pass" if r.conclusion == "success" else "fail" if r.conclusion in ("failure", "timed_out") else None)
            if k[1]:
                t[k] = t.get(k, 0) + 1
        return t

    def verdict(self) -> str:
        t = self.tally()
        f26, p26 = t.get(("26.04", "fail"), 0), t.get(("26.04", "pass"), 0)
        f24, p24 = t.get(("24.04", "fail"), 0), t.get(("24.04", "pass"), 0)
        if f26 and not f24 and p24 and not p26:
            return "FAILS ONLY ON 26.04"
        if f26 and not f24 and p24 and p26:
            return "fails more on 26.04"
        if f26 and f24:
            return "fails on both"
        if p26 and not f26:
            return "ok on 26.04"
        if not (f26 or p26):
            return "not on 26.04 yet"
        return "fails on 26.04"


def collect(client, repo: str, limit_runs: int = 30, branch: str | None = None, workflow: str | None = None,
            max_logs: int = 80, all_labels: bool = False, progress=None):
    params = {"per_page": min(100, limit_runs), "status": "completed"}
    if branch:
        params["branch"] = branch
    path = f"/repos/{repo}/actions/runs"
    if workflow:
        path = f"/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow)}/runs"
    runs = client.get_json(path, params).get("workflow_runs", [])[:limit_runs]
    groups: "OrderedDict[tuple, Group]" = OrderedDict()
    todo = []
    for run in runs:
        jobs = client.get_json(f"/repos/{repo}/actions/runs/{run['id']}/jobs", {"filter": "all", "per_page": 100}).get("jobs", [])
        for j in jobs:
            labels = j.get("labels") or []
            if not all_labels and "ubuntu-latest" not in labels:
                continue
            if all_labels and not any(str(l).startswith("ubuntu") for l in labels):
                continue
            if j.get("conclusion") in (None, "skipped", "cancelled", "neutral"):
                continue
            jr = JobRun(run.get("name") or str(run.get("workflow_id")), j.get("name", "?"), run["id"],
                        j.get("run_attempt", 1), j["id"], j.get("conclusion"), j.get("started_at") or "",
                        j.get("html_url", ""), labels)
            groups.setdefault((jr.workflow, jr.name), Group(jr.workflow, jr.name)).runs.append(jr)
            todo.append(jr)
    todo = todo[:max_logs]

    def fetch(jr):
        jr.image, jr.image_version = parse_image(client.get_log_head(repo, jr.job_id))
        return jr

    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, _ in enumerate(pool.map(fetch, todo), 1):
            if progress:
                progress(i)
    return list(groups.values()), len(runs), len(todo)


class CassetteClient:
    """Replays recorded API answers from a JSON file (tests and the recorded demo).

    Format: {"runs": [...workflow_runs...], "jobs": {"<run_id>": [...jobs...]}, "logs": {"<job_id>": "log text"}}"""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as fh:
            self.data = json.load(fh)

    def get_json(self, path: str, params=None):
        m = re.search(r"/actions/runs/(\d+)/jobs$", path)
        if m:
            return {"jobs": self.data.get("jobs", {}).get(m.group(1), [])}
        if path.endswith("/runs"):
            return {"workflow_runs": self.data.get("runs", [])}
        raise ApiError(f"GET {path}: not in cassette")

    def get_log_head(self, repo, job_id):
        return self.data.get("logs", {}).get(str(job_id), "")
