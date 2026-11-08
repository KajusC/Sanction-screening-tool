from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from pgvector.django import HnswIndex, VectorField

EMBEDDING_DIMENSIONS = 1024
BULK_BATCH_SIZE = 5000


class EntityType(models.TextChoices):
    ORGANIZATION = "Organization"
    COMPANY = "Company"
    LEGAL_ENTITY = "LegalEntity"
    PUBLIC_BODY = "PublicBody"
    PERSON = "Person"


def text_array() -> ArrayField:
    return ArrayField(models.TextField(), default=list)


class Entity(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    entity_type = models.CharField(max_length=32, choices=EntityType.choices)
    name = models.TextField()
    name_norm = models.TextField()
    countries = text_array()
    identifiers = text_array()
    datasets = text_array()
    source_datasets = text_array()
    programs = text_array()
    sanctions = text_array()
    last_seen = models.DateTimeField()
    last_change = models.DateTimeField()

    class Meta:
        db_table = "entities"


class EntityName(models.Model):
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="names")
    name_raw = models.TextField()
    name_norm = models.TextField()
    phonetic = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True)

    class Meta:
        db_table = "entity_names"
        indexes = [
            GinIndex(
                fields=["name_norm"],
                name="entity_names_name_norm_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["phonetic"],
                name="entity_names_phonetic_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            HnswIndex(
                fields=["embedding"],
                name="entity_names_embedding_hnsw",
                opclasses=["vector_cosine_ops"],
            ),
        ]
