from pydantic import BaseModel


class LoadReport(BaseModel):
    entity_count: int
    name_count: int


class EmbedReport(BaseModel):
    name_count: int
    token_count: int


class EmbeddingBatch(BaseModel):
    vectors: list[list[float]]
    token_count: int
