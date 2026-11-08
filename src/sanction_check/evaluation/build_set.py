import random
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from sanction_check.db.models import Entity, EntityName, EntityType
from sanction_check.evaluation.gleif import list_companies
from sanction_check.evaluation.name_variants import (
    drop_legal_form,
    introduce_typo,
    keep_partial_name,
    reorder_words,
    transliterate,
    transliterate_to_pinyin_words,
)
from sanction_check.helpers.log import log
from sanction_check.matching.normalize import CJK_CHARACTER_PATTERN, has_cjk_character, normalize_name
from sanction_check.schemas.evaluation_schema import EvalCase, EvalCategory, EvalTarget
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.screening.supplier_screening import find_ranked_candidates
from sanction_check.settings import get_settings

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

    return bool(candidates) and candidates[0].score >= get_settings().hit_threshold


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


def list_target_ids(is_person: bool) -> list[str]:
    entities = Entity.objects.filter(countries__len__gt=0)

    if is_person:
        entities = entities.filter(entity_type=EntityType.PERSON)
    else:
        entities = entities.exclude(entity_type=EntityType.PERSON)

    return list(entities.order_by("id").values_list("id", flat=True))


def fetch_direct_targets(entity_ids: list[str]) -> list[EvalTarget]:
    entities = Entity.objects.filter(id__in=entity_ids).in_bulk()

    targets = [
        build_target_if_valid(entity_id, entities[entity_id].name, entities[entity_id].countries[0])
        for entity_id in entity_ids
    ]

    return [target for target in targets if target is not None]


def build_exact_cases(random_generator: random.Random, sample_size: int) -> list[EvalCase]:
    person_count = sample_size // 2
    targets = [
        *sample_targets(random_generator, list_target_ids(is_person=True), person_count, fetch_direct_targets),
        *sample_targets(
            random_generator, list_target_ids(is_person=False), sample_size - person_count, fetch_direct_targets
        ),
    ]

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
        random_generator, list_target_ids(is_person=False), sample_size * VARIANT_POOL_MULTIPLIER, fetch_direct_targets
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

    cases = []
    for build_cases in CASE_BUILDERS:
        built_cases = build_cases(random_generator, EVAL_SAMPLE_SIZE)
        log.info("built %d cases with %s", len(built_cases), build_cases.__name__)
        cases.extend(built_cases)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        file.writelines(f"{case.model_dump_json()}\n" for case in cases)

    log.info("wrote %d eval cases to %s", len(cases), output_path)

    return len(cases)
