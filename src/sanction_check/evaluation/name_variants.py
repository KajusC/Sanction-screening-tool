import itertools
import random
import re

from anyascii import anyascii

from sanction_check.matching.normalize import CJK_CHARACTER_PATTERN, CJK_LEGAL_FORMS_PATTERN, LEGAL_FORMS

MIN_TYPO_WORD_LENGTH = 4
MIN_PARTIAL_WORD_COUNT = 3
CYRILLIC_LETTER = re.compile(r"[Ѐ-ӿ]")
CJK_RUN = re.compile(CJK_CHARACTER_PATTERN + "+")
PINYIN_WORD_LENGTH = 2
LEGAL_FORM_PUNCTUATION = ".,\"'"

LATIN_TO_CYRILLIC = {
    "shch": "щ",
    "sh": "ш",
    "ch": "ч",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "yu": "ю",
    "ya": "я",
    "yo": "ё",
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "ы",
    "z": "з",
}
LATIN_PATTERN = re.compile("|".join(sorted(map(re.escape, LATIN_TO_CYRILLIC), key=len, reverse=True)), re.IGNORECASE)


def introduce_typo(name: str, random_generator: random.Random) -> str | None:
    words = name.split()
    long_word_positions = [position for position, word in enumerate(words) if len(word) >= MIN_TYPO_WORD_LENGTH]
    if not long_word_positions:
        return None

    word_position = random_generator.choice(long_word_positions)
    word = words[word_position]

    swap_position = random_generator.randrange(1, len(word) - 1)

    words[word_position] = (
        word[:swap_position] + word[swap_position + 1] + word[swap_position] + word[swap_position + 2 :]
    )

    return " ".join(words)


def transliterate(name: str) -> str:
    if CYRILLIC_LETTER.search(name):
        return anyascii(name)

    return LATIN_PATTERN.sub(lambda match: LATIN_TO_CYRILLIC[match.group().lower()], name).title()


def drop_legal_form(name: str) -> str | None:
    kept_words = [word for word in name.split() if word.lower().strip(LEGAL_FORM_PUNCTUATION) not in LEGAL_FORMS]

    return " ".join(kept_words) or None


def reorder_words(name: str) -> str | None:
    words = name.split()
    if len(words) < 2:
        return None

    return " ".join(reversed(words))


def keep_partial_name(name: str) -> str | None:
    words = name.split()
    if len(words) < MIN_PARTIAL_WORD_COUNT:
        return None

    return " ".join(words[:-1])


def spell_pinyin_word(characters: tuple[str, ...]) -> str:
    return "".join(anyascii(character).strip() for character in characters).capitalize()


def spell_pinyin_words(cjk_run: str) -> str:
    return " ".join(spell_pinyin_word(characters) for characters in itertools.batched(cjk_run, PINYIN_WORD_LENGTH))


def transliterate_to_pinyin_words(name: str) -> str:
    separated = CJK_LEGAL_FORMS_PATTERN.sub(lambda match: f" {match.group()} ", name)

    return " ".join(CJK_RUN.sub(lambda match: spell_pinyin_words(match.group()), token) for token in separated.split())
