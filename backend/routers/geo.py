"""Nominatim (OpenStreetMap) geocoder proxy — locality search and reverse geocoding.

The backend is the single caller of Nominatim: it sends the required identifying
User-Agent, serialises requests at least 1.1 s apart (usage policy: max 1 req/s),
and caches recent answers so repeated searches cost nothing. The frontend only ever
hits these /api routes.
"""

import asyncio
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/geo", tags=["geo"])

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
HEADERS = {
    "User-Agent": "CivicConnect/1.0 (civic issue reporting platform; contact@civicconnect.app)",
    "Accept-Language": "en-IN,en",
}
_lock = asyncio.Lock()
_last_call = 0.0
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 600.0


async def _nominatim(endpoint: str, params: dict[str, Any]) -> Any:
    global _last_call
    key = endpoint + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    async with _lock:
        gap = 1.1 - (time.monotonic() - _last_call)
        if gap > 0:
            await asyncio.sleep(gap)
        _last_call = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(f"{NOMINATIM_URL}/{endpoint}", params=params, headers=HEADERS)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Location service is unreachable") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Location service returned an error")
    data = resp.json()
    _cache[key] = (time.monotonic(), data)
    return data


def _simplify(raw: dict) -> dict:
    address = raw.get("address", {}) or {}
    return {
        "display_name": raw.get("display_name", ""),
        "lat": float(raw["lat"]),
        "lng": float(raw["lon"]),
        "address": {
            "state": address.get("state"),
            "district": address.get("state_district") or address.get("district"),
            "city": address.get("city") or address.get("town") or address.get("village"),
            "suburb": address.get("suburb") or address.get("neighbourhood") or address.get("quarter"),
            "road": address.get("road"),
            "postcode": address.get("postcode"),
        },
    }


@router.get("/search")
async def search(q: str = Query(min_length=3, max_length=120)):
    data = await _nominatim(
        "search",
        {
            "q": q,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 8,
            "countrycodes": "in",
        },
    )
    return {"results": [_simplify(r) for r in data]}


@router.get("/reverse")
async def reverse(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
):
    data = await _nominatim(
        "reverse",
        {"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 18},
    )
    if not data or data.get("error"):
        return {"result": None}
    return {"result": _simplify(data)}
