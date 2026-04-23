"""POST /api/select_tags - pre-screen which tags to include in a chat query."""
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from routers.deps import require_api_key
from services.tag_selector import select_tags

router = APIRouter(tags=["select_tags"], dependencies=[Depends(require_api_key)])


class TagCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    category: str
    keywords: list[str] = Field(default_factory=list)
    core: bool = False


class SelectTagsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    line_id: str = Field(min_length=1, max_length=50)
    catalog: list[TagCatalogEntry] = Field(min_length=1, max_length=500)
    max_extra: int = Field(default=20, ge=0, le=100)


class SelectTagsResponse(BaseModel):
    selected_names: list[str]
    matched_categories: list[str]
    matched_zones: list[int]
    reason: str


@router.post("/select_tags", response_model=SelectTagsResponse)
async def select_tags_endpoint(req: SelectTagsRequest) -> SelectTagsResponse:
    catalog: list[dict[str, Any]] = [t.model_dump() for t in req.catalog]
    result = select_tags(req.query, catalog, max_extra=req.max_extra)
    return SelectTagsResponse(**result)
