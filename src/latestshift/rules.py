"""Rules: known ways a job breaks when ubuntu-latest moves from Ubuntu 24.04 to Ubuntu 26.04.

Every rule cites where the breakage was reported. Severity:
  break  the documented failure happens on 26.04 as written
  risk   it breaks under conditions latestshift cannot see from the files alone
  info   behaviour changes worth knowing, rarely a hard failure
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict

from .workflow import JobVariant, scalar

ISSUE = "https://github.com/actions/runner-images/issues/"
MIGRATION = ISSUE + "14748"
LESSTEP = "https://lesstep.com/github-actions/ubuntu-latest-26-04/"
README_2604 = "https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md"
PY_MANIFEST = "https://github.com/actions/python-versions/blob/main/versions-manifest.json"

# actions/python-versions builds for linux 26.04 x64 (versions-manifest.json, 2026-11-09)
PY_2604 = {
    "3.10": ["3.10.20", "3.10.21", "3.10.22"],
    "3.11": ["3.11.15", "3.11.16", "3.11.17", "3.11.18"],
    "3.12": ["3.12.13", "3.12.14", "3.12.15", "3.12.16"],
    "3.13": ["3.13.13", "3.13.14", "3.13.15", "3.13.16", "3.13.17", "3.13.18"],
    "3.14": ["3.14.6", "3.14.7", "3.14.8", "3.14.9", "3.14.10"],
    "3.15": ["3.15.0", "3.15.1", "3.15.2"],
}
# ruby/setup-ruby builds for ubuntu-26.04, as reported 2026-11 (lesstep notes)
RUBY_2604_MIN = {"3.4": 9, "3.3": 11, "3.2": 11, "4.0": 5}
RUBY_2604_LAST = {"2.4": "2.4.10", "2.5": "2.5.9", "2.6": "2.6.10", "2.7": "2.7.8", "3.0": "3.0.7", "3.1": "3.1.7"}
GONE_APT = {
    "libpcre3": "PCRE1 is gone on 26.04 (only PCRE2)",
    "libpcre3-dev": "PCRE1 is gone on 26.04 (only PCRE2); port to libpcre2-dev",
    "qemu-user-static": "renamed on 26.04; use qemu-user-binfmt / qemu-user",
    "gcc-10": "not packaged for 26.04; images ship GCC 13, 14 and 15",
    "g++-10": "not packaged for 26.04; images ship GCC 13, 14 and 15",
    "gcc-9": "not packaged for 26.04; images ship GCC 13, 14 and 15",
    "g++-9": "not packaged for 26.04; images ship GCC 13, 14 and 15",
    "clang-format-14": "not packaged for 26.04; images ship clang-format 20, 21 and 22",
    "libicu74": "26.04 has ICU 78 (libicu78)",
}
SEV_ORDER = {"info": 0, "risk": 1, "break": 2}


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    job: str
    message: str
    fix: str
    source: str
    combo: str = ""

    def key(self):
        return (self.rule, self.file, self.line, self.job, self.message)

    def to_dict(self):
        return asdict(self)


class Repo:
    """Lazy access to files in the scanned repository."""

    def __init__(self, root: str):
        self.root = root if os.path.isdir(root) else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(root))))
        self._cache = {}

    def read(self, rel: str):
        if rel not in self._cache:
            p = os.path.join(self.root, rel)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    self._cache[rel] = fh.read()
            except OSError:
                self._cache[rel] = None
        return self._cache[rel]

    def package_versions(self) -> dict:
        txt = self.read("package.json")
        if not txt:
            return {}
        try:
            d = json.loads(txt)
        except ValueError:
            return {}
        out = {}
        for sec in ("dependencies", "devDependencies", "optionalDependencies"):
            for k, v in (d.get(sec) or {}).items():
                if isinstance(v, str):
                    out[k] = v
        return out

    def gradle_version(self):
        txt = self.read("gradle/wrapper/gradle-wrapper.properties") or self.read(
            "android/gradle/wrapper/gradle-wrapper.properties")
        if not txt:
            return None
        m = re.search(r"gradle-(\d+(?:\.\d+)*)-(?:all|bin)\.zip", txt)
        return m.group(1) if m else None


def vtuple(v: str):
    nums = re.findall(r"\d+", v)
    return tuple(int(x) for x in nums[:3])


def semver_floor(spec: str):
    """Lowest version an npm range like ^1.48.0 / ~1.2 / 1.50.1 could resolve to (None if unknown)."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", spec or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def uses_of(step) -> tuple[str, str]:
    u = scalar(step.get("uses")) if isinstance(step, dict) else ""
    if "@" in u:
        name, ref = u.split("@", 1)
    else:
        name, ref = u, ""
    return name.lower(), ref


