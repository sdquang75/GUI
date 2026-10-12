import os
import re

from packaging.requirements import Requirement

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_PATH = os.path.join(REPO_ROOT, "requirements.txt")

_HASH_TOKEN_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


def _read_requirement_lines():
    with open(REQUIREMENTS_PATH, "r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh.readlines()]
    return [line for line in lines if line and not line.startswith("#")]


def _parse_line(line):
    """Split a requirements-file line into (requirement_str, hash_tokens)."""
    tokens = line.split()
    requirement_str = tokens[0]
    hash_tokens = tokens[1:]
    return requirement_str, hash_tokens


def test_requirements_file_is_not_empty():
    assert _read_requirement_lines(), "requirements.txt must list at least one dependency"


def test_every_requirement_is_pinned_to_exact_version():
    # pip's hash-checking mode refuses to install anything that isn't pinned
    # with == once any --hash is present, so this is a precondition, not just
    # a style preference.
    for line in _read_requirement_lines():
        requirement_str, _ = _parse_line(line)
        req = Requirement(requirement_str)
        specifiers = list(req.specifier)
        assert len(specifiers) == 1, f"{requirement_str!r} must have exactly one specifier"
        assert specifiers[0].operator == "==", f"{requirement_str!r} must be pinned with =="


def test_every_requirement_has_at_least_one_valid_sha256_hash():
    for line in _read_requirement_lines():
        requirement_str, hash_tokens = _parse_line(line)
        assert hash_tokens, (
            f"{requirement_str!r} has no --hash entries: pip performs no integrity "
            "verification against PyPI without them"
        )
        for token in hash_tokens:
            match = _HASH_TOKEN_RE.match(token)
            assert match, f"malformed hash token {token!r} for {requirement_str!r}"


def test_pip_would_enter_hash_checking_mode_for_every_line():
    # Once any requirement in a file carries a --hash, pip requires every
    # requirement it needs to install -- including transitive dependencies --
    # to be pinned and hashed too, or the install fails outright. Asserting
    # every line (not just the direct top-level deps) carries a hash catches
    # that failure mode before it reaches `pip install -r requirements.txt`.
    lines = _read_requirement_lines()
    for line in lines:
        _, hash_tokens = _parse_line(line)
        assert hash_tokens, f"line {line!r} would break --require-hashes mode"


def test_direct_dependencies_are_present_with_expected_versions():
    names_to_versions = {}
    for line in _read_requirement_lines():
        requirement_str, _ = _parse_line(line)
        req = Requirement(requirement_str)
        version = next(iter(req.specifier)).version
        names_to_versions[req.name.lower()] = version

    assert names_to_versions.get("pyinstaller") == "6.22.1"
    assert names_to_versions.get("pywebview") == "6.2.1"


def test_no_duplicate_package_entries():
    seen = set()
    for line in _read_requirement_lines():
        requirement_str, _ = _parse_line(line)
        name = Requirement(requirement_str).name.lower()
        assert name not in seen, f"duplicate requirements.txt entry for {name!r}"
        seen.add(name)
