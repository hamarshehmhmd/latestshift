from latestshift.scan import scan


def rules(res):
    return sorted(f.rule for f in res.findings)


def test_python_39_on_latest_breaks_but_pinned_include_is_fine(repo):
    root = repo("""
        jobs:
          test:
            runs-on: ${{ matrix.os }}
            strategy:
              matrix:
                os: [ubuntu-latest]
                python: ["3.9", "3.12"]
                include:
                  - os: ubuntu-24.04
                    python: "3.8"
            steps:
              - uses: actions/setup-python@v6
                with:
                  python-version: ${{ matrix.python }}
    """)
    res = scan(root)
    assert rules(res) == ["py-old"]
    f = res.findings[0]
    assert "3.9" in f.message and f.line == 15 and "python=3.9" in f.combo


def test_python_exact_patch(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12.4"
          b:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12.15"
    """)
    res = scan(root)
    assert [(f.rule, f.job) for f in res.findings] == [("py-patch", "a")]
    assert "3.12.13" in res.findings[0].message


def test_python_version_file(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/setup-python@v5
                with:
                  python-version-file: .python-version
    """, files={".python-version": "3.9.18\n"})
    assert rules(scan(root)) == ["py-old"]


def test_pinned_jobs_are_not_checked(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-24.04
            steps:
              - run: sudo -E make install
    """)
    res = scan(root)
    assert res.findings == [] and res.jobs[0].status == "pinned"
    assert rules(scan(root, include_pinned=False)) == []


def test_include_26_checks_explicit_26(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-26.04
            steps:
              - run: sudo -E make install
    """)
    assert rules(scan(root)) == []
    assert rules(scan(root, include_pinned=True)) == ["sudo-E"]


def test_sudo_variants(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: |
                  echo one
                  sudo -E ./install.sh
              - run: sudo --preserve-env=PATH make
              - run: sudo -HE pip install .
    """)
    res = scan(root)
    assert [(f.rule, f.line) for f in res.findings] == [("sudo-E", 8), ("sudo-E", 10)]


def test_gradle_old_wrapper_without_setup_java(repo):
    wf = """
        jobs:
          android:
            runs-on: ubuntu-latest
            steps:
              - run: ./gradlew build
    """
    old = repo(wf, files={"gradle/wrapper/gradle-wrapper.properties": "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.2-bin.zip\n"})
    assert rules(scan(old)) == ["java25-gradle"]


def test_gradle_ok_with_setup_java_or_new_wrapper(repo, tmp_path):
    root = repo("""
        jobs:
          android:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/setup-java@v5
                with: {distribution: temurin, java-version: '17'}
              - run: ./gradlew build
    """)
    assert rules(scan(root)) == []
    (tmp_path / "gradle" / "wrapper").mkdir(parents=True)
    (tmp_path / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text("distributionUrl=gradle-9.2.0-bin.zip\n")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: ./gradlew build\n")
    assert rules(scan(str(tmp_path))) == []


def test_java8_home(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: echo "JAVA_HOME=$JAVA_HOME_8_X64" >> $GITHUB_ENV
    """)
    assert rules(scan(root)) == ["java8-home"]


def test_apt_gone_packages_each_reported(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: |
                  sudo apt-get update
                  sudo apt-get install -y --no-install-recommends \\
                    libpcre3-dev qemu-user-static curl
              - run: sudo apt install linux-modules-extra-$(uname -r)
    """)
    res = scan(root)
    msgs = [f.message for f in res.findings]
    assert rules(res) == ["apt-gone", "apt-gone", "apt-modules-extra"]
    assert [f.line for f in res.findings] == [9, 9, 10]
    assert any("libpcre3-dev" in m for m in msgs) and any("qemu-user-static" in m for m in msgs)


def test_modules_extra_literal(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: sudo apt-get install -y linux-modules-extra-azure
    """)
    assert rules(scan(root)) == ["apt-modules-extra"]


def test_conda_without_setup(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: $CONDA/bin/conda env update --file environment.yml --name base
          b:
            runs-on: ubuntu-latest
            steps:
              - uses: conda-incubator/setup-miniconda@v3
              - run: conda install numpy
    """)
    res = scan(root)
    assert [(f.rule, f.job) for f in res.findings] == [("conda", "a")]


def test_llvm_versions(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && sudo ./llvm.sh 18
          b:
            runs-on: ubuntu-latest
            steps:
              - run: sudo add-apt-repository ppa:ondrej/php
    """)
    res = scan(root)
    assert [(f.rule, f.severity, f.job) for f in res.findings] == [("llvm-apt", "break", "a"), ("ppa-ondrej", "break", "b")]


