"""Conservative multilingual numeral parsing and natural sorting helpers."""

from __future__ import annotations

import re
from unicodedata import decimal
from unicodedata import normalize as normalise

_ASCII_ROMAN_PATTERN = re.compile(r"^[IVXLCDM]+$")
_FULLWIDTH_DECIMAL_DIGITS = "".join(chr(code) for code in range(0xFF10, 0xFF1A))
_DECIMAL_DIGITS = frozenset("0123456789" + _FULLWIDTH_DECIMAL_DIGITS)
_DECIMAL_TOKEN_PATTERN = r"[0-9\uFF10-\uFF19]+"
_HAN_DIGITS = {
    "\u3007": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "壱": 1,
    "壹": 1,
    "弐": 2,
    "貳": 2,
    "参": 3,
    "參": 3,
    "肆": 4,
    "伍": 5,
    "陸": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}
_HAN_UNITS = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1_000,
    "仟": 1_000,
    "万": 10_000,
    "萬": 10_000,
    "亿": 100_000_000,
    "億": 100_000_000,
    "兆": 1_000_000_000_000,
}
_HANGUL_DIGITS = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
_HANGUL_UNITS = {
    "십": 10,
    "백": 100,
    "천": 1_000,
    "만": 10_000,
    "억": 100_000_000,
    "조": 1_000_000_000_000,
}
_UNICODE_ROMAN_CHARACTERS = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅬⅭⅮⅯⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻⅼⅽⅾⅿ"
_HAN_NUMERAL_CHARACTERS = "".join((*_HAN_DIGITS, *_HAN_UNITS))
_HANGUL_NUMERAL_CHARACTERS = "".join((*_HANGUL_DIGITS, *_HANGUL_UNITS))
NUMERAL_TOKEN_PATTERN = (
    rf"(?:{_DECIMAL_TOKEN_PATTERN}|[{_UNICODE_ROMAN_CHARACTERS}]+|[IVXLCDMivxlcdm]+|"
    rf"[{_HAN_NUMERAL_CHARACTERS}]+|[{_HANGUL_NUMERAL_CHARACTERS}]+)"
)
_NATURAL_NUMERAL_PATTERN = re.compile(
    rf"{_DECIMAL_TOKEN_PATTERN}|[{_UNICODE_ROMAN_CHARACTERS}]+|"
    rf"(?<![A-Za-z])[IVXLCDM]+(?![A-Za-z])|"
    rf"[{_HAN_NUMERAL_CHARACTERS}]+|[{_HANGUL_NUMERAL_CHARACTERS}]+"
)


def parse_numeral(value: str, *, maximum: int | None = None) -> int | None:
    """Parse an unambiguous Arabic, Roman, Han, or Hangul integer token."""

    candidate = value.strip()
    if not candidate:
        return None
    parsed = _parse_decimal(candidate)
    if parsed is None:
        parsed = _parse_roman(candidate)
    if parsed is None:
        parsed = _parse_east_asian(candidate, _HAN_DIGITS, _HAN_UNITS)
    if parsed is None:
        parsed = _parse_east_asian(candidate, _HANGUL_DIGITS, _HANGUL_UNITS)
    if parsed is None or (maximum is not None and parsed > maximum):
        return None
    return parsed


def natural_sort_key(value: str) -> str:
    """Return a case-insensitive key that compares embedded numerals by value."""

    def replace(match: re.Match[str]) -> str:
        parsed = parse_numeral(match.group())
        return match.group().casefold() if parsed is None else f"\x01{parsed:020d}"

    return _NATURAL_NUMERAL_PATTERN.sub(replace, normalise("NFKC", value)).casefold()


def _parse_decimal(value: str) -> int | None:
    if not value or any(character not in _DECIMAL_DIGITS for character in value):
        return None
    try:
        return int("".join(str(decimal(character)) for character in value))
    except ValueError:
        return None


def _parse_roman(value: str) -> int | None:
    normalised = normalise("NFKC", value).upper()
    if _ASCII_ROMAN_PATTERN.fullmatch(normalised) is None:
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1_000}
    total = 0
    previous = 0
    for character in reversed(normalised):
        current = values[character]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total if _roman_numeral(total) == normalised else None


def _roman_numeral(value: int) -> str:
    parts = (
        (1_000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
        (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
        (5, "V"), (4, "IV"), (1, "I"),
    )
    result: list[str] = []
    remainder = value
    for amount, symbol in parts:
        count, remainder = divmod(remainder, amount)
        result.append(symbol * count)
    return "".join(result)


def _parse_east_asian(
    value: str, digits: dict[str, int], units: dict[str, int]
) -> int | None:
    """Parse canonical multiplicative East-Asian numerals without guessing malformed forms."""

    if not value or any(character not in digits and character not in units for character in value):
        return None
    if not any(character in units for character in value):
        return digits[value] if len(value) == 1 else None
    total = 0
    section = 0
    digit: int | None = None
    previous_large_unit: int | None = None
    previous_small_unit: int | None = None
    zero_pending = False
    for character in value:
        if character in digits:
            parsed_digit = digits[character]
            if parsed_digit == 0:
                if (
                    digit is not None
                    or zero_pending
                    or (total == 0 and section == 0 and previous_large_unit is None)
                ):
                    return None
                zero_pending = True
            elif digit is not None:
                return None
            else:
                zero_pending = False
                digit = parsed_digit
            continue
        unit = units[character]
        if zero_pending:
            return None
        current = digit if digit is not None else 1
        if unit >= 10_000:
            if previous_large_unit is not None and unit >= previous_large_unit:
                return None
            total += (section + current) * unit
            section = 0
            previous_large_unit = unit
            previous_small_unit = None
        else:
            if previous_small_unit is not None and unit >= previous_small_unit:
                return None
            section += current * unit
            previous_small_unit = unit
        digit = None
    return None if zero_pending else total + section + (digit or 0)
