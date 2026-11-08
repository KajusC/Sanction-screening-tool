import math

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Count, Max, Q
from pgvector.django import CosineDistance

from sanction_check.db.models import Entity, EntityName
from sanction_check.helpers.log import log
from sanction_check.loading.embed import embed_texts
from sanction_check.matching.normalize import build_phonetic_key, normalize_registration_number
from sanction_check.schemas.candidate_schema import CandidateName, SearchMethod, UnscoredCandidate
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.settings import get_settings


def find_registration_number_ids(registration_number: str | None) -> list[str]:
    if registration_number is None:
        return []

    entities = Entity.objects.filter(identifiers__contains=[normalize_registration_number(registration_number)])

    return list(entities.values_list("id", flat=True)[: get_settings().top_k])


def find_similar_name_ids(field_name: str, search_text: str) -> list[str]:
    if not search_text:
        return []

    names = (
        EntityName.objects.filter(**{f"{field_name}__trigram_similar": search_text})
        .values("entity_id")
        .annotate(best_similarity=Max(TrigramSimilarity(field_name, search_text)))
        .order_by("-best_similarity")
    )

    return [match["entity_id"] for match in names[: get_settings().top_k]]


def find_nearest_name_similarities(supplier_name: str) -> dict[str, float]:
    query_vector = embed_texts([supplier_name]).vectors[0]

    nearest_names = (
        EntityName.objects.filter(embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", query_vector))
        .order_by("distance")
        .values_list("entity_id", "distance")[: get_settings().top_k]
    )

    similarities: dict[str, float] = {}
    for entity_id, distance in nearest_names:
        similarities.setdefault(entity_id, 1 - distance)

    return similarities


def group_search_methods_by_entity(
    entity_ids_by_search_method: dict[SearchMethod, list[str]],
) -> dict[str, list[SearchMethod]]:
    search_methods_by_entity: dict[str, list[SearchMethod]] = {}
    for search_method, entity_ids in entity_ids_by_search_method.items():
        for entity_id in entity_ids:
            search_methods_by_entity.setdefault(entity_id, []).append(search_method)

    return search_methods_by_entity


def build_unscored_candidate(
    entity: Entity, search_methods: list[SearchMethod], vector_similarity: float | None
) -> UnscoredCandidate:
    names = [CandidateName(name_raw=name.name_raw, name_norm=name.name_norm) for name in entity.names.all()]
    return UnscoredCandidate(
        id=entity.id,
        name=entity.name,
        entity_type=entity.entity_type,
        countries=entity.countries,
        datasets=entity.datasets,
        programs=entity.programs,
        sanctions=entity.sanctions,
        last_change=entity.last_change,
        names=names,
        search_methods=search_methods,
        vector_similarity=vector_similarity,
    )


def find_entity_candidates(supplier: ScreeningInput, normalized_name: str) -> list[UnscoredCandidate]:
    vector_similarities = find_nearest_name_similarities(supplier.name)
    entity_ids_by_search_method: dict[SearchMethod, list[str]] = {
        "reg_no": find_registration_number_ids(supplier.reg_no),
        "trigram": find_similar_name_ids("name_norm", normalized_name),
        "phonetic": find_similar_name_ids("phonetic", build_phonetic_key(normalized_name)),
        "vector": list(vector_similarities),
    }

    for search_method, entity_ids in entity_ids_by_search_method.items():
        log.info("%s search found %d entities", search_method, len(entity_ids))

    search_methods_by_entity = group_search_methods_by_entity(entity_ids_by_search_method)
    log.info("searches found %d distinct entities", len(search_methods_by_entity))

    entities = Entity.objects.filter(id__in=search_methods_by_entity).prefetch_related("names")

    return [
        build_unscored_candidate(entity, search_methods_by_entity[entity.id], vector_similarities.get(entity.id))
        for entity in entities
    ]


def find_token_weights(normalized_name: str) -> dict[str, float]:
    tokens = normalized_name.split()

    count_expressions = {"total": Count("id")}
    for token in tokens:
        count_expressions[f"token_{token}"] = Count("id", filter=Q(name_norm__regex=rf"\m{token}\M"))

    counts = EntityName.objects.aggregate(**count_expressions)
    log.info("counted %d names for %d tokens", counts["total"], len(tokens))

    token_weights = {token: math.log((counts["total"] + 1) / (1 + counts[f"token_{token}"])) for token in tokens}
    log.debug("token weights: %s", token_weights)

    return token_weights
