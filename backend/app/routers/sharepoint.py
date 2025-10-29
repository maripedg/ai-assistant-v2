from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.sharepoint import HistoryResponse, SyncRunRequest, SyncRunResponse
from backend.app.services.sharepoint_sync import SharePointSyncError, sharepoint_client
from backend.app.services.sync_orchestrator import run_sharepoint_sync

router = APIRouter(tags=["sharepoint"])
logger = logging.getLogger(__name__)


@router.post(
    "/sharepoint/sync/run",
    response_model=SyncRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a SharePoint sync run",
)
def run_sync(request: SyncRunRequest):
    try:
        result = run_sharepoint_sync(mode=request.mode, folder_name=request.folder_name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Manual SharePoint sync failed: %s", exc)
        raise HTTPException(status_code=502, detail="SharePoint sync failed") from exc

    return SyncRunResponse(**result)


@router.get(
    "/sharepoint/history",
    response_model=HistoryResponse,
    summary="Proxy SharePoint sync history summary",
)
def sharepoint_history() -> HistoryResponse:
    try:
        data = sharepoint_client.history_summary()
    except SharePointSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return HistoryResponse(data=data)
