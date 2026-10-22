from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

Source = Literal['arxiv', 'semantic_scholar']

class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    queries: list[str] = Field(min_length=1, max_length=3)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)

    @field_validator('queries')
    @classmethod
    def clean_queries(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 250 for v in result):
            raise ValueError('每条检索词应为 1–250 字符')
        return result

    @model_validator(mode='after')
    def dates(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError('起始年份不能晚于结束年份')
        return self

class Idea(BaseModel):
    text: str = Field(min_length=2, max_length=3000)

class SearchRequest(BaseModel):
    plan: Plan
    sources: list[Source] = Field(default=['arxiv', 'semantic_scholar'], min_length=1, max_length=2)
    limit: int = Field(default=10, ge=1, le=20)

class Paper(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ''
    doi: str | None = None
    arxiv_id: str | None = None
    sources: list[str]
    url: str
    fulltext_url: str | None = None
    access: Literal['open_fulltext', 'open_preprint', 'abstract_only', 'unknown']
    evidence: str

class SourceResult(BaseModel):
    source: str
    status: Literal['ok', 'partial', 'error']
    count: int
    message: str = ''
    cached: bool = False

class SearchResult(BaseModel):
    id: str
    created_at: str
    request: SearchRequest
    papers: list[Paper]
    sources: list[SourceResult]
