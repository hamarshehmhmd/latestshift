import json
import os

from latestshift.cli import main

HERE = os.path.dirname(__file__)
CASSETTE = os.path.join(HERE, "fixtures", "cassette.json")
EXAMPLE = os.path.join(HERE, "..", "examples", "webapp")


def test_scan_exit_codes(repo, capsys):
    bad = repo("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: sudo -E make\n")
    assert main(["scan", bad]) == 1
    assert main(["scan", bad, "--fail-on", "none"]) == 0
    out = capsys.readouterr().out
    assert "BREAK" in out and "sudo-E" in out


def test_scan_clean_exit_zero(repo, capsys):
    ok = repo("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make\n")
    assert main(["scan", ok]) == 0
    assert "no known 26.04 breakages" in capsys.readouterr().out


def test_scan_risk_fail_on(repo, capsys):
    r = repo("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: podman run --rm alpine true\n")
    assert main(["scan", r]) == 0
    assert main(["scan", r, "--fail-on", "risk"]) == 1


def test_scan_json_and_only(capsys):
    assert main(["scan", EXAMPLE, "--format", "json", "--only", "sudo-E"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in d["findings"]] == ["sudo-E"]
    assert d["counts"]["break"] >= 5


def test_scan_github_and_summary(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["scan", EXAMPLE, "--format", "github", "--summary"])
    out = capsys.readouterr().out
    assert "::error file=.github/workflows/ci.yml,line=" in out
    assert "| break |" in summary.read_text()


def test_pin_dry_run_and_write(repo, capsys):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest  # moving
            strategy:
              matrix:
                os: [ubuntu-latest, windows-latest]
            if: matrix.os == 'ubuntu-latest'
            steps:
              - run: echo "# ubuntu-latest in a comment stays"
    """)
    path = os.path.join(root, ".github", "workflows", "ci.yml")
    before = open(path).read()
    assert main(["pin", root]) == 0
    assert open(path).read() == before
    assert "+    runs-on: ubuntu-24.04  # moving" in capsys.readouterr().out
    assert main(["pin", root, "--write"]) == 0
    after = open(path).read()
    assert "runs-on: ubuntu-24.04  # moving" in after
    assert "os: [ubuntu-24.04, windows-latest]" in after
    assert "matrix.os == 'ubuntu-24.04'" in after
    assert "ubuntu-latest" not in after.split("\n")[0]
    assert main(["pin", root]) == 0
    assert "nothing to change" in capsys.readouterr().out


def test_pin_to_2604(repo):
    root = repo("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{run: make}]\n")
    main(["pin", root, "--to", "ubuntu-26.04", "--write"])
    assert "ubuntu-26.04" in open(os.path.join(root, ".github", "workflows", "ci.yml")).read()


def test_runs_from_cassette(monkeypatch, capsys):
    monkeypatch.setenv("LATESTSHIFT_CASSETTE", CASSETTE)
    assert main(["--color", "never", "runs", "acme/shop"]) == 0
    out = capsys.readouterr().out
    lines = {l.split()[2]: l for l in out.splitlines() if l.startswith("  CI / ")}
    assert "FAILS ONLY ON 26.04" in lines["build"]
    assert "ok on 26.04" in lines["lint"]
    assert "not on 26.04 yet" in lines["docs"]
    assert "fails on both" in lines["e2e"]
    assert "run 103: failed on 26.04, re-run passed on 24.04" in out
    assert "release" not in out  # pinned job ignored by default
    assert main(["--color", "never", "runs", "acme/shop", "--fail-on-suspect"]) == 1


def test_runs_all_labels_and_json(monkeypatch, capsys):
    monkeypatch.setenv("LATESTSHIFT_CASSETTE", CASSETTE)
    main(["runs", "acme/shop", "--all-labels", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    names = {g["job"]: g for g in d}
    assert "release" in names
    assert names["build"]["verdict"] == "FAILS ONLY ON 26.04"
    assert {r["image"] for r in names["build"]["runs"]} == {"ubuntu-24.04", "ubuntu-26.04"}


def test_runs_needs_token(monkeypatch, capsys):
    import latestshift.runs as R
    monkeypatch.delenv("LATESTSHIFT_CASSETTE", raising=False)
    monkeypatch.setattr(R, "token", lambda: None)
    assert main(["runs", "a/b"]) == 2
    assert "needs a GitHub token" in capsys.readouterr().err


def test_parse_image_variants():
    from latestshift.runs import parse_image
    ts = "2026-11-09T13:47:52.9Z "
    log = "".join(ts + l + "\n" for l in ["##[group]Runner Image", "Image: ubuntu-26.04", "Version: 20261101.186.1"])
    assert parse_image(log) == ("ubuntu-26.04", "20261101.186.1")
    only_os = "".join(ts + l + "\n" for l in ["##[group]Operating System", "Ubuntu", "24.04.5", "LTS"])
    assert parse_image(only_os) == ("ubuntu-24.04", None)
    assert parse_image("nothing here") == (None, None)


def test_version_and_help(capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "latestshift 0.1.0" in capsys.readouterr().out
    assert main([]) == 2


def test_client_retries_5xx(monkeypatch):
    import io
    import urllib.error
    import latestshift.runs as R
    calls = []

    def fake_urlopen(req, timeout=30):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    assert R.Client("t").get_json("/x") == {"ok": True}
    assert len(calls) == 2
