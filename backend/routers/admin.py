"""Admin endpoints — every route requires the `admin` role (RBAC, enforced server-side)."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from lib.db import db
from lib.security import require_admin
from models.complaints import (
    STATUSES,
    AdminStats,
    ComplaintDetail,
    ComplaintPage,
    DailyCount,
    StatusUpdateIn,
    utcnow,
)
from routers.complaints import complaint_detail, complaint_out, query_complaints

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/stats", response_model=AdminStats)
async def admin_stats():
    total = await db.complaints.count_documents({})

    by_status = {s: 0 for s in STATUSES}
    async for row in db.complaints.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        if row["_id"] in by_status:
            by_status[row["_id"]] = row["n"]

    by_category: dict[str, int] = {}
    async for row in db.complaints.aggregate([{"$group": {"_id": "$category", "n": {"$sum": 1}}}]):
        by_category[row["_id"]] = row["n"]

    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = day_start - timedelta(days=13)
    daily_counts: dict[str, int] = {}
    async for row in db.complaints.aggregate(
        [
            {"$match": {"created_at": {"$gte": window_start}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "n": {"$sum": 1}}},
        ]
    ):
        daily_counts[row["_id"]] = row["n"]
    daily = []
    for offset in range(14):  # oldest → newest, left to right on the chart
        day = window_start + timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        daily.append(DailyCount(date=key, count=daily_counts.get(key, 0)))

    return AdminStats(
        total=total,
        pending=by_status["reported"] + by_status["under_review"] + by_status["assigned"],
        in_progress=by_status["in_progress"],
        resolved=by_status["resolved"],
        rejected=by_status["rejected"],
        by_status=by_status,
        by_category=by_category,
        daily=daily,
    )


@router.get("/complaints", response_model=ComplaintPage)
async def admin_list_complaints(
    state: str | None = None,
    district: str | None = None,
    town: str | None = None,
    locality: str | None = None,
    category: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    docs, total = await query_complaints(
        state=state, district=district, town=town, locality=locality,
        category=category, status=status_filter, q=q, skip=skip, limit=limit,
    )
    return ComplaintPage(items=[complaint_out(d) for d in docs], total=total)


@router.get("/complaints/{complaint_id}", response_model=ComplaintDetail)
async def admin_complaint_detail(complaint_id: str, admin: dict = Depends(require_admin)):
    return await complaint_detail(complaint_id, admin)


@router.put("/complaints/{complaint_id}/status", response_model=ComplaintDetail)
async def update_status(
    complaint_id: str,
    input: StatusUpdateIn,
    admin: dict = Depends(require_admin),
):
    doc = await db.complaints.find_one({"id": complaint_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Complaint not found")
    if doc["status"] == input.status:
        raise HTTPException(status_code=400, detail="Complaint is already in this status")
    if input.status != "resolved" and len(input.remark.strip()) < 3:
        raise HTTPException(status_code=422, detail="Add a remark of at least 3 characters")

    now = utcnow()
    update_fields = {"status": input.status, "updated_at": now}
    if input.status == "resolved":
        # Evidence and status are committed together in Mongo, so a failed write
        # cannot leave a resolved complaint without its after photo.
        update_fields.update({
            "resolution_photo": input.resolution_photo,
            "resolution_remark": input.resolution_remark.strip() if input.resolution_remark else None,
            "resolved_at": now,
        })
    result = await db.complaints.update_one(
        {"id": complaint_id, "status": doc["status"]},
        {"$set": update_fields},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Complaint status changed by another administrator")
    await db.status_history.insert_one({
        "id": str(uuid.uuid4()),
        "complaint_id": complaint_id,
        "status": input.status,
        "remark": (input.resolution_remark or input.remark).strip() or "Resolved with photographic evidence.",
        "actor_name": admin["name"],
        "created_at": now,
    })
    return await complaint_detail(complaint_id, admin)
