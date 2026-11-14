"""Public complaint endpoints: list/filter/search, detail, create (auth), upvote (auth)."""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from lib.db import db
from lib.security import current_user, require_user
from models.complaints import (
    CATEGORIES,
    STATUSES,
    ComplaintCreate,
    ComplaintDetail,
    ComplaintOut,
    ComplaintPage,
    StatusEventOut,
    utcnow,
)

router = APIRouter(tags=["complaints"])

PHOTO_PROJECTION = {"photos": 0, "_id": 0, "location": 0}


def _aware(dt: datetime) -> datetime:
    """Motor hands naive UTC datetimes back from BSON — re-anchor them before serialising."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def complaint_out(doc: dict, voted_ids: set[str] | None = None) -> ComplaintOut:
    return ComplaintOut(
        id=doc["id"],
        title=doc["title"],
        description=doc["description"],
        category=doc["category"],
        status=doc["status"],
        state=doc["state"],
        district=doc["district"],
        town=doc["town"],
        locality=doc["locality"],
        address=doc.get("address", ""),
        lat=doc["lat"],
        lng=doc["lng"],
        photos=list(doc.get("photos", [])),
        reporter_id=doc["reporter_id"],
        reporter_name=doc["reporter_name"],
        upvote_count=doc.get("upvote_count", 0),
        viewer_has_voted=bool(voted_ids and doc["id"] in voted_ids),
        created_at=_aware(doc["created_at"]),
        updated_at=_aware(doc["updated_at"]),
        resolution_photo=doc.get("resolution_photo"),
        resolution_remark=doc.get("resolution_remark"),
        resolved_at=_aware(doc["resolved_at"]) if doc.get("resolved_at") else None,
    )


async def _voted_ids(user: Optional[dict], complaint_ids: list[str]) -> set[str]:
    if not user or not complaint_ids:
        return set()
    cursor = db.votes.find({"user_id": user["id"], "complaint_id": {"$in": complaint_ids}})
    return {v["complaint_id"] async for v in cursor}


def _base_filter(
    state: Optional[str], district: Optional[str], town: Optional[str], locality: Optional[str],
    category: Optional[str], status: Optional[str], q: Optional[str], reporter_id: Optional[str],
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for field, value in [
        ("state", state), ("district", district), ("town", town), ("locality", locality),
        ("category", category), ("status", status), ("reporter_id", reporter_id),
    ]:
        if value:
            query[field] = value
    if q:
        escaped = re.escape(q.strip())
        query["$or"] = [
            {"title": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
            {"locality": {"$regex": escaped, "$options": "i"}},
        ]
    return query


async def query_complaints(
    *,
    state: Optional[str] = None,
    district: Optional[str] = None,
    town: Optional[str] = None,
    locality: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    reporter_id: Optional[str] = None,
    sort: str = "new",
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
    radius_km: float = 3.0,
    skip: int = 0,
    limit: int = 60,
) -> tuple[list[dict], int]:
    """Shared list query for the public feed and the admin table. Returns (docs, total)."""
    base = _base_filter(state, district, town, locality, category, status, q, reporter_id)

    if near_lat is not None and near_lng is not None:
        geo_stage: dict[str, Any] = {
            "$geoNear": {
                "near": {"type": "Point", "coordinates": [near_lng, near_lat]},
                "distanceField": "distance_m",
                "maxDistance": max(100, int(radius_km * 1000)),
                "spherical": True,
                "query": base,
            }
        }
        count_pipeline = [geo_stage.copy(), {"$count": "n"}]
        count_result = await db.complaints.aggregate(count_pipeline).to_list(1)
        total = count_result[0]["n"] if count_result else 0
        pipeline = [
            geo_stage,
            {"$sort": {"distance_m": 1}},
            {"$skip": skip},
            {"$limit": limit},
            {"$project": {"photos": 0, "distance_m": 0}},
        ]
        docs = await db.complaints.aggregate(pipeline).to_list(limit)
        return docs, total

    sort_spec: list[tuple[str, int]]
    if sort == "top":
        sort_spec = [("upvote_count", -1), ("created_at", -1)]
    else:
        sort_spec = [("created_at", -1)]
    total = await db.complaints.count_documents(base)
    cursor = (
        db.complaints.find(base, PHOTO_PROJECTION)
        .sort(sort_spec)
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(limit), total


@router.get("/complaints", response_model=ComplaintPage)
async def list_complaints(
    state: Optional[str] = None,
    district: Optional[str] = None,
    town: Optional[str] = None,
    locality: Optional[str] = None,
    category: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    q: Optional[str] = None,
    sort: str = Query(default="new", pattern="^(new|top)$"),
    near_lat: Optional[float] = None,
    near_lng: Optional[float] = None,
    radius_km: float = Query(default=3.0, gt=0, le=50),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=200),
    user: Optional[dict] = Depends(current_user),
):
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown category")
    if status_filter and status_filter not in STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status")
    docs, total = await query_complaints(
        state=state, district=district, town=town, locality=locality,
        category=category, status=status_filter, q=q, sort=sort,
        near_lat=near_lat, near_lng=near_lng, radius_km=radius_km,
        skip=skip, limit=limit,
    )
    voted = await _voted_ids(user, [d["id"] for d in docs])
    return ComplaintPage(
        items=[complaint_out(d, voted) for d in docs],
        total=total,
    )


@router.get("/complaints/mine", response_model=ComplaintPage)
async def my_complaints(
    user: dict = Depends(require_user),
):
    docs, total = await query_complaints(reporter_id=user["id"], limit=200)
    return ComplaintPage(items=[complaint_out(d) for d in docs], total=total)


@router.get("/complaints/{complaint_id}", response_model=ComplaintDetail)
async def complaint_detail(
    complaint_id: str,
    user: Optional[dict] = Depends(current_user),
):
    doc = await db.complaints.find_one({"id": complaint_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Complaint not found")
    voted = await _voted_ids(user, [complaint_id])
    out = complaint_out(doc, voted)
    history_docs = await (
        db.status_history.find({"complaint_id": complaint_id}, {"_id": 0})
        .sort([("created_at", 1)])
        .to_list(100)
    )
    history = [
        StatusEventOut(
            id=h["id"], status=h["status"], remark=h["remark"],
            actor_name=h["actor_name"], created_at=_aware(h["created_at"]),
        )
        for h in history_docs
    ]
    return ComplaintDetail(**out.model_dump(), history=history)


@router.post("/complaints", response_model=ComplaintDetail, status_code=201)
async def create_complaint(input: ComplaintCreate, user: dict = Depends(require_user)):
    now = utcnow()
    complaint_id = str(uuid.uuid4())
    doc = {
        **input.model_dump(),
        "id": complaint_id,
        "status": "reported",
        "location": {"type": "Point", "coordinates": [input.lng, input.lat]},
        "reporter_id": user["id"],
        "reporter_name": user["name"],
        "upvote_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.complaints.insert_one(doc)
    initial_event = {
        "id": str(uuid.uuid4()),
        "complaint_id": complaint_id,
        "status": "reported",
        "remark": "Complaint received and awaiting municipal review.",
        "actor_name": user["name"],
        "created_at": now,
    }
    await db.status_history.insert_one(initial_event)
    return ComplaintDetail(
        **complaint_out(doc).model_dump(),
        history=[StatusEventOut(
            id=initial_event["id"], status="reported", remark=initial_event["remark"],
            actor_name=initial_event["actor_name"], created_at=now,
        )],
    )


@router.post("/complaints/{complaint_id}/upvote")
async def upvote_complaint(complaint_id: str, user: dict = Depends(require_user)):
    complaint = await db.complaints.find_one({"id": complaint_id}, {"id": 1, "upvote_count": 1})
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    if await db.votes.find_one({"complaint_id": complaint_id, "user_id": user["id"]}):
        raise HTTPException(status_code=409, detail="You have already supported this complaint")
    await db.votes.insert_one({
        "id": str(uuid.uuid4()),
        "complaint_id": complaint_id,
        "user_id": user["id"],
        "created_at": utcnow(),
    })
    await db.complaints.update_one({"id": complaint_id}, {"$inc": {"upvote_count": 1}})
    return {"upvote_count": complaint.get("upvote_count", 0) + 1, "viewer_has_voted": True}
