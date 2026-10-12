"""T5.1: static/AST-lite guard over `QuickRes.spec`.

Verifies the PyInstaller build spec actually bundles the two webview assets
(`panel.html`, `QuickRes.png`) at the exact package-relative destination that
`quickres.config.resource_path()` expects inside a frozen `_MEIPASS`, and
that Tkinter is excluded now that `quickres/gui.py` is gone.

This is a static guard, not a real PyInstaller build (no PyInstaller
invocation happens in CI) -- it parses `QuickRes.spec` as Python source via
`ast.parse` (never executed/imported -- `Analysis`/`PYZ`/`EXE` are PyInstaller
runtime names that don't exist outside a real build) and inspects the
`Analysis(...)` / `EXE(...)` call nodes directly.
"""

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(REPO_ROOT, "QuickRes.spec")

# Package-relative destinations resource_path("quickres/webview/panel.html")
# resolves to inside a frozen build: os.path.join(sys._MEIPASS, relative_path).
# PyInstaller's `datas` dest directory must therefore be "quickres/webview"
# for BOTH assets, or resource_path() 404s at runtime despite a clean build.
EXPECTED_DATAS = {
    ("quickres/webview/panel.html", "quickres/webview"),
    ("quickres/webview/QuickRes.png", "quickres/webview"),
}


@pytest.fixture(scope="module")
def spec_source():
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def spec_tree(spec_source):
    return ast.parse(spec_source, filename=SPEC_PATH)


def _find_call(tree, func_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func_name:
            return node
    return None


def _kwarg(call_node, name):
    for kw in call_node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _const_tuple_pairs(list_node):
    """Extract (str, str) literal pairs from an ast.List of ast.Tuple nodes."""
    pairs = set()
    if list_node is None:
        return pairs
    assert isinstance(list_node, ast.List), "datas= must be a literal list"
    for elt in list_node.elts:
        assert isinstance(elt, ast.Tuple), "each datas entry must be a literal tuple"
        assert len(elt.elts) == 2, "each datas tuple must be (src, dest)"
        src, dest = elt.elts
        assert isinstance(src, ast.Constant) and isinstance(src.value, str)
        assert isinstance(dest, ast.Constant) and isinstance(dest.value, str)
        pairs.add((src.value, dest.value))
    return pairs


def _const_str_list(list_node):
    values = []
    if list_node is None:
        return values
    assert isinstance(list_node, ast.List)
    for elt in list_node.elts:
        assert isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        values.append(elt.value)
    return values


def test_spec_file_exists():
    assert os.path.exists(SPEC_PATH), "QuickRes.spec must exist at repo root"


def test_analysis_datas_bundles_panel_html_and_logo_at_package_relative_dest(spec_tree):
    analysis_call = _find_call(spec_tree, "Analysis")
    assert analysis_call is not None, "QuickRes.spec must call Analysis(...)"

    datas_node = _kwarg(analysis_call, "datas")
    pairs = _const_tuple_pairs(datas_node)

    assert EXPECTED_DATAS.issubset(pairs), (
        f"Analysis(datas=[...]) must bundle both webview assets at the "
        f"'quickres/webview' dest so resource_path() resolves them inside "
        f"_MEIPASS. Expected {EXPECTED_DATAS}, found {pairs}."
    )


def test_analysis_excludes_tkinter(spec_tree):
    analysis_call = _find_call(spec_tree, "Analysis")
    assert analysis_call is not None

    excludes_node = _kwarg(analysis_call, "excludes")
    excludes = _const_str_list(excludes_node)

    assert "tkinter" in excludes, (
        "Analysis(excludes=[...]) must exclude 'tkinter' now that "
        "quickres/gui.py is deleted and quickres/updater.py no longer "
        "imports tkinter.messagebox."
    )


def test_exe_icon_matches_repo_icon_availability(spec_tree):
    icon_path = os.path.join(REPO_ROOT, "icon.ico")
    exe_call = _find_call(spec_tree, "EXE")
    assert exe_call is not None, "QuickRes.spec must call EXE(...)"

    icon_node = _kwarg(exe_call, "icon")

    if os.path.exists(icon_path):
        assert icon_node is not None, "icon.ico exists in the repo but EXE(icon=...) is missing"
        assert isinstance(icon_node, ast.Constant) and icon_node.value == "icon.ico"
    else:
        # No icon.ico is checked into the repo (verified at collection time
        # above) -- adding icon='icon.ico' here would silently break the
        # frozen build with a missing-file error, so EXE(...) must NOT
        # declare it until an icon.ico actually ships.
        assert icon_node is None, "icon.ico is not present in the repo; EXE(icon=...) must not be set"


def test_tkinter_is_unreachable_from_the_updater_module():
    # Companion runtime check (not spec-static): excludes=['tkinter'] is only
    # safe if nothing in the frozen app graph actually imports tkinter.
    # quickres/updater.py used to import tkinter.messagebox for its now-dead
    # check_for_update()/_check_for_update() functions (unused since
    # quickres/gui.py was deleted in Slice 3) -- this proves that import is
    # gone for good, not just currently unused.
    updater_path = os.path.join(REPO_ROOT, "quickres", "updater.py")
    with open(updater_path, "r", encoding="utf-8") as f:
        updater_source = f.read()

    assert "tkinter" not in updater_source