def with_of(step) -> dict:
    w = step.get("with") if isinstance(step, dict) else None
    return w if isinstance(w, dict) else {}


def run_text(step) -> str:
    return scalar(step.get("run")) if isinstance(step, dict) and step.get("run") is not None else ""


def step_line(step, offset_pattern=None) -> int:
    base = step.get("__line__", 0) if isinstance(step, dict) else 0
    if offset_pattern is not None:
        r = step.get("run")
        rl = getattr(r, "line", 0)
        text = run_text(step)
        m = re.search(offset_pattern, text, re.M)
        if m and rl:
            # block scalars (| or >) start on the line after `run:`
            raw_line = rl + (1 if getattr(r, "style", "") in ("|", ">") else 0) + text[: m.start()].count("\n")
            return raw_line
    return base


def input_line(step, key) -> int:
    w = step.get("with") if isinstance(step, dict) else None
    if isinstance(w, dict) and hasattr(w, "lines") and key in w.lines:
        return w.lines[key]
    return step_line(step)


def split_versions(v) -> list[str]:
    if isinstance(v, list):
        return [scalar(x).strip() for x in v if scalar(x).strip()]
    s = scalar(v)
    return [x.strip() for x in re.split(r"[\n,]", s) if x.strip()]


# ---------------------------------------------------------------- rules

def r_python(j: JobVariant, repo: Repo):
    for st in j.steps:
        name, _ = uses_of(st)
        if name != "actions/setup-python":
            continue
        w = with_of(st)
        specs = split_versions(w.get("python-version")) if "python-version" in w else []
        origin = "python-version"
        if not specs:
            f = scalar(w.get("python-version-file")) or ".python-version"
            txt = repo.read(f)
            if txt:
                specs = [l.strip() for l in txt.splitlines() if l.strip() and not l.startswith("#")][:1]
                origin = f
        for spec in specs:
            if "${{" in spec or spec.startswith(("pypy", "graalpy")):
                continue
            s = spec.lstrip("v")
            m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(x|\d+))?(?:-dev)?", s)
            if not m:
                continue
            minor = f"{m.group(1)}.{m.group(2)}"
            patch = m.group(3)
            line = input_line(st, "python-version") if origin == "python-version" else step_line(st)
            if (int(m.group(1)), int(m.group(2))) < (3, 10):
                yield ("py-old", "break", line,
                       f"setup-python {origin} {spec}: no Python {minor} build exists for Ubuntu 26.04 "
                       f"(\"The version '{spec}' with architecture 'x64' was not found for Ubuntu 26.04\")",
                       f"run this version on ubuntu-24.04 (matrix include with os: ubuntu-24.04), or drop {minor}",
                       PY_MANIFEST)
            elif patch and patch != "x" and minor in PY_2604 and s not in PY_2604[minor]:
                avail = PY_2604[minor]
                yield ("py-patch", "break", line,
                       f"setup-python {origin} {spec}: that exact patch is not built for Ubuntu 26.04 "
                       f"(26.04 has {avail[0]} to {avail[-1]})",
                       f"pin the minor (\"{minor}\") or a patch >= {avail[0]}", PY_MANIFEST)


