"""Repository spelling policy for British English."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[1]
_SCAN_ROOTS = (
    _PROJECT_ROOT / "README.md",
    _PROJECT_ROOT / "docs",
    _PROJECT_ROOT / "src",
    _PROJECT_ROOT / "tests",
    _PROJECT_ROOT / "alembic",
    _PROJECT_ROOT / "pyproject.toml",
)
_IGNORED_PATHS = frozenset({Path("tests/test_british_spellings.py")})
_TEXT_SUFFIXES = frozenset({".css", ".ini", ".js", ".md", ".mako", ".py", ".toml"})
_REQUIRED_STANDARD_TERMS = frozenset({"authorization", "behavior", "center", "color", "license"})
_AMERICAN_SPELLING_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"("
    r"analy(?:ze|zed|zes|zing)|"
    r"artifacts?|"
    r"behaviors?|"
    r"canceled|canceling|cancelation|"
    r"catalog(?:ed|ing|s)?|"
    r"categoriz(?:e|ed|es|ing)|"
    r"cent(?:er|ered|ers)|"
    r"colors?|colored|coloring|"
    r"customiz(?:e|ed|es|ing|ation)|"
    r"defense|"
    r"deserializ(?:e|ed|es|ing)|"
    r"favor(?:ite|ites)?|"
    r"gray|"
    r"honors?|honored|honoring|"
    r"initializ(?:e|ed|es|ing|ation)|"
    r"labor|"
    r"licen[cs](?:e|ed|es|ing)?|"
    r"localiz(?:e|ed|es|ing|ation)|"
    r"meters?|"
    r"neighbors?|neighboring|"
    r"normaliz(?:e|ed|es|ing|ation)|"
    r"offense|"
    r"organiz(?:e|ed|es|ing|ation)|"
    r"recogniz(?:e|ed|es|ing)|"
    r"serializ(?:e|ed|er|ers|es|ing|ation)|"
    r"specializ(?:e|ed|es|ing|ation)|"
    r"theaters?|"
    r"unauthorized|authoriz(?:e|ed|es|ing|ation)|"
    r"utiliz(?:e|ed|es|ing|ation)"
    r")"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpellingViolation:
    path: Path
    line_number: int
    term: str

    def render(self) -> str:
        return f"{self.path}:{self.line_number}: {self.term}"


def test_repository_uses_british_spellings_where_possible() -> None:
    violations = tuple(_spelling_violations())

    assert not violations, "American spellings found:\n" + "\n".join(
        violation.render() for violation in violations
    )


def _spelling_violations() -> Iterator[SpellingViolation]:
    for path in _source_files():
        relative_path = path.relative_to(_PROJECT_ROOT)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _AMERICAN_SPELLING_PATTERN.finditer(line):
                term = match.group(0)
                if _is_allowed_standard_term(relative_path, term.casefold()):
                    continue
                yield SpellingViolation(relative_path, line_number, term)


def _source_files() -> Iterator[Path]:
    for root in _SCAN_ROOTS:
        if root.is_file():
            paths = (root,)
        else:
            paths = root.rglob("*")
        for path in paths:
            relative_path = path.relative_to(_PROJECT_ROOT)
            if (
                path.is_file()
                and path.suffix in _TEXT_SUFFIXES
                and relative_path not in _IGNORED_PATHS
            ):
                yield path


def _is_allowed_standard_term(path: Path, term: str) -> bool:
    if term not in _REQUIRED_STANDARD_TERMS:
        return False
    if path == Path("pyproject.toml") and term == "license":
        return True
    if path.suffix in {".css", ".js"} and term in {"behavior", "center", "color"}:
        return True
    if path.suffix == ".py" and term in {"authorization", "color"}:
        return True
    return False
