import itertools
import random
import re
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path

import httpx
import typer
from anyascii import anyascii
from pydantic import ValidationError

from sanction_check.db.models import Entity, EntityName, EntityType
from sanction_check.helpers.log import log
from sanction_check.matching.normalize import (
    CJK_CHARACTER_PATTERN,
    CJK_LEGAL_FORMS_PATTERN,
    LEGAL_FORMS,
    has_cjk_character,
    normalize_name,
)
from sanction_check.schemas.evaluation_schema import EvalCase, EvalCategory, EvalTarget
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.screening.rules import find_score_status
from sanction_check.screening.supplier_screening import find_ranked_candidates

EVAL_SEED = 42
EVAL_SAMPLE_SIZE = 17
CLEAN_COUNTRIES = ["LT", "LV", "EE", "RS", "HU"]
CROSS_SCRIPT_COUNTRIES = ["CN", "HK", "TW"]
ID_POOL_MULTIPLIER = 10
VARIANT_POOL_MULTIPLIER = 3
COMPANY_POOL_MULTIPLIER = 2
COMMON_TOKEN_COUNT = 100
MIN_COMMON_TOKEN_LENGTH = 4
MIN_CASES_PER_VARIANT_KIND = 1
GLEIF_BASE_URL = "https://api.gleif.org/api/v1"
GLEIF_TIMEOUT_SECONDS = 10.0
GLEIF_MAX_PAGE_SIZE = 200
GLEIF_ACCEPT_HEADER = "application/vnd.api+json"
MIN_TYPO_WORD_LENGTH = 4
MIN_PARTIAL_WORD_COUNT = 3
CYRILLIC_LETTER = re.compile(r"[Ѐ-ӿ]")
CJK_RUN = re.compile(CJK_CHARACTER_PATTERN + "+")
PINYIN_WORD_LENGTH = 2
LEGAL_FORM_PUNCTUATION = ".,\"'"
CONFIRMED_SUPPLIERS = {
    "NK-N7dfJjNtqB9hibmDaoviPf": ScreeningInput(name="PJSC GAZPROM", country="RU", reg_no="7736050003"),
    "NK-cuVFsKgk3rPqzM4pWTexzc": ScreeningInput(
        name="Public Joint Stock Company Gazprom Neft", country="RU", reg_no="5504036333"
    ),
    "NK-nq68oAyhLJDTxPnYfQq8Vg": ScreeningInput(
        name="Open Joint-Stock Company Rosneft Oil Company", country="RU", reg_no="7706107510"
    ),
    "NK-f7u9JR4f7piS8PngjTkUSa": ScreeningInput(name="Gazprombank", country="RU", reg_no="7744001497"),
    "NK-mvzx7uNDj9jZf8EAxaHH2h": ScreeningInput(name="VTB Bank", country="RU", reg_no="1027739609391"),
    "NK-VDEKGF5ZgHAkye4kGuUXs3": ScreeningInput(name="Sovcomflot", country="RU", reg_no="7702060116"),
    "NK-JDEoxEMoEQHGnYeJqt9qox": ScreeningInput(name="Alrosa", country="RU", reg_no="1433000147"),
    "NK-gyXAZAjfijNDV3fHyi8HTR": ScreeningInput(name="Surgutneftegas", country="RU", reg_no="8602060555"),
    "NK-kNULoiM7XfTYeWLtTZ4UHg": ScreeningInput(name="State Corporation Rostec", country="RU", reg_no="7704274402"),
    "NK-jLcvcAFxBd2aYNeqUvit3a": ScreeningInput(name="Rosoboronexport OJSC", country="RU", reg_no="7718852163"),
    "NK-nR5AXM9AVPq4pQaSPSaVam": ScreeningInput(name="JSC Kalashnikov Concern", country="RU", reg_no="1832090230"),
    "NK-mQUAbTQtxXSSatg7uSPSAz": ScreeningInput(
        name="Joint-Stock Company Concern Almaz-Antey", country="RU", reg_no="7731084175"
    ),
    "NK-WnLPPjoB8rSwNQJ4o5ZeeK": ScreeningInput(
        name='Open Joint Stock Company "Belaruskali"', country="BY", reg_no="600122610"
    ),
    "NK-YmLP9PngSL4mPvdK3L2rW9": ScreeningInput(
        name="Islamic Republic of Iran Shipping Lines", country="IR", reg_no="10861629011"
    ),
    "NK-gGV2Tg7C77XZ9C6svLPHep": ScreeningInput(
        name="Petroleos De Venezuela, S.A.", country="VE", reg_no="J000950369"
    ),
    "NK-ntj6mnGQTwchT5LgwujyRH": ScreeningInput(name="Bank Melli Iran", country="IR"),
    "NK-neZdRCM4Agd9EjZvWy6Yoh": ScreeningInput(name="Mahan Air", country="IR"),
    "NK-ZSYwNUJeVRptthnMWmLtzs": ScreeningInput(name="Korea Mining Development Trading Corporation", country="KP"),
}

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


