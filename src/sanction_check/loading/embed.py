import itertools
import math
from concurrent.futures import ThreadPoolExecutor

from django.db import connections

from sanction_check.db.models import EMBEDDING_DIMENSIONS, EntityName
from sanction_check.helpers.log import log
from sanction_check.helpers.openrouter import DENY_DATA_COLLECTION, build_openrouter_client
from sanction_check.schemas.loading_schema import EmbeddingBatch, EmbedReport
from sanction_check.settings import get_settings

EMBEDDING_BATCH_SIZE = 1024
EMBEDDING_WORKER_COUNT = 4
PROVIDER_EXTRA_BODY = {"provider": DENY_DATA_COLLECTION}


def truncate_and_normalize(vector: list[float]) -> list[float]:
    if len(vector) <= EMBEDDING_DIMENSIONS:
        return vector

    truncated = vector[:EMBEDDING_DIMENSIONS]
    length = math.sqrt(sum(value * value for value in truncated))

    return [value / length for value in truncated]


def embed_texts(texts: list[str]) -> EmbeddingBatch:
    client = build_openrouter_client()
    log.info("requesting embeddings for %d texts", len(texts))

    response = client.embeddings.create(
        model=get_settings().embedding_model,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
        extra_body=PROVIDER_EXTRA_BODY,
    )

    return EmbeddingBatch(
        vectors=[truncate_and_normalize(item.embedding) for item in response.data],
        token_count=response.usage.total_tokens,
    )


def embed_and_save_batch(batch: tuple[EntityName, ...]) -> EmbeddingBatch:
    try:
        embedded = embed_texts([name.name_raw for name in batch])
        for name, vector in zip(batch, embedded.vectors, strict=True):
            name.embedding = vector

        EntityName.objects.bulk_update(batch, ["embedding"])
    finally:
        connections.close_all()

    return embedded


def embed_missing_names() -> EmbedReport:
    missing_names = EntityName.objects.filter(embedding__isnull=True)
    missing_count = missing_names.count()
    names = missing_names.only("id", "name_raw").iterator(chunk_size=EMBEDDING_BATCH_SIZE)
    batches = itertools.batched(names, EMBEDDING_BATCH_SIZE, strict=False)

    name_count = 0
    token_count = 0
    with ThreadPoolExecutor(max_workers=EMBEDDING_WORKER_COUNT) as executor:
        for wave in itertools.batched(batches, EMBEDDING_WORKER_COUNT, strict=False):
            for batch, embedded in zip(wave, executor.map(embed_and_save_batch, wave), strict=True):
                name_count += len(batch)
                token_count += embedded.token_count

            log.info("embedded %d names, %d tokens", name_count, token_count)
            log.info(
                "embedding progress: %.2f%%, %d names remaining",
                name_count / missing_count * 100,
                missing_count - name_count,
            )

    return EmbedReport(name_count=name_count, token_count=token_count)