def r_java(j: JobVariant, repo: Repo):
    has_setup_java = any(uses_of(s)[0] == "actions/setup-java" for s in j.steps)
    for st in j.steps:
        t = run_text(st)
        if "JAVA_HOME_8_X64" in t or "JAVA_HOME_8_X64" in json.dumps(st.get("env") or {}):
            yield ("java8-home", "break", step_line(st, r"JAVA_HOME_8_X64"),
                   "JAVA_HOME_8_X64 is not set on 26.04 (JDK 8 is not installed; 11, 17, 21, 25 are)",
                   "use actions/setup-java with java-version: '8'", README_2604)
    if has_setup_java:
        return
    for st in j.steps:
        t = run_text(st)
        m = re.search(r"(\./gradlew|\bgradle\s|flutter build apk)", t)
        if not m:
            continue
        gv = repo.gradle_version()
        if gv and vtuple(gv) >= (9, 1):
            continue
        what = f"Gradle wrapper {gv}" if gv else "Gradle (wrapper version unknown)"
        yield ("java25-gradle", "break" if gv else "risk", step_line(st, re.escape(m.group(1))),
               f"default JDK is 25 on 26.04 (17 on 24.04); {what} cannot run on Java 25 before 9.1 "
               "(\"Unsupported class file major version 69\")",
               "add actions/setup-java with java-version: '17' (or 21), or move the wrapper to Gradle >= 9.1",
               README_2604)
        return


def r_sudo_e(j, repo):
    for st in j.steps:
        t = run_text(st)
        m = re.search(r"\bsudo\s+(?:-[A-Za-z]*E[A-Za-z]*|--preserve-env)(?=\s|$)", t)
        if m:
            yield ("sudo-E", "break", step_line(st, re.escape(m.group(0))),
                   "`sudo -E` / bare --preserve-env: sudo on 26.04 is sudo-rs, which drops your environment "
                   "(\"preserving the entire environment is not supported, '-E' is ignored\")",
                   "name the variables: sudo --preserve-env=PATH,FOO cmd (or pass them as VAR=value after sudo)",
                   LESSTEP)


def r_conda(j, repo):
    if any(uses_of(s)[0] in ("conda-incubator/setup-miniconda", "mamba-org/setup-micromamba",
                             "mamba-org/provision-with-micromamba") for s in j.steps):
        return
    for st in j.steps:
        t = run_text(st)
        m = re.search(r"\$\{?CONDA\}?\b|(?:^|[\s;&|])conda\s", t, re.M)
        if m:
            yield ("conda", "break", step_line(st, re.escape(m.group(0).strip()) if m.group(0).strip() else None),
                   "the 26.04 image has no Miniconda and $CONDA is empty (\"conda: command not found\")",
                   "add conda-incubator/setup-miniconda (or setup-micromamba) before this step", README_2604)
            return


def r_apt_sources(j, repo):
    for st in j.steps:
        t = run_text(st)
        m = re.search(r"ppa:ondrej/php", t)
        if m:
            yield ("ppa-ondrej", "break", step_line(st, re.escape(m.group(0))),
                   "the ondrej/php PPA publishes nothing for 26.04 (resolute): apt-get update fails with "
                   "\"does not have a Release file\"", "use shivammathur/setup-php, or stay on ubuntu-24.04", LESSTEP)
        if "apt.llvm.org" in t:
            vm = re.search(r"llvm\.sh\S*\s+(?:[^\n]*?\s)?(\d{2})\b|llvm-toolchain-[a-z]+-(\d{2})|clang-(\d{2})\b", t)
            ver = next((g for g in (vm.groups() if vm else ()) if g), None)
            if ver and 17 <= int(ver) <= 20:
                yield ("llvm-apt", "break", step_line(st, r"apt\.llvm\.org"),
                       f"apt.llvm.org has no clang {ver} packages for 26.04 (resolute): apt-get update fails with "
                       "\"does not have a Release file\"", "use the image's clang 20, 21 or 22, or keep this job on ubuntu-24.04",
                       LESSTEP)
            else:
                yield ("llvm-apt", "risk", step_line(st, r"apt\.llvm\.org"),
                       "apt.llvm.org publishes nothing for 26.04 for clang 17 to 20; check the version this adds",
                       "use the image's clang 20, 21 or 22 where possible", LESSTEP)