def fetch_companies(client: httpx.Client, country: str, page_number: int, page_size: int) -> list[ScreeningInput]:
    response = client.get(
        "/lei-records",
        params={"filter[entity.legalAddress.country]": country, "page[size]": page_size, "page[number]": page_number},
        headers={"Accept": GLEIF_ACCEPT_HEADER},
    )
    response.raise_for_status()

    entities = [record["attributes"]["entity"] for record in response.json()["data"]]

    return [
        ScreeningInput(
            name=entity["legalName"]["name"], country=entity["legalAddress"]["country"], reg_no=entity["registeredAs"]
        )
        for entity in entities
    ]


def list_companies(country: str, count: int) -> list[ScreeningInput]:
    page_size = min(count, GLEIF_MAX_PAGE_SIZE)
    companies: list[ScreeningInput] = []
    page_number = 1

    with httpx.Client(base_url=GLEIF_BASE_URL, timeout=GLEIF_TIMEOUT_SECONDS) as client:
        while len(companies) < count:
            page = fetch_companies(client, country, page_number, page_size)
            log.info("fetched %d companies for %s, page %d", len(page), country, page_number)
            if not page:
                break

            companies.extend(page)
            page_number += 1

    return companies[:count]


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


def build_case_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number:04d}"


def build_positive_case(case_id: str, category: EvalCategory, target: EvalTarget, name: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        category=category,
        input=ScreeningInput(name=name, country=target.supplier.country),
        expected_status_min="review",
        expected_entity_id=target.entity_id,
    )


def build_hanzi_case(case_id: str, target: EvalTarget) -> EvalCase:
    return EvalCase(
        id=case_id,
        category="cross_script",
        input=target.supplier,
        expected_status_min="review",
        expected_entity_id=target.entity_id,
        should_hide_cjk_names=True,
    )


def build_clean_case(category: EvalCategory, company: ScreeningInput, number: int) -> EvalCase:
    return EvalCase(
        id=build_case_id(category, number),
        category=category,
        input=company,
        expected_status_min="clear",
        expected_entity_id=None,
    )


def has_common_token(supplier: ScreeningInput, common_tokens: set[str]) -> bool:
    return not common_tokens.isdisjoint(normalize_name(supplier.name).split())


def is_confusable_with_target(supplier: ScreeningInput) -> bool:
    candidates = find_ranked_candidates(supplier)

    return bool(candidates) and find_score_status(candidates[0], supplier) != "clear"


def build_target_if_valid(entity_id: str, name: str, country: str) -> EvalTarget | None:
    try:
        supplier = ScreeningInput(name=name, country=country)
    except ValidationError:
        return None

    return EvalTarget(entity_id=entity_id, supplier=supplier)


def sample_targets(
    random_generator: random.Random,
    entity_ids: list[str],
    count: int,
    fetch_targets: Callable[[list[str]], list[EvalTarget]],
) -> list[EvalTarget]:
    shuffled_entity_ids = list(entity_ids)
    random_generator.shuffle(shuffled_entity_ids)

    return fetch_targets(shuffled_entity_ids[: count * ID_POOL_MULTIPLIER])[:count]


