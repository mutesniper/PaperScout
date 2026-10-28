from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Fragment(BaseModel):
    page: int = Field(ge=1)
    text: str


class Section(BaseModel):
    id: str
    title: str
    fragments: list[Fragment]


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(ge=1)
    quote: str = Field(min_length=10, max_length=500)


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1500)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class SummaryContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    points: list[Point] = Field(min_length=1, max_length=6)


class Summary(SummaryContent):
    section_id: str
    model: str
    created_at: str


class DocumentSummaryContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    abstract: str = Field(min_length=20, max_length=10000)
    evidence: list[Evidence] = Field(min_length=1, max_length=12)


class DocumentSummary(DocumentSummaryContent):
    model: str
    created_at: str
    scope: Literal['fulltext', 'abstract']


class Reading(BaseModel):
    id: str
    title: str
    kind: Literal['pdf', 'abstract']
    origin: Literal['upload', 'public', 'abstract']
    source_url: str | None = None
    created_at: str
    page_count: int
    sections: list[Section]
    warnings: list[str] = Field(default_factory=list)
    summaries: list[Summary] = Field(default_factory=list)
    document_summary: DocumentSummary | None = None


class ImportPaper(BaseModel):
    record_id: str = Field(min_length=1, max_length=100)
    paper_id: str = Field(min_length=1, max_length=500)
    mode: Literal['pdf', 'abstract']
