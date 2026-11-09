"""Generate tests/fixtures/cassette.json: a recorded-style example with made-up repo and job names."""
import json
import os

def log(image, version, osv):
    ts = "2026-11-05T10:00:00.0000000Z "
    lines = ["Current runner version: '2.338.2'", "##[group]Operating System", "Ubuntu", osv, "LTS", "##[endgroup]",
             "##[group]Runner Image", f"Image: {image}", f"Version: {version}", "##[endgroup]", "Prepare workflow directory"]
    return "".join(ts + l + "\n" for l in lines)

L24 = log("ubuntu-24.04", "20261025.353.1", "24.04.5")
L26 = log("ubuntu-26.04", "20261101.186.1", "26.04.1")
runs, jobs, logs = [], {}, {}
plan = [  # (run_id, day, attempt, {job: (image, conclusion)})
    (101, "2026-11-02", 1, {"build": (L24, "success"), "lint": (L26, "success"), "docs": (L24, "success"), "e2e": (L24, "failure")}),
    (102, "2026-11-03", 1, {"build": (L24, "success"), "lint": (L24, "success"), "docs": (L24, "success"), "e2e": (L26, "failure")}),
    (103, "2026-11-04", 1, {"build": (L26, "failure"), "lint": (L26, "success"), "docs": (L24, "success"), "e2e": (L24, "success")}),
    (103, "2026-11-04", 2, {"build": (L24, "success")}),
    (104, "2026-11-05", 1, {"build": (L26, "failure"), "lint": (L26, "success"), "docs": (L24, "success"), "e2e": (L26, "failure")}),
    (105, "2026-11-06", 1, {"build": (L24, "success"), "lint": (L26, "success"), "docs": (L24, "cancelled"), "e2e": (L24, "success")}),
]
jid = 9000
for run_id, day, attempt, js in plan:
    if attempt == 1:
        runs.append({"id": run_id, "name": "CI", "workflow_id": 1, "created_at": day + "T10:00:00Z"})
    for name, (lg, concl) in js.items():
        jid += 1
        jobs.setdefault(str(run_id), []).append({
            "id": jid, "name": name, "run_attempt": attempt, "conclusion": concl, "labels": ["ubuntu-latest"],
            "started_at": f"{day}T10:0{attempt}:00Z",
            "html_url": f"https://github.com/acme/shop/actions/runs/{run_id}/job/{jid}"})
        logs[str(jid)] = lg
# a job pinned to ubuntu-24.04 is ignored unless --all-labels
jobs["105"].append({"id": 9999, "name": "release", "run_attempt": 1, "conclusion": "success", "labels": ["ubuntu-24.04"],
                    "started_at": "2026-11-06T10:05:00Z", "html_url": "https://github.com/acme/shop/actions/runs/105/job/9999"})
logs["9999"] = L24
runs.reverse()  # API order: newest first
out = os.path.join(os.path.dirname(__file__), "cassette.json")
with open(out, "w") as fh:
    json.dump({"runs": runs, "jobs": jobs, "logs": logs}, fh, indent=1)
print(out)