def list_target_ids() -> list[str]:
    entities = Entity.objects.filter(countries__len__gt=0).exclude(entity_type=EntityType.PERSON)

    return list(entities.order_by("id").values_list("id", flat=True))


def fetch_direct_targets(entity_ids: list[str]) -> list[EvalTarget]:
    entities = Entity.objects.filter(entity_type=EntityType.COMPANY).in_bulk(entity_ids)

    targets = [
        build_target_if_valid(entity_id, entities[entity_id].name, entities[entity_id].countries[0])
        for entity_id in entity_ids
        if entity_id in entities
    ]

    return [target for target in targets if target is not None]


def build_confirmed_cases() -> list[EvalCase]:
    missing_entity_ids = CONFIRMED_SUPPLIERS.keys() - Entity.objects.in_bulk(list(CONFIRMED_SUPPLIERS)).keys()
    if missing_entity_ids:
        raise ValueError(f"confirmed targets not in the database: {sorted(missing_entity_ids)!r}")

    return [
        EvalCase(
            id=build_case_id("confirmed", number),
            category="confirmed",
            input=supplier,
            expected_status_min="likely_hit",
            expected_entity_id=entity_id,
        )
        for number, (entity_id, supplier) in enumerate(CONFIRMED_SUPPLIERS.items(), start=1)
    ]


def build_exact_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    targets = sample_targets(random_generator, list_target_ids(), sample_size, fetch_direct_targets)

    return [
        build_positive_case(build_case_id("exact", number), "exact", target, target.supplier.name)
        for number, target in enumerate(targets, start=1)
    ]


