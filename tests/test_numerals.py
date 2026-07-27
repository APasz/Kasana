"""Regression coverage for conservative multilingual numeral parsing."""

from __future__ import annotations

import pytest

from kasana.katalog.numerals import natural_sort_key, parse_numeral
from kasana.katalog.parsing import parse_episode_numbers, parse_season_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("十", 10),
        ("十二", 12),
        ("二十", 20),
        ("一百零一", 101),
        ("一万零二十", 10_020),
        ("십", 10),
        ("십이", 12),
        ("이십", 20),
        ("백일", 101),
        ("일만이십", 10_020),
    ],
)
def test_parse_numeral_accepts_canonical_east_asian_forms(value: str, expected: int) -> None:
    assert parse_numeral(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "一二",
        "一二十",
        "十十",
        "十百",
        "百百",
        "一万万",
        "万亿",
        "零十",
        "一百零",
        "零零一",
        "일이",
        "일이십",
        "십십",
        "십백",
        "백백",
        "일만만",
        "만억",
        "영십",
        "백영",
    ),
)
def test_parse_numeral_rejects_malformed_east_asian_forms(value: str) -> None:
    assert parse_numeral(value) is None


def test_malformed_east_asian_numerals_do_not_affect_episode_detection_or_sorting() -> None:
    assert parse_season_number("第十百季", allow_volume=False) is None
    assert parse_episode_numbers("Show S一二E三", season_from_directory=1) is None
    assert "\x01" not in natural_sort_key("Episode 一二")