APT_INSTALL = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*install\b((?:\\\n|[^\n;&|])*)")


def r_apt_packages(j, repo):
    for st in j.steps:
        t = run_text(st)
        for m in APT_INSTALL.finditer(t):
            args = m.group(1).replace("\\\n", " ").split()
            for a in args:
                if a.startswith("linux-modules-extra"):
                    yield ("apt-modules-extra", "break", step_line(st, re.escape(a)),
                           "no linux-modules-extra package for the 7.0 azure kernel; the modules are in linux-modules",
                           "skip this install on 7.0 kernels and just modprobe", LESSTEP)
                    continue
                if a.startswith("-") or "$" in a:
                    continue
                pkg = a.split("=")[0]
                if pkg in GONE_APT:
                    yield ("apt-gone", "break", step_line(st, re.escape(a)),
                           f"apt package {pkg}: {GONE_APT[pkg]} (\"Unable to locate package\" / \"has no installation candidate\")",
                           "use the 26.04 name, or keep this job on ubuntu-24.04", LESSTEP)


def r_bwrap(j, repo):
    for st in j.steps:
        m = re.search(r"\bbwrap\b", run_text(st))
        if m:
            yield ("bwrap", "break", step_line(st, r"\bbwrap\b"),
                   "AppArmor's bwrap-userns-restrict profile is loaded on 26.04: \"No permissions to create a new namespace\"",
                   "load an AppArmor profile for your bwrap binary, or run this job on ubuntu-24.04", LESSTEP)


def r_golangci(j, repo):
    go_pinned_old = False
    for st in j.steps:
        if uses_of(st)[0] == "actions/setup-go":
            gv = scalar(with_of(st).get("go-version"))
            if gv and not gv.startswith(("1.26", "stable", "${{")) and gv not in ("", "oldstable"):
                go_pinned_old = True
    if go_pinned_old:
        return
    for st in j.steps:
        name, ref = uses_of(st)
        ver = ""
        if name == "golangci/golangci-lint-action":
            ver = scalar(with_of(st).get("version"))
        else:
            m = re.search(r"golangci-lint(?:/cmd/golangci-lint)?@(v1\.[\d.]+)|install\.sh[^\n]*\s(v1\.[\d.]+)", run_text(st))
            if m:
                ver = m.group(1) or m.group(2)
        if ver.startswith("v1.") or ver.startswith("1."):
            yield ("golangci-v1", "break", input_line(st, "version") if name == "golangci/golangci-lint-action" else step_line(st, r"golangci-lint"),
                   f"golangci-lint {ver} (built with Go 1.24) grows without limit under 26.04's default Go 1.26 "
                   "until \"The runner has received a shutdown signal\"",
                   "pin go-version: '1.24' in this job, or move to golangci-lint v2", ISSUE + "15179")


def r_podman(j, repo):
    for st in j.steps:
        if re.search(r"\bpodman\b", run_text(st)):
            yield ("podman-pasta", "risk", step_line(st, r"\bpodman\b"),
                   "rootless Podman: pasta's AppArmor profile on 26.04 blocks stopping it (`podman unshare --rootless-netns` "
                   "exits 126; `podman run --rm` on a bridge network logs an error but exits 0)",
                   "see runner-images #15182 for the two-line AppArmor workaround, or use slirp4netns", ISSUE + "15182")
            return