def build_variant_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    variant_builders: dict[str, Callable[[str], str | None]] = {
        "typo": partial(introduce_typo, random_generator=random_generator),
        "transliteration": transliterate,
        "legal_form": drop_legal_form,
        "word_order": reorder_words,
        "partial": keep_partial_name,
    }

    pool = sample_targets(
        random_generator, list_target_ids(), sample_size * VARIANT_POOL_MULTIPLIER, fetch_direct_targets
    )
    count_per_kind = max(sample_size // len(variant_builders), MIN_CASES_PER_VARIANT_KIND)

    cases = []
    for kind, build_variant in variant_builders.items():
        kind_count = 0

        for target in random_generator.sample(pool, len(pool)):
            variant_name = build_variant(target.supplier.name)
            if variant_name is None or variant_name == target.supplier.name:
                continue
            kind_count += 1
            case_id = build_case_id(f"variant-{kind}", kind_count)
            cases.append(build_positive_case(case_id, "variant", target, variant_name))
            if kind_count == count_per_kind:
                break

        if kind_count < count_per_kind:
            log.warning("only %d of %d %s variants built", kind_count, count_per_kind, kind)

    return cases


def find_common_tokens() -> set[str]:
    organization_names = (
        Entity.objects.exclude(entity_type=EntityType.PERSON).values_list("name_norm", flat=True).iterator()
    )
    token_counts = Counter(
        token for name in organization_names for token in name.split() if len(token) >= MIN_COMMON_TOKEN_LENGTH
    )

    return {token for token, _ in token_counts.most_common(COMMON_TOKEN_COUNT)}


def build_clean_cases(
    random_generator: random.Random, category: EvalCategory, companies: list[ScreeningInput], sample_size: int
) -> list[EvalCase]:
    cases = []

    for company in random_generator.sample(companies, len(companies)):
        if is_confusable_with_target(company):
            log.info("skipped %r: confusable with a sanctioned target", company.name)
            continue
        cases.append(build_clean_case(category, company, len(cases) + 1))

        if len(cases) == sample_size:
            return cases

    log.warning("only %d of %d %s cases built", len(cases), sample_size, category)

    return cases


def build_company_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    companies = [
        company
        for country in CLEAN_COUNTRIES
        for company in list_companies(country, sample_size * COMPANY_POOL_MULTIPLIER)
    ]

    common_tokens = find_common_tokens()

    common_name_companies = [company for company in companies if has_common_token(company, common_tokens)]
    negative_companies = [company for company in companies if not has_common_token(company, common_tokens)]

    return [
        *build_clean_cases(random_generator, "negative", negative_companies, sample_size),
        *build_clean_cases(random_generator, "common_name", common_name_companies, sample_size),
    ]


def list_cross_script_entity_ids() -> list[str]:
    cjk_entity_ids = EntityName.objects.filter(name_raw__regex=CJK_CHARACTER_PATTERN).values("entity_id")
    latin_entity_ids = EntityName.objects.exclude(name_raw__regex=CJK_CHARACTER_PATTERN).values("entity_id")

    entities = (
        Entity.objects.filter(countries__overlap=CROSS_SCRIPT_COUNTRIES, id__in=cjk_entity_ids)
        .filter(id__in=latin_entity_ids)
        .exclude(entity_type=EntityType.PERSON)
    )

    return list(entities.order_by("id").values_list("id", flat=True))


def find_cross_script_country(entity: Entity) -> str:
    return next(country for country in entity.countries if country in CROSS_SCRIPT_COUNTRIES)


def fetch_cross_script_targets(entity_ids: list[str]) -> list[EvalTarget]:
    cjk_names = (
        EntityName.objects.filter(entity_id__in=entity_ids, name_raw__regex=CJK_CHARACTER_PATTERN)
        .select_related("entity")
        .order_by("id")
    )

    first_cjk_names: dict[str, EntityName] = {}
    for cjk_name in cjk_names:
        first_cjk_names.setdefault(cjk_name.entity_id, cjk_name)

    targets = []
    for entity_id in entity_ids:
        cjk_name = first_cjk_names[entity_id]
        country = find_cross_script_country(cjk_name.entity)
        targets.append(build_target_if_valid(entity_id, cjk_name.name_raw, country))

    return [target for target in targets if target is not None]


def build_cross_script_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    targets = sample_targets(
        random_generator, list_cross_script_entity_ids(), sample_size // 2, fetch_cross_script_targets
    )

    hanzi_cases = [
        build_hanzi_case(build_case_id("cross_script-hanzi", number), target)
        for number, target in enumerate(targets, start=1)
    ]
    pinyin_cases = [
        build_positive_case(
            build_case_id("cross_script-pinyin", number),
            "cross_script",
            target,
            transliterate_to_pinyin_words(target.supplier.name),
        )
        for number, target in enumerate(targets, start=1)
    ]

    return [*hanzi_cases, *pinyin_cases]


def build_cross_script_clean_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    companies = [
        company
        for country in CROSS_SCRIPT_COUNTRIES
        for company in list_companies(country, sample_size)
        if has_cjk_character(company.name)
    ]

    return build_clean_cases(random_generator, "cross_script_clean", companies, sample_size)


CASE_BUILDERS = [
    build_exact_cases,
    build_variant_cases,
    build_company_cases,
    build_cross_script_cases,
    build_cross_script_clean_cases,
]


def build_eval_set(output_path: Path) -> int:
    random_generator = random.Random(EVAL_SEED)

    cases = build_confirmed_cases()
    for build_cases in CASE_BUILDERS:
        built_cases = build_cases(random_generator, EVAL_SAMPLE_SIZE)
        log.info("built %d cases with %s", len(built_cases), build_cases.__name__)
        cases.extend(built_cases)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        file.writelines(f"{case.model_dump_json(exclude_defaults=True)}\n" for case in cases)

    log.info("wrote %d eval cases to %s", len(cases), output_path)

    return len(cases)


def main(output: Path) -> None:
    """Regenerate eval cases from real data and write them to OUTPUT."""
    typer.echo(f"{build_eval_set(output)} cases written to {output}")


if __name__ == "__main__":
    typer.run(main)
