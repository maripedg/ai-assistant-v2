from __future__ import annotations

from typing import Any, Dict, Optional, Union

from app.services.api_client import feedback_list


def _coerce_rating_param(rating: Optional[Union[int, str]]) -> Optional[Union[int, str]]:
    if rating in (None, "", "all"):
        return None
    if isinstance(rating, str):
        rating_str = rating.strip().lower()
        if rating_str in {"like", "1", "+1", "positive"}:
            return 1
        if rating_str in {"dislike", "-1", "negative"}:
            return -1
        return rating
    try:
        value = int(rating)
    except (TypeError, ValueError):
        return None
    if value > 0:
        return 1
    if value < 0:
        return -1
    return None


def list_feedback(
    limit: int,
    offset: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    rating: Optional[Union[int, str]] = None,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch feedback entries from the backend API.

    Returns a mapping containing at least ``items`` (list) and ``total`` (int, when provided).
    """

    params: Dict[str, Any] = {
        "limit": max(1, int(limit or 1)),
        "offset": max(0, int(offset or 0)),
    }
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    rating_param = _coerce_rating_param(rating)
    if rating_param is not None:
        params["rating"] = rating_param

    user_param = (user or "").strip()
    if user_param:
        params["user"] = user_param

    response = feedback_list(**params)

    if isinstance(response, dict):
        items = response.get("items")
        if isinstance(items, list):
            total_raw = response.get("total", response.get("count"))
            try:
                total = int(total_raw)
            except (TypeError, ValueError):
                total = len(items)
            return {"items": items, "total": total}

        # Some APIs might nest results under an alternative key.
        results = response.get("results")
        if isinstance(results, list):
            return {
                "items": results,
                "total": int(response.get("total") or response.get("count") or len(results)),
            }

        # If the dict itself represents a single item, normalise to list.
        return {"items": [response], "total": 1}

    if isinstance(response, list):
        return {"items": response, "total": len(response)}

    return {"items": [], "total": 0}


def build_feedback_payload(
    *,
    user_id: Optional[int],
    session_id: str,
    rating: int,
    category: str,
    comment: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Prepare the keyword arguments used by chat feedback submissions.

    Comment is passed through verbatim (allowing empty strings) so the backend receives
    exactly what the user typed.
    """

    payload: Dict[str, Any] = {
        "session_id": session_id,
        "rating": rating,
        "category": category,
        "comment": comment,
        "metadata": metadata or {},
    }
    if user_id is not None:
        payload["user_id"] = user_id
    return payload