def r_services(j, repo):
    services = j.job.get("services") if isinstance(j.job.get("services"), dict) else {}
    pg_old = False
    for sname, svc in services.items():
        image = scalar(svc.get("image") if isinstance(svc, dict) else svc)
        line = services.line_of(sname) if hasattr(services, "line_of") else j.line
        if re.match(r"(?:docker\.io/)?(?:library/)?mongo:8\.(?:0|3)\b", image):
            yield ("mongo-kernel7", "risk", line,
                   f"service {image}: mongod 8.0/8.3 refuse to start on kernel 7.0.0-*-azure (26.04, and current 24.04 images)",
                   "use mongo:9.0, or run this job on ubuntu-22.04", LESSTEP)
        m = re.match(r"(?:docker\.io/)?(?:library/)?postgres:(\d+)", image)
        if m and int(m.group(1)) < 18:
            pg_old = True
    if pg_old:
        for st in j.steps:
            if re.search(r"\bpg_dump\b", run_text(st)):
                yield ("pg-dump-18", "risk", step_line(st, r"\bpg_dump\b"),
                       "the image's pg_dump is 18 on 26.04; a dump made with it writes `SET transaction_timeout` and "
                       "no longer loads into an older PostgreSQL service", "run pg_dump inside the service container "
                       "(docker exec), or install the matching client version", LESSTEP)


def r_actions(j, repo):
    for st in j.steps:
        name, ref = uses_of(st)
        w = with_of(st)
        if name == "pycqa/bandit-action" and ref.startswith("v1"):
            yield ("bandit-action", "break", step_line(st),
                   "PyCQA/bandit-action@v1 asks setup-python for 3.8/3.9, which 26.04 does not have",
                   "run bandit in your own step after setup-python, or pin this job to ubuntu-24.04", LESSTEP)
        elif name == "swift-actions/setup-swift" and re.match(r"v?2(\.|$)", ref):
            yield ("setup-swift-v2", "break", step_line(st),
                   "swift-actions/setup-swift v2 does not accept Ubuntu 26.04",
                   "use the image's Swift (6.4.x), or run old Swift in the swift container", LESSTEP)
        elif name == "erlef/setup-beam":
            otp = scalar(w.get("otp-version"))
            m = re.match(r"v?1\.(\d+)\.(\d+)$", ref)
            if m and (int(m.group(1)), int(m.group(2))) <= (24, 0):
                yield ("setup-beam-old", "break", step_line(st),
                       f"erlef/setup-beam@{ref} fails on every version on 26.04 (\"got ubuntu26\")",
                       "update to a setup-beam release newer than v1.24.0", LESSTEP)
            if re.match(r"^(?:OTP-)?2[45](?:\.|$)", otp):
                yield ("otp-old", "break", input_line(st, "otp-version"),
                       f"otp-version {otp}: setup-beam's 26.04 builds start at OTP 26.0",
                       "keep OTP 24/25 jobs on ubuntu-24.04", LESSTEP)
        elif name == "ruby/setup-ruby":
            rv = scalar(w.get("ruby-version"))
            origin = "ruby-version"
            if not rv or rv == "default":
                txt = repo.read(".ruby-version")
                if txt:
                    rv, origin = txt.strip().split()[0], ".ruby-version"
            rv = rv.replace("ruby-", "")
            m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", rv)
            if m:
                minor = f"{m.group(1)}.{m.group(2)}"
                patch = int(m.group(3))
                bad = False
                if minor in RUBY_2604_MIN and patch < RUBY_2604_MIN[minor]:
                    bad = True
                if minor in RUBY_2604_LAST and rv != RUBY_2604_LAST[minor]:
                    bad = True
                if bad:
                    yield ("ruby-patch", "break", input_line(st, "ruby-version"),
                           f"{origin} {rv}: setup-ruby has no build of that patch for 26.04 "
                           f"(\"Unavailable version {rv} for ruby on ubuntu-26.04\")",
                           f"use \"{minor}\" or a newer patch", LESSTEP)
        elif name == "julia-actions/setup-julia":
            v = scalar(w.get("version"))
            m = re.fullmatch(r"1\.(9|10)(?:\.(\d+))?", v)
            if m and (m.group(1) == "9" or (m.group(2) is not None and int(m.group(2)) <= 8)):
                yield ("julia-execstack", "break", input_line(st, "version"),
                       f"Julia {v} does not start with glibc 2.43 (dlopen refuses executable stacks)",
                       "use Julia >= 1.10.9", LESSTEP)
        elif name == "crystal-lang/install-crystal":
            m = re.match(r"v?1\.(\d+)\.(\d+)$", ref)
            if m and (int(m.group(1)), int(m.group(2))) < (9, 3):
                yield ("crystal-pcre", "break", step_line(st),
                       f"install-crystal@{ref} needs PCRE1, which 26.04 does not have", "use install-crystal >= v1.9.3", LESSTEP)
        elif name in ("jlumbroso/free-disk-space", "lumbroso-commons/free-disk-space"):
            if scalar(w.get("large-packages")).lower() in ("true", "") and "large-packages" in w:
                yield ("free-disk-space", "risk", step_line(st),
                       "free-disk-space large-packages: its last apt command names powershell, not an apt package "
                       "on 26.04, so Azure CLI, Chrome and Firefox are not removed", "check the freed space on 26.04", LESSTEP)
        elif name == "actions/cache":
            path = scalar(w.get("path"))
            key = scalar(w.get("key"))
            if re.search(r"ms-playwright|(?:^|/)\.?venv\b", path) and not re.search(r"ImageOS|ImageVersion|runner\.os.*matrix|steps\.", key):
                yield ("cache-image-key", "risk", step_line(st),
                       f"cache of {path.strip().splitlines()[0]} with a key that does not name the image: a cache saved on "
                       "24.04 restored on 26.04 fails with \"libicudata.so.74\" / \"libxml2.so.2\" or a missing Python",
                       "add ${{ env.ImageOS }} (and ideally ${{ env.ImageVersion }}) to the key", LESSTEP)


