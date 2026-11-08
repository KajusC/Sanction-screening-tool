import itertools
import math

from sanction_check.db.models import EMBEDDING_DIMENSIONS, EntityName
from sanction_check.helpers.log import log
from sanction_check.helpers.openrouter import DENY_DATA_COLLECTION, build_openrouter_client
from sanction_check.schemas.loading_schema import EmbeddingBatch, EmbedReport
from sanction_check.settings import get_settings

EMBEDDING_BATCH_SIZE = 256
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
        model=get_settings().embedding_model, input=texts, dimensions=EMBEDDING_DIMENSIONS, extra_body=PROVIDER_EXTRA_BODY
    )

    return EmbeddingBatch(
        vectors=[truncate_and_normalize(item.embedding) for item in response.data],
        token_count=response.usage.total_tokens,
    )


def embed_missing_names() -> EmbedReport:
    names = (
        EntityName.objects.filter(embedding__isnull=True)
        .only("id", "name_raw")
        .iterator(chunk_size=EMBEDDING_BATCH_SIZE)
    )

    name_count = 0
    token_count = 0
    for batch in itertools.batched(names, EMBEDDING_BATCH_SIZE):
        embedded = embed_texts([name.name_raw for name in batch])
        for name, vector in zip(batch, embedded.vectors, strict=True):
            name.embedding = vector

        EntityName.objects.bulk_update(batch, ["embedding"])

        name_count += len(batch)
        token_count += embedded.token_count
        log.info("embedded %d names, %d tokens", name_count, token_count)

    return EmbedReport(name_count=name_count, token_count=token_count)
