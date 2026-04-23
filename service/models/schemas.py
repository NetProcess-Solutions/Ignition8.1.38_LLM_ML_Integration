"""
Pydantic request/response models.

The CuratedContextPackage is the formal contract between Ignition (which
gathers plant context) and the FastAPI service (which assembles prompts).
It enforces that raw historian dumps never reach the LLM directly.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# -----------------------------------------------------------------------------
# Curated context (sent FROM Ignition TO the service in /api/chat)
# -----------------------------------------------------------------------------

class TagValue(BaseModel):
    """A single key tag's current value with metadata."""
    name: str
    value: float | int | str | bool | None
    unit: str | None = None
    target: float | int | None = None
    quality: str | None = None  # 'good', 'bad', 'uncertain'


class TagSummaryStat(BaseModel):
    """Aggregate stats for a tag over a recent window."""
    name: str
    window_minutes: int
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    current: float | None = None
    trend: Literal["rising", "falling", "stable", "unknown"] = "unknown"


class TagDeviation(BaseModel):
    """A tag whose recent behavior is notably different from baseline."""
    name: str
    current: float | None = None
    baseline_mean: float | None = None
    baseline_std: float | None = None
    sigma_deviation: float | None = None
    pct_deviation: float | None = None
    direction: Literal["above", "below"] | None = None
    note: str | None = None


class ActiveAlarm(BaseModel):
    """A currently-active alarm."""
    source: str
    display_path: str | None = None
    priority: str
    state: str
    active_since: datetime | None = None
    label: str | None = None


class RecipeContext(BaseModel):
    """Current product/recipe context."""
    product_style: str | None = None
    product_family: str | None = None
    recipe_id: str | None = None
    target_specs: dict[str, Any] = Field(default_factory=dict)


class CuratedContextPackage(BaseModel):
    """
    The structured, pre-digested plant context Ignition sends to the service.

    THIS IS A CONTRACT: Ignition MUST NOT send raw historian dumps. It MUST
    pre-aggregate and curate. The service MUST NOT accept arbitrary blobs
    here. Adding fields requires updating both sides.
    """
    model_config = ConfigDict(extra="forbid")

    snapshot_time: datetime
    line_id: str
    key_tags: list[TagValue] = Field(default_factory=list)
    tag_summaries: list[TagSummaryStat] = Field(default_factory=list)
    deviations: list[TagDeviation] = Field(default_factory=list)
    active_alarms: list[ActiveAlarm] = Field(default_factory=list)
    recipe: RecipeContext | None = None
    historian_window_minutes: int = 60


# -----------------------------------------------------------------------------
# /api/chat
# -----------------------------------------------------------------------------

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    line_id: str = Field(min_length=1, max_length=50)
    live_context: CuratedContextPackage
    conversation_id: UUID | None = None  # for continuing existing conversation


class SourceCitation(BaseModel):
    """A single source cited in an assistant response."""
    id: str  # display id like "1", "2"
    type: Literal[
        "live_tag", "tag_summary", "tag_deviation", "active_alarm",
        "document_chunk", "downtime_event", "quality_result", "defect_event",
        "business_rule", "line_memory", "ml_prediction"
    ]
    title: str
    excerpt: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    message_id: UUID
    conversation_id: UUID
    response: str
    sources: list[SourceCitation]
    confidence: Literal["confirmed", "likely", "hypothesis", "insufficient_evidence"]
    context_summary: dict[str, int]
    processing_time_ms: int
    prompt_version: str
    model_name: str


# -----------------------------------------------------------------------------
# /api/feedback
# -----------------------------------------------------------------------------

FeedbackSignalType = Literal[
    "usefulness", "correctness", "completeness", "source_relevance",
    "root_cause_confirmed", "root_cause_rejected",
    "recommendation_acted_on", "recommendation_ignored",
    "recommendation_helped", "recommendation_did_not_help",
]
FeedbackSignalValue = Literal["positive", "negative", "neutral"]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    user_id: str
    signal_type: FeedbackSignalType
    signal_value: FeedbackSignalValue
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    feedback_id: UUID
    accepted: bool


# -----------------------------------------------------------------------------
# /api/corrections
# -----------------------------------------------------------------------------

CorrectionType = Literal[
    "factual_error", "wrong_root_cause", "missing_context",
    "wrong_equipment", "outdated_info", "misleading_conclusion", "other",
]


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    user_id: str
    correction_type: CorrectionType
    original_claim: str | None = Field(default=None, max_length=2000)
    corrected_claim: str = Field(min_length=1, max_length=4000)
    supporting_evidence: str | None = Field(default=None, max_length=4000)


class CorrectionResponse(BaseModel):
    correction_id: UUID
    accepted: bool


# -----------------------------------------------------------------------------
# /api/outcomes
# -----------------------------------------------------------------------------

class OutcomeLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    outcome_type: Literal["quality_result", "defect_event", "downtime_event", "resolution"]
    outcome_id: UUID
    outcome_table: Literal["quality_results", "defect_events", "downtime_events"]
    alignment: Literal["confirmed", "contradicted", "partial", "unrelated"]
    linked_by: str
    notes: str | None = Field(default=None, max_length=2000)


class OutcomeLinkResponse(BaseModel):
    linkage_id: UUID
    accepted: bool


# -----------------------------------------------------------------------------
# /api/health
# -----------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    database: bool
    embedding_model: bool
    llm_provider: str
    version: str = "0.1.0"