def r_node_pkgs(j, repo):
    pk = repo.package_versions()
    if not pk:
        return
    for st in j.steps:
        t = run_text(st)
        if re.search(r"playwright\s+install", t):
            for dep in ("@playwright/test", "playwright"):
                fl = semver_floor(pk.get(dep, ""))
                if fl and fl < (1, 61, 0):
                    yield ("playwright-old", "break", step_line(st, r"playwright\s+install"),
                           f"{dep} {pk[dep]} (package.json): Playwright before 1.61 cannot install browsers on 26.04 "
                           "(\"does not support chromium on ubuntu26.04-x64\")", "update to @playwright/test >= 1.61",
                           LESSTEP)
                    break
        if re.search(r"\bwdio\b|webdriverio", t):
            for dep in ("@wdio/cli", "webdriverio"):
                fl = semver_floor(pk.get(dep, ""))
                if fl and fl[0] == 9 and fl < (9, 33, 1):
                    yield ("wdio-xvfb", "break", step_line(st, r"\bwdio\b|webdriverio"),
                           f"{dep} {pk[dep]}: WebdriverIO 9 workers die with \"Error: write EINVAL\" under 26.04's xvfb-run",
                           "update to 9.33.1, or set autoXvfb: false", LESSTEP)
                    break


def r_misc_runs(j, repo):
    releases = any(uses_of(s)[0] in ("softprops/action-gh-release", "actions/upload-release-asset",
                                     "svenstaro/upload-release-action", "ncipollo/release-action") for s in j.steps)
    for st in j.steps:
        t = run_text(st)
        if releases:
            m = re.search(r"\bpyinstaller\b|\bnuitka\b|cargo\s+build[^\n]*--release|\bcmake\s+--build\b|\bmake\b", t, re.I)
            if m:
                yield ("glibc-floor", "risk", step_line(st, re.escape(m.group(0))),
                       "binaries built on 26.04 link against glibc 2.43 and fail on Ubuntu 24.04 / Debian 13 with "
                       "\"version `GLIBC_2.43' not found\"",
                       "build release binaries on ubuntu-24.04 (or older), or in a manylinux/old-glibc container", LESSTEP)
        m = re.search(r"\brpmbuild\b", t)
        if m:
            yield ("rpm6", "risk", step_line(st, r"\brpmbuild\b"),
                   "rpm 6 on 26.04: %install starts in a per-package build dir (relative cp fails) and v6 packages by default",
                   "use absolute %{buildroot} paths and --define '_rpmformat 4' if consumers need v4", LESSTEP)
        m = re.search(r"\bwg-quick\s+up\s+\S*[/.]\S*", t)
        if m:
            yield ("wireguard", "break", step_line(st, r"wg-quick\s+up"),
                   "26.04's AppArmor profile lets wg-quick read configs only from /etc/wireguard (Permission denied)",
                   "write the config to /etc/wireguard/wg0.conf and run wg-quick up wg0", LESSTEP)
        m = re.search(r"kind\s+load\s+docker-image", t)
        if m:
            yield ("kind-load", "risk", step_line(st, r"kind\s+load"),
                   "Docker 29 (containerd image store) on 26.04 breaks kind load docker-image for multi-platform images",
                   "build single-platform (--platform linux/amd64) or use kind load image-archive", LESSTEP)
        m = re.search(r"\bansible-test\b", t)
        if m:
            yield ("ansible-test", "risk", step_line(st, r"\bansible-test\b"),
                   "ansible-test 2.22/devel often stops before any test on kernel 7.0.0-*-azure (26.04 and current 24.04)",
                   "run these jobs on ubuntu-22.04 for now", LESSTEP)
        m = re.search(r"\bgcc\b(?![^\n]*-std=)", t)
        if m and not re.search(r"gcc-1[34]\b", t):
            yield ("gcc15", "info", step_line(st, r"\bgcc\b"),
                   "default GCC goes 13 -> 15 on 26.04: C is compiled as C23 (e.g. `f()` means no arguments)",
                   "pass -std=gnu17, or call gcc-13/gcc-14 (still installed)", LESSTEP)


