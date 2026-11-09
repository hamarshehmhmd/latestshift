"""latestshift command line."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .rules import RULE_IDS, SEV_ORDER
from .scan import filter_findings, scan

COLORS = {"break": "31", "risk": "33", "info": "36", "dim": "2", "ok": "32", "bold": "1", "head": "35"}


class Out:
    def __init__(self, color: str):
        if color == "always":
            self.on = True
        elif color == "never":
            self.on = False
        else:
            self.on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def c(self, kind, text):
        if not self.on:
            return text
        return f"\x1b[{COLORS[kind]}m{text}\x1b[0m"


def _sev_tag(o, sev):
    return o.c(sev, {"break": "BREAK", "risk": "RISK ", "info": "INFO "}[sev])


def print_scan_text(res, shown, o, verbose=False):
    moving = res.moving
    pinned = [j for j in res.jobs if j.status == "pinned"]
    print(o.c("bold", f"latestshift: {res.workflows} workflow file(s), {len(res.jobs)} job(s)"))
    print(f"  {o.c('break' if moving else 'ok', str(len(moving)))} job(s) on ubuntu-latest (moving to Ubuntu 26.04 by Nov 19, 2026)"
          f", {len(pinned)} pinned to an explicit ubuntu-XX.04")
    for e in res.errors:
        print("  " + o.c("break", "error: ") + e)
    if not shown:
        if moving:
            print(o.c("ok", "  no known 26.04 breakages found in these jobs") +
                  o.c("dim", " (that is not a guarantee: try runs-on: ubuntu-26.04 in a branch)"))
        for n in res.notes if verbose else []:
            print(o.c("dim", "  note: " + n))
        return
    print()
    for f in shown:
        loc = o.c("dim", f"{f.file}:{f.line}")
        print(f"{_sev_tag(o, f.severity)} {loc}  job {o.c('bold', f.job)}  {o.c('dim', '[' + f.rule + ']')}")
        if f.combo:
            print(o.c("dim", f"       matrix: {f.combo[:110]}"))
        print(f"       {f.message}")
        print(f"       {o.c('ok', 'fix:')} {f.fix}")
        if verbose:
            print(o.c("dim", f"       source: {f.source}"))
    c = res.counts()
    print()
    print(f"{o.c('break', str(c['break']) + ' break')}, {o.c('risk', str(c['risk']) + ' risk')}, "
          f"{o.c('info', str(c['info']) + ' info')}"
          + o.c("dim", "   quick way out: latestshift pin --write  (ubuntu-latest -> ubuntu-24.04)"))
    if verbose:
        for n in res.notes:
            print(o.c("dim", "note: " + n))


def scan_markdown(res, shown) -> str:
    c = res.counts()
    lines = ["## latestshift: ubuntu-latest -> Ubuntu 26.04", "",
             f"{len(res.moving)} job(s) on `ubuntu-latest`; **{c['break']} break**, {c['risk']} risk, {c['info']} info.", ""]
    if shown:
        lines += ["| | where | job | what | fix |", "|---|---|---|---|---|"]
        for f in shown:
            esc = lambda s: s.replace("|", "\\|")
            lines.append(f"| {f.severity} | `{f.file}:{f.line}` | {esc(f.job)} | {esc(f.message)} ([source]({f.source})) | {esc(f.fix)} |")
    else:
        lines.append("No known 26.04 breakages found.")
    return "\n".join(lines) + "\n"


def cmd_scan(a):
    res = scan(a.path, include_pinned=a.include_26)
    shown = filter_findings(res.findings, a.min_severity)
    if a.only:
        keep = set(a.only.split(","))
        shown = [f for f in shown if f.rule in keep]
    if a.format == "json":
        d = res.to_dict()
        d["findings"] = [f.to_dict() for f in shown]
        print(json.dumps(d, indent=2))
    elif a.format == "markdown":
        sys.stdout.write(scan_markdown(res, shown))
    elif a.format == "github":
        for f in shown:
            level = {"break": "error", "risk": "warning", "info": "notice"}[f.severity]
            msg = f"{f.message}. Fix: {f.fix}".replace("%", "%25").replace("\n", " ")
            print(f"::{level} file=.github/workflows/{os.path.basename(f.file)},line={f.line},"
                  f"title=latestshift {f.rule} (job {f.job})::{msg}")
        c = res.counts()
        print(f"latestshift: {len(res.moving)} job(s) on ubuntu-latest, {c['break']} break, {c['risk']} risk, {c['info']} info")
    else:
        print_scan_text(res, shown, Out(a.color), verbose=a.verbose)
    if a.summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(scan_markdown(res, shown))
    if res.errors and not res.jobs:
        return 2
    if a.fail_on == "none":
        return 0
    return 1 if any(SEV_ORDER[f.severity] >= SEV_ORDER[a.fail_on] for f in shown) else 0


def cmd_pin(a):
    from .pin import pin, unified, write
    changes = pin(a.path, a.to, a.file or None)
    o = Out(a.color)
    if not changes:
        print(f"nothing to change: no ubuntu-latest in workflow files under {a.path}")
        return 0
    diff = unified(changes)
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            print(o.c("ok", line))
        elif line.startswith("-") and not line.startswith("---"):
            print(o.c("break", line))
        else:
            print(o.c("dim", line) if line.startswith("@@") else line)
    if a.write:
        write(changes)
        print(o.c("bold", f"wrote {len(changes)} file(s): ubuntu-latest -> {a.to}"))
    else:
        print(o.c("dim", f"dry run: {len(changes)} file(s) would change; add --write to apply"))
    return 0


def cmd_runs(a):
    from .runs import ApiError, CassetteClient, Client, collect, token
    o = Out(a.color)
    cassette = os.environ.get("LATESTSHIFT_CASSETTE")
    tok = None if cassette else token()
    if not cassette and not tok:
        print("latestshift runs needs a GitHub token (GitHub only serves job logs to signed-in requests): "
              "set GH_TOKEN/GITHUB_TOKEN or run `gh auth login`", file=sys.stderr)
        return 2
    client = CassetteClient(cassette) if cassette else Client(tok)
    try:
        groups, nruns, nlogs = collect(client, a.repo, a.limit, a.branch, a.workflow, a.max_logs, a.all_labels)
    except ApiError as e:
        print(f"latestshift: {e}", file=sys.stderr)
        return 2
    if a.format == "json":
        print(json.dumps([{"workflow": g.workflow, "job": g.name, "verdict": g.verdict(),
                           "runs": [r.__dict__ for r in g.runs]} for g in groups], indent=2))
        return 1 if any(g.verdict() == "FAILS ONLY ON 26.04" for g in groups) and a.fail_on_suspect else 0
    print(o.c("bold", f"latestshift runs {a.repo}: {nruns} recent run(s), {nlogs} job log(s) read"))
    if not groups:
        print("  no completed ubuntu-latest jobs in these runs")
        return 0
    width = min(48, max(len(f"{g.workflow} / {g.name}") for g in groups))
    for g in groups:
        t = g.tally()
        parts = []
        for img in ("24.04", "26.04", "unknown"):
            p, f = t.get((img, "pass"), 0), t.get((img, "fail"), 0)
            if p or f:
                txt = f"{img}: {p} pass {f} fail"
                parts.append(o.c("break", txt) if f and img == "26.04" else txt)
        v = g.verdict()
        vk = "break" if v.startswith(("FAILS ONLY", "fails more")) else "risk" if v.startswith("fails") else "ok" if v.startswith("ok") else "dim"
        label = f"{g.workflow} / {g.name}"
        print(f"  {label[:width]:<{width}}  {'  '.join(parts):<44} {o.c(vk, v)}")
        if v.startswith(("FAILS ONLY", "fails more")) and a.verbose is False:
            bad = next((r for r in g.runs if r.image == "ubuntu-26.04" and r.conclusion != "success"), None)
            good = next((r for r in g.runs if r.image == "ubuntu-24.04" and r.conclusion == "success"), None)
            if bad:
                print(o.c("dim", f"      red on 26.04 ({bad.image_version}): {bad.url}"))
            if good:
                print(o.c("dim", f"      green on 24.04 ({good.image_version}): {good.url}"))
            for rid in sorted({r.run_id for r in g.runs}):
                att = [r for r in g.runs if r.run_id == rid]
                if any(r.image == "ubuntu-26.04" and r.conclusion != "success" for r in att) and \
                        any(r.image == "ubuntu-24.04" and r.conclusion == "success" for r in att):
                    print(o.c("risk", f"      run {rid}: failed on 26.04, re-run passed on 24.04 (the re-run proves nothing)"))
        if a.verbose:
            for r in g.runs:
                print(o.c("dim", f"      {r.started[:16]} attempt {r.attempt} {r.conclusion:<8} {r.image or '?'} {r.image_version or ''} {r.url}"))
    suspects = [g for g in groups if g.verdict() in ("FAILS ONLY ON 26.04", "fails more on 26.04")]
    if suspects:
        print(o.c("break", f"{len(suspects)} job(s) look broken by the image, not your code.") +
              o.c("dim", " Pin them (latestshift pin) or fix with latestshift scan."))
    return 1 if suspects and a.fail_on_suspect else 0


def cmd_rules(a):
    from . import rules as R
    import inspect
    print(f"{len(RULE_IDS)} rules (see README for sources):")
    for rid in RULE_IDS:
        print("  " + rid)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="latestshift",
                                description="Find what breaks when GitHub's ubuntu-latest label moves from Ubuntu 24.04 to 26.04.")
    p.add_argument("--version", action="version", version=f"latestshift {__version__}")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="check workflow files for 26.04 breakages (offline)")
    s.add_argument("path", nargs="?", default=".", help="repo root or a workflow file (default .)")
    s.add_argument("--format", choices=["text", "json", "markdown", "github"], default="text")
    s.add_argument("--min-severity", choices=["info", "risk", "break"], default="risk",
                   help="lowest severity to show (default risk)")
    s.add_argument("--fail-on", choices=["info", "risk", "break", "none"], default="break",
                   help="exit 1 when a shown finding is at least this severe (default break)")
    s.add_argument("--only", help="comma-separated rule ids")
    s.add_argument("--include-26", action="store_true", help="also check jobs already on ubuntu-26.04")
    s.add_argument("--summary", action="store_true", help="append a markdown table to $GITHUB_STEP_SUMMARY")
    s.add_argument("-v", "--verbose", action="store_true", help="show sources and notes")
    s.set_defaults(func=cmd_scan)

    pn = sub.add_parser("pin", help="rewrite ubuntu-latest to an explicit label (dry run unless --write)")
    pn.add_argument("path", nargs="?", default=".")
    pn.add_argument("--to", default="ubuntu-24.04", help="label to pin to (default ubuntu-24.04)")
    pn.add_argument("--file", action="append", help="only this workflow file (repeatable)")
    pn.add_argument("--write", action="store_true")
    pn.set_defaults(func=cmd_pin)

    r = sub.add_parser("runs", help="which image did recent ubuntu-latest jobs get, and do they fail only on 26.04?")
    r.add_argument("repo", help="OWNER/REPO")
    r.add_argument("--limit", type=int, default=30, help="recent completed runs to read (default 30)")
    r.add_argument("--max-logs", type=int, default=80, help="cap on job logs fetched (default 80)")
    r.add_argument("--branch")
    r.add_argument("--workflow", help="workflow file name or id, e.g. ci.yml")
    r.add_argument("--all-labels", action="store_true", help="include jobs on explicit ubuntu-24.04 / 26.04 labels too")
    r.add_argument("--fail-on-suspect", action="store_true", help="exit 1 if a job fails only on 26.04")
    r.add_argument("--format", choices=["text", "json"], default="text")
    r.add_argument("-v", "--verbose", action="store_true", help="list every job run")
    r.set_defaults(func=cmd_runs)

    ru = sub.add_parser("rules", help="list rule ids")
    ru.set_defaults(func=cmd_rules)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 2
    try:
        return a.func(a)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
