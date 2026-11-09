"""Scan a repository's workflows for jobs on ubuntu-latest and what will break on Ubuntu 26.04."""
from __future__ import annotations

from dataclasses import dataclass, field

from .rules import SEV_ORDER, Finding, Repo, check_job
from .workflow import find_workflows, job_variants


@dataclass
class JobSummary:
    workflow: str
    job: str
    line: int
    labels: list
    status: str  # moving | pinned | unresolved | other


@dataclass
class ScanResult:
    root: str
    workflows: int = 0
    jobs: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def moving(self):
        return [j for j in self.jobs if j.status == "moving"]

    def counts(self):
        c = {"break": 0, "risk": 0, "info": 0}
        for f in self.findings:
            c[f.severity] += 1
        return c

    def worst(self):
        return max((SEV_ORDER[f.severity] for f in self.findings), default=-1)

    def to_dict(self):
        return {
            "root": self.root,
            "workflows": self.workflows,
            "jobs": [j.__dict__ for j in self.jobs],
            "findings": [f.to_dict() for f in self.findings],
            "counts": self.counts(),
            "notes": self.notes,
            "errors": self.errors,
        }


def _status(labels, unresolved):
    if "ubuntu-latest" in labels:
        return "moving"
    if unresolved:
        return "unresolved"
    if any(l.startswith("ubuntu-2") for l in labels):
        return "pinned"
    return "other"


def scan(root: str, include_pinned: bool = False) -> ScanResult:
    """include_pinned also checks jobs already on ubuntu-26.04 (useful when moving them on purpose)."""
    res = ScanResult(root)
    repo = Repo(root)
    wfs = find_workflows(root)
    res.workflows = len(wfs)
    merged: dict = {}
    for wf in wfs:
        if wf.error:
            res.errors.append(f"{wf.rel}: {wf.error}")
            continue
        variants, notes = job_variants(wf)
        res.notes.extend(notes)
        per_job: dict = {}
        for v in variants:
            st = _status(v.labels, v.unresolved)
            prev = per_job.get(v.job_id)
            rank = {"moving": 3, "unresolved": 2, "pinned": 1, "other": 0}
            if prev is None or rank[st] > rank[prev.status]:
                labels = sorted(set((prev.labels if prev else []) + v.labels))
                per_job[v.job_id] = JobSummary(wf.rel, v.job_id, v.line, labels, st)
            else:
                prev.labels = sorted(set(prev.labels + v.labels))
            check = v.affected or (include_pinned and "ubuntu-26.04" in v.labels)
            if not check:
                continue
            for f in check_job(v, repo):
                k = f.key()
                if k in merged:
                    if f.combo and f.combo not in merged[k].combo.split(" | "):
                        merged[k].combo = (merged[k].combo + " | " + f.combo).strip(" |")
                else:
                    merged[k] = f
        res.jobs.extend(per_job.values())
        for j in per_job.values():
            if j.status == "unresolved":
                res.notes.append(f"{wf.rel}: job '{j.job}' runs-on {', '.join(j.labels)} could not be resolved offline")
    res.findings = sorted(merged.values(), key=lambda f: (-SEV_ORDER[f.severity], f.file, f.line, f.rule))
    return res


def filter_findings(findings: list[Finding], min_severity: str) -> list[Finding]:
    lo = SEV_ORDER[min_severity]
    return [f for f in findings if SEV_ORDER[f.severity] >= lo]