RULES = [r_python, r_java, r_sudo_e, r_conda, r_apt_sources, r_apt_packages, r_bwrap, r_golangci,
         r_podman, r_services, r_actions, r_node_pkgs, r_misc_runs]

RULE_IDS = ["py-old", "py-patch", "java8-home", "java25-gradle", "sudo-E", "conda", "ppa-ondrej", "llvm-apt",
            "apt-gone", "apt-modules-extra", "bwrap", "golangci-v1", "podman-pasta", "mongo-kernel7", "pg-dump-18",
            "bandit-action", "setup-swift-v2", "setup-beam-old", "otp-old", "ruby-patch", "julia-execstack",
            "crystal-pcre", "free-disk-space", "cache-image-key", "playwright-old", "wdio-xvfb", "glibc-floor",
            "rpm6", "wireguard", "kind-load", "ansible-test", "gcc15"]


CONTAINER_SAFE = {"mongo-kernel7", "kind-load", "podman-pasta", "bwrap", "ansible-test"}


def check_job(j: JobVariant, repo: Repo) -> list[Finding]:
    out = []
    container = j.job.get("container")
    for rule in RULES:
        for rid, sev, line, msg, fix, src in rule(j, repo):
            if container and rid not in CONTAINER_SAFE:
                # steps run inside the job container: the host image's userland, Python builds etc. do not apply
                continue
            out.append(Finding(rid, sev, j.workflow, line, j.job_id, msg, fix, src, j.combo_label))
    return out
