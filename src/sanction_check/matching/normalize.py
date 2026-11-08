import re

import jellyfish
from anyascii import anyascii

LEGAL_FORMS = frozenset(
    {
        "uab",
        "ab",
        "sia",
        "as",
        "ou",
        "ooo",
        "zao",
        "pao",
        "oao",
        "llc",
        "ltd",
        "limited",
        "inc",
        "co",
        "gmbh",
        "doo",
        "kft",
        "zrt",
        "nyrt",
        "youxian",
        "gongsi",
        "gufen",
        "zeren",
    }
)

CJK_LEGAL_FORMS = (
    "股份有限公司",
    "有限责任公司",
    "有限責任公司",
    "有限公司",
    "集团",
    "集團",
    "公司",
    "株式会社",
    "有限会社",
    "合同会社",
)
CJK_LEGAL_FORMS_PATTERN = re.compile("|".join(CJK_LEGAL_FORMS))
CJK_CHARACTER_PATTERN = r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]"
CJK_CHARACTER = re.compile(CJK_CHARACTER_PATTERN)
JOINING_PUNCTUATION = re.compile(r"[.'`]")
SEPARATING_PUNCTUATION = re.compile(r"[^a-z0-9]+")
NOT_ALPHANUMERIC = re.compile(r"[^A-Z0-9]")
PARENTHESIZED_TEXT = re.compile(r"\([^)]*\)")


def remove_cjk_legal_forms(name_raw: str) -> str:
    return CJK_LEGAL_FORMS_PATTERN.sub(" ", name_raw)


def normalize_name(name_raw: str) -> str:
    latin = anyascii(remove_cjk_legal_forms(name_raw)).lower()
    joined = JOINING_PUNCTUATION.sub("", latin)
    tokens = SEPARATING_PUNCTUATION.sub(" ", joined).split()

    return " ".join(token for token in tokens if token not in LEGAL_FORMS)


def has_cjk_character(text: str) -> bool:
    return CJK_CHARACTER.search(text) is not None


def normalize_registration_number(registration_number: str) -> str:
    without_notes = PARENTHESIZED_TEXT.sub("", registration_number)

    return NOT_ALPHANUMERIC.sub("", without_notes.upper())


def normalize_identifiers(identifiers: list[str]) -> list[str]:
    normalized_identifiers = [normalize_registration_number(identifier) for identifier in identifiers]

    return [identifier for identifier in normalized_identifiers if identifier]


def build_phonetic_key(normalized_name: str) -> str:
    return " ".join(jellyfish.metaphone(word) for word in normalized_name.split())