def test_golangci_v1_unless_go_pinned(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: golangci/golangci-lint-action@v6
                with:
                  version: v1.64.8
          b:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/setup-go@v5
                with: {go-version: '1.24'}
              - uses: golangci/golangci-lint-action@v6
                with: {version: v1.64.8}
          c:
            runs-on: ubuntu-latest
            steps:
              - uses: golangci/golangci-lint-action@v8
                with: {version: v2.5}
    """)
    res = scan(root)
    assert [(f.rule, f.job, f.line) for f in res.findings] == [("golangci-v1", "a", 8)]


def test_action_versions(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: PyCQA/bandit-action@v1
              - uses: swift-actions/setup-swift@v2
              - uses: erlef/setup-beam@v1
                with: {otp-version: '25', elixir-version: '1.14'}
              - uses: ruby/setup-ruby@v1
                with: {ruby-version: '3.4.2'}
              - uses: ruby/setup-ruby@v1
                with: {ruby-version: '3.4.10'}
              - uses: julia-actions/setup-julia@v2
                with: {version: '1.10.4'}
              - uses: crystal-lang/install-crystal@v1.8.0
    """)
    assert rules(scan(root)) == sorted(["bandit-action", "setup-swift-v2", "otp-old", "ruby-patch",
                                        "julia-execstack", "crystal-pcre"])


def test_ruby_version_file(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: ruby/setup-ruby@v1
                with: {bundler-cache: true}
    """, files={".ruby-version": "3.3.4\n"})
    res = scan(root)
    assert rules(res) == ["ruby-patch"] and ".ruby-version" in res.findings[0].message


def test_playwright_and_wdio_from_package_json(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: npx playwright install --with-deps
              - run: npx wdio run wdio.conf.js
    """, files={"package.json": '{"devDependencies": {"@playwright/test": "^1.55.0", "@wdio/cli": "9.20.0"}}'})
    assert rules(scan(root)) == ["playwright-old", "wdio-xvfb"]


def test_playwright_new_enough(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: npx playwright install
    """, files={"package.json": '{"devDependencies": {"@playwright/test": "^1.61.2"}}'})
    assert rules(scan(root)) == []


def test_services_mongo_and_pg_dump(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            services:
              mongo:
                image: mongo:8.0
              pg:
                image: postgres:16-alpine
            steps:
              - run: pg_dump -h localhost app > dump.sql
    """)
    res = scan(root)
    assert rules(res) == ["mongo-kernel7", "pg-dump-18"]
    assert all(f.severity == "risk" for f in res.findings)


def test_container_job_skips_userland_rules(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            container: python:3.9
            steps:
              - uses: actions/setup-python@v5
                with: {python-version: '3.9'}
              - run: sudo -E make && bwrap --ro-bind / / true
    """)
    assert rules(scan(root)) == ["bwrap"]


def test_cache_key_without_image(repo):
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/cache@v4
                with:
                  path: ~/.cache/ms-playwright
                  key: pw-${{ hashFiles('package-lock.json') }}
              - uses: actions/cache@v4
                with:
                  path: ~/.cache/ms-playwright
                  key: pw-${{ env.ImageOS }}-${{ hashFiles('package-lock.json') }}
    """)
    res = scan(root)
    assert [(f.rule, f.line) for f in res.findings] == [("cache-image-key", 6)]


def test_release_binary_glibc_floor(repo):
    root = repo("""
        jobs:
          release:
            runs-on: ubuntu-latest
            steps:
              - run: cargo build --release
              - uses: softprops/action-gh-release@v2
    """)
    assert rules(scan(root)) == ["glibc-floor"]


def test_info_gcc_hidden_by_default_severity_filter(repo):
    from latestshift.scan import filter_findings
    root = repo("""
        jobs:
          a:
            runs-on: ubuntu-latest
            steps:
              - run: gcc -O2 -o x x.c
    """)
    res = scan(root)
    assert rules(res) == ["gcc15"]
    assert filter_findings(res.findings, "risk") == []


def test_reusable_workflow_and_expression_matrix_notes(repo):
    root = repo("""
        jobs:
          call:
            uses: org/shared/.github/workflows/ci.yml@main
          dyn:
            runs-on: ${{ matrix.os }}
            strategy:
              matrix: ${{ fromJSON(needs.x.outputs.m) }}
            steps:
              - run: sudo -E true
    """)
    res = scan(root)
    assert res.findings == []
    assert any("reusable workflow" in n for n in res.notes)
    assert any("expression" in n for n in res.notes)
    assert any(j.status == "unresolved" for j in res.jobs)


def test_bad_yaml_reported(repo):
    root = repo("jobs:\n  a: [\n")
    res = scan(root)
    assert res.errors and "YAML error" in res.errors[0]


def test_example_repo_findings():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "examples", "webapp")
    res = scan(root)
    assert rules(res) == sorted(["py-old", "playwright-old", "java25-gradle", "apt-gone", "apt-gone", "sudo-E", "pg-dump-18"])
