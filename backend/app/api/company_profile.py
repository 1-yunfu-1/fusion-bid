"""本地企业画像 API（不存储密钥、Cookie 或登录态）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.company import CompanyProfile

router = APIRouter(prefix="/company-profile", tags=["company-profile"])


class CompanyProfilePayload(BaseModel):
    name: str = Field(default="本地企业画像", max_length=256)
    product_capabilities: list[str] = Field(default_factory=list)
    service_regions: list[str] = Field(default_factory=list)
    qualifications: list[Any] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    delivery_constraints: list[str] = Field(default_factory=list)
    agent_capability: bool | None = None
    joint_venture_capability: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_company_profile(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.scalar(select(CompanyProfile).order_by(CompanyProfile.updated_at.desc()))
    if not row:
        return {"configured": False, **CompanyProfilePayload().model_dump()}
    return {
        "configured": True,
        "id": row.id,
        "name": row.name,
        **(row.profile_data or {}),
        "updated_at": row.updated_at.isoformat(),
    }


@router.put("")
async def put_company_profile(
    body: CompanyProfilePayload, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.scalar(select(CompanyProfile).order_by(CompanyProfile.updated_at.desc()))
    profile_data = body.model_dump(exclude={"name"})
    if row is None:
        row = CompanyProfile(name=body.name, profile_data=profile_data)
        db.add(row)
    else:
        row.name = body.name
        row.profile_data = profile_data
    await db.commit()
    await db.refresh(row)
    return {
        "configured": True,
        "id": row.id,
        "name": row.name,
        **(row.profile_data or {}),
        "updated_at": row.updated_at.isoformat(),
        "message": "企业画像已保存；可在公告详情中点击「重新分析」生成逐条匹配矩阵。",
    }
