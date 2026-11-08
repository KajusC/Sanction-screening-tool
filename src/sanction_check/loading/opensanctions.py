import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
from django.db import transaction

from sanction_check.db.models import BULK_BATCH_SIZE, Entity, EntityName, EntityType
from sanction_check.helpers.log import log
from sanction_check.matching.normalize import build_phonetic_key, normalize_identifiers, normalize_name
from sanction_check.schemas.loading_schema import LoadReport
from sanction_check.settings import get_settings

OPENSANCTIONS_COLUMNS = frozenset(
    {
        "id",
        "schema",
        "name",
        "aliases",
        "birth_date",
        "countries",
        "addresses",
        "identifiers",
        "sanctions",
        "phones",
        "emails",
        "program_ids",
        "dataset",
        "first_seen",
        "last_seen",
        "last_change",
    }
)
MERGED_LIST_COLUMNS = ("countries", "identifiers", "sanctions", "program_ids", "dataset", "source_datasets")
SOURCE_DATASET_PATTERN = re.compile(r"/(?:artifacts|datasets/[^/]+)/([^/]+)/")
DOWNLOAD_TIMEOUT_SECONDS = 60.0


def split_list(cell: str) -> list[str]:
    return [item for item in cell.split(";") if item]


def parse_utc(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp).replace(tzinfo=UTC)


def build_entity(row: dict[str, str]) -> Entity:
    return Entity(
        id=row["id"],
        entity_type=row["schema"],
        name=row["name"],
        name_norm=normalize_name(row["name"]),
        countries=[country.upper() for country in split_list(row["countries"])],
        identifiers=normalize_identifiers(split_list(row["identifiers"])),
        datasets=split_list(row["dataset"]),
        source_datasets=split_list(row["source_datasets"]),
        programs=split_list(row["program_ids"]),
        sanctions=split_list(row["sanctions"]),
        last_seen=parse_utc(row["last_seen"]),
        last_change=parse_utc(row["last_change"]),
    )


def build_entity_names(row: dict[str, str], entity_id: str) -> list[EntityName]:
    names_raw = [row["name"], *split_list(row["aliases"])]

    entity_names = []
    for name_raw in names_raw:
        normalized_name = normalize_name(name_raw)
        entity_names.append(
            EntityName(
                entity_id=entity_id,
                name_raw=name_raw,
                name_norm=normalized_name,
                phonetic=build_phonetic_key(normalized_name),
            )
        )

    return entity_names


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def read_source_text(source: str) -> str:
    if not is_url(source):
        return Path(source).read_text(encoding="utf-8")

    response = httpx.get(source, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()

    return response.content.decode("utf-8")


def build_source_dataset(source: str) -> str:
    if not is_url(source):
        return Path(source).stem

    match = SOURCE_DATASET_PATTERN.search(source)
    if match is None:
        raise ValueError(f"cannot find the dataset name in {source!r}")

    return match.group(1)


def read_rows(source: str) -> list[dict[str, str]]:
    supported_entity_types = set(EntityType.values)

    reader = csv.DictReader(io.StringIO(read_source_text(source), newline=""))
    missing_columns = OPENSANCTIONS_COLUMNS - set(reader.fieldnames or [])
    if missing_columns:
        raise ValueError(f"{source!r} is not in the OpenSanctions format, missing columns: {sorted(missing_columns)}")

    source_dataset = build_source_dataset(source)

    return [{**row, "source_datasets": source_dataset} for row in reader if row["schema"] in supported_entity_types]


def join_unique(*cells: str) -> str:
    return ";".join(dict.fromkeys(item for cell in cells for item in split_list(cell)))


def merge_rows(first: dict[str, str], second: dict[str, str]) -> dict[str, str]:
    latest = max(first, second, key=lambda row: row["last_change"])
    merged_columns = {column: join_unique(first[column], second[column]) for column in MERGED_LIST_COLUMNS}
    names = split_list(join_unique(first["aliases"], second["aliases"], first["name"], second["name"]))
    aliases = [name for name in names if name != latest["name"]]

    return latest | merged_columns | {"aliases": ";".join(aliases)}


def merge_duplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for row in rows:
        merged[row["id"]] = merge_rows(merged[row["id"]], row) if row["id"] in merged else row

    return list(merged.values())


def load_sources(sources: list[str] | None) -> LoadReport:
    rows = []
    for source in sources or get_settings().load_sources:
        source_rows = read_rows(source)
        log.info("read %d supported rows from %s", len(source_rows), source)
        rows.extend(source_rows)

    rows = merge_duplicate_rows(rows)

    entities = []
    entity_names = []
    for row in rows:
        entity = build_entity(row)
        entities.append(entity)
        entity_names.extend(build_entity_names(row, entity.id))

    with transaction.atomic():
        Entity.objects.all().delete()
        EntityName.objects.all().delete()
        Entity.objects.bulk_create(entities, batch_size=BULK_BATCH_SIZE)
        EntityName.objects.bulk_create(entity_names, batch_size=BULK_BATCH_SIZE)

    log.info("loaded %d entities and %d names", len(entities), len(entity_names))

    return LoadReport(entity_count=len(entities), name_count=len(entity_names))
