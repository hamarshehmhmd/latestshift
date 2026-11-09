from latestshift.workflow import expand_matrix, labels_of, substitute
from latestshift.yamlload import load


def test_line_numbers_kept():
    d = load("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n")
    assert d["jobs"].line_of("a") == 2
    assert d["jobs"]["a"].line_of("runs-on") == 3
    assert d["jobs"]["a"]["steps"].lines == [5]


def test_on_key_not_true():
    d = load("on: push\njobs: {}\n")
    assert "on" in d


def test_unquoted_310_becomes_31_like_github():
    d = load("v: [3.9, 3.10]\n")
    assert [str(x) for x in d["v"]] == ["3.9", "3.1"]


def test_matrix_product_and_exclude():
    combos, note = expand_matrix({"os": ["ubuntu-latest", "windows-latest"], "py": ["3.9", "3.12"],
                                  "exclude": [{"os": "windows-latest", "py": "3.9"}]})
    assert note is None
    assert len(combos) == 3
    assert {"os": "windows-latest", "py": "3.9"} not in combos


def test_matrix_include_extends_and_adds():
    combos, _ = expand_matrix({"py": ["3.12"], "include": [{"py": "3.12", "extra": "x"}, {"py": "3.8", "os": "ubuntu-24.04"}]})
    assert {"py": "3.12", "extra": "x"} in combos
    assert {"py": "3.8", "os": "ubuntu-24.04"} in combos


def test_matrix_expression_is_noted():
    combos, note = expand_matrix("${{ fromJSON(needs.setup.outputs.matrix) }}")
    assert combos == [{}] and "expression" in note


def test_substitute():
    c = {"os": "ubuntu-latest", "cfg": {"py": "3.9"}}
    assert substitute("${{ matrix.os }}", c) == "ubuntu-latest"
    assert substitute("py-${{ matrix.cfg.py }}-x", c) == "py-3.9-x"
    assert substitute("${{ matrix.missing }}", c) == "${{ matrix.missing }}"
    assert substitute("${{ github.ref }}", c) == "${{ github.ref }}"


def test_labels_of_shapes():
    assert labels_of("ubuntu-latest") == ["ubuntu-latest"]
    assert labels_of(["self-hosted", "linux"]) == ["self-hosted", "linux"]
    assert labels_of({"group": "big", "labels": "ubuntu-latest"}) == ["ubuntu-latest", "group:big"]
