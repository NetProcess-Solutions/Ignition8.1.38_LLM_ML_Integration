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
    front_step: int | None = None
    target_specs: dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Query anchor (design §3.1)
# -----------------------------------------------------------------------------

AnchorType = Literal["past_event", "current_state", "pattern"]
AnchorStatus = Literal[
    "resolved",
    "needs_clarification_enumerated",
    "needs_clarification_open",
    "needs_clarification_scoped",
]


class ClarificationOption(BaseModel):
    """One enumerated candidate the user may pick to disambiguate the anchor."""
    label: str
    anchor_event_id: str | None = None
    anchor_run_id: str | None = None
    anchor_time: datetime | None = None
    note: str | None = None


class QueryAnchor(BaseModel):
    """
    Structured parse of the user query that drives anchor-conditional
    evidence-bucket assembly. See design §3.1–3.3.
    """
    model_config = ConfigDict(extra="forbid")

    anchor_type: AnchorType
    anchor_time: datetime | None = None
    anchor_event_id: str | None = None
    anchor_run_id: str | None = None
    style_scope: str | None = None
    failure_mode_scope: str | None = None
    equipment_scope: list[str] = Field(default_factory=list)
    anchor_status: AnchorStatus = "resolved"
    clarification_prompt: str | None = None
    clarification_options: list[ClarificationOption] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Anchor-conditional tag evidence (design §3.3, §3.5)
# -----------------------------------------------------------------------------

TagClass = Literal[
    "setpoint_tracking", "oscillating_controlled",
    "process_following", "discrete_state",
]


class BaselineWindow(BaseModel):
    """
    Per-tag aggregate over a named evidence bucket. The bucket name is
    one of: pre_anchor_60m, pre_anchor_24h, normal_baseline_14d,
    last_4_runs, failure_mode_matched.
    """
    bucket: Literal[
        "pre_anchor_60m", "pre_anchor_24h", "normal_baseline_14d",
        "last_4_runs", "failure_mode_matched",
    ]
    window_start: datetime | None = None
    window_end: datetime | None = None
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    samples: list[float] = Field(default_factory=list)
    note: str | None = None


class TagBucketEvidence(BaseModel):
    """Full per-tag evidence rendering for a single anchor query."""
    name: str
    tag_class: TagClass
    target: float | None = None
    current: float | None = None
    baselines: list[BaselineWindow] = Field(default_factory=list)


class MatchedHistoryRun(BaseModel):
    """A prior run that matches (style, failure_mode) for the anchor."""
    run_id: UUID | None = None
    run_number: str | None = None
    failure_mode: str | None = None
    event_time: datetime | None = None
    pre_event_summary: dict[str, Any] = Field(default_factory=dict)


class CameraClipRef(BaseModel):
    """Symphony clip handle attached to an event in scope."""
    clip_id: UUID | None = None
    event_id: UUID | None = None
    event_type: str | None = None
    camera_id: str
    camera_location: str | None = None
    storage_handle: str | None = None
    clip_start: datetime | None = None
    clip_end: datetime | None = None
    extraction_status: str | None = None


class BucketExclusion(BaseModel):
    """Records a bucket that was deliberately not populated, with reason."""
    bucket: str
    reason: str  # e.g. "anchor_type=past_event excludes live state"


class CuratedContextPackage(BaseModel):
    """
    The structured, pre-digested plant context delivered into prompt assembly.

    THIS IS A CONTRACT: raw historian dumps never reach the LLM. Adding
    fields requires updating Ignition gateway-side code in lockstep.

    v1 fields (key_tags / tag_summaries / deviations / active_alarms /
    recipe) are retained so the existing Ignition gateway client keeps
    validating. v2 adds the parsed anchor, the five anchor-conditional
    evidence buckets per §3.3, attached camera clips, and explicit
    bucket-exclusion records.
    """
    model_config = ConfigDict(extra="forbid")

    snapshot_time: datetime
    line_id: str

    # ---- v1 flat snapshot (kept for backward compatibility) -------------
    key_tags: list[TagValue] = Field(default_factory=list)
    tag_summaries: list[TagSummaryStat] = Field(default_factory=list)
    deviations: list[TagDeviation] = Field(default_factory=list)
    active_alarms: list[ActiveAlarm] = Field(default_factory=list)
    recipe: RecipeContext | None = None
    historian_window_minutes: int = 60

    # ---- v2 anchor + buckets (server-populated when present) ------------
    anchor: QueryAnchor | None = None
    tag_evidence: list[TagBucketEvidence] = Field(default_factory=list)
    matched_history: list[MatchedHistoryRun] = Field(default_factory=list)
    attached_clips: list[CameraClipRef] = Field(default_factory=list)
    excluded_buckets: list[BucketExclusion] = Field(default_factory=list)


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
        # v1 names (kept as aliases for one release; v2 prefers the names below)
        "live_tag", "tag_summary", "tag_deviation", "active_alarm",
        "document_chunk", "downtime_event", "quality_result", "defect_event",
        "business_rule", "line_memory", "ml_prediction",
        # v2 provenance taxonomy (design §3.6)
        "LIVE_TAG", "HISTORIAN_STAT", "DEVIATION", "BASELINE_COMPARE",
        "MATCHED_HISTORY", "ALARM", "EVENT", "WORK_ORDER",
        "DOCUMENT", "CAMERA_CLIP", "RULE", "MEMORY", "ML_PREDICTION",
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
