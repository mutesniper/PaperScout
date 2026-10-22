from typing import Literal
from datetime import date
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

class AnalysisRequest(Idea):
    clarification: str = Field(default='', max_length=3000)

class KnowledgePlan(Plan):
    queries: list[str] = Field(min_length=1, max_length=1)

class KnowledgePoint(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    importance: Literal['core', 'optional']
    explanation: str = Field(min_length=1, max_length=800)
    goal_connection: str = Field(min_length=1, max_length=800)
    plan: KnowledgePlan

    @field_validator('plan')
    @classmethod
    def single_query(cls, value):
        if len(value.queries) != 1:
            raise ValueError('每个知识点需要一条聚焦检索词')
        return value

class GoalAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    goal: str = Field(min_length=1, max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    questions: list[str] = Field(default_factory=list, max_length=3)
    knowledge: list[KnowledgePoint] = Field(min_length=1, max_length=8)
    steps: list[str] = Field(min_length=1, max_length=8)
    alternatives: list[str] = Field(default_factory=list, max_length=4)

class SearchContext(BaseModel):
    goal: str = Field(min_length=1, max_length=3000)
    topic: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=800)

class SearchRequest(BaseModel):
    plan: Plan
    context: SearchContext | None = None
    sources: list[Source] = Field(default=['arxiv', 'semantic_scholar'], min_length=1, max_length=2)
    limit: int = Field(default=10, ge=1, le=100)
    sort: Literal['relevance', 'newest', 'oldest', 'citations'] = 'relevance'

class Paper(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    publication_date: date | None = None
    citation_count: int | None = Field(default=None, ge=0)
    citation_source: str | None = None
    source_ranks: dict[str, int] = Field(default_factory=dict)
    relevance_score: float | None = None
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
    candidate_count: int | None = None
    ranking_note: str = ''
    warnings: list[str] = Field(default_factory=list)
