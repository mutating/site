"""Portable Unicode matching and deterministic natural name ordering."""

import re
import unicodedata


def normalize_name(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def name_key(value: str) -> str:
    # Names have at most 200 characters. Length-prefixed numbers sort 2 before 10,
    # and equal numeric spellings (2 / 02) fall back to the organization ID.
    def number(match):
        digits = match[0].lstrip("0") or "0"
        return f"\x01{len(digits):03}{digits}"

    return re.sub(r"[0-9]+", number, normalize_name(value))
