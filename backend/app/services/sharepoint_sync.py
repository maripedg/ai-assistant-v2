from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from backend.app import config as app_config

logger = logging.getLogger(__name__)


class SharePointSyncError(RuntimeError):
    pass


class SharePointSyncClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        site_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or app_config.sp_sync_base_url()).rstrip("/")
        self.site_key = site_key or app_config.sp_site_key()
        self._session = session or requests.Session()

    def list_site(self, site_key: Optional[str] = None) -> Dict[str, Any]:
        key = site_key or self.site_key
        url = f"{self.base_url}/list/{key}"
        return self._request("GET", url)

    def update(self, site_key: Optional[str] = None, folder: str = "rolling") -> Dict[str, Any]:
        key = site_key or self.site_key
        url = f"{self.base_url}/update/{key}/{folder}"
        return self._request("GET", url)

    def download(self, site_key: Optional[str] = None, folder: str = "rolling") -> Dict[str, Any]:
        key = site_key or self.site_key
        url = f"{self.base_url}/download/{key}/{folder}"
        return self._request("GET", url)

    def history_summary(self) -> Dict[str, Any]:
        url = f"{self.base_url}/history/summary"
        return self._request("GET", url)

    def _request(self, method: str, url: str) -> Dict[str, Any]:
        try:
            response = self._session.request(method, url, timeout=30)
        except Exception as exc:  # noqa: BLE001
            logger.error("SharePoint sync request failed: %s", exc)
            raise SharePointSyncError(str(exc)) from exc

        if not response.ok:
            logger.error(
                "SharePoint sync request returned %s | url=%s | body=%s",
                response.status_code,
                url,
                response.text,
            )
            raise SharePointSyncError(f"SharePoint sync service error {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            logger.error("Invalid JSON from SharePoint sync service: %s", exc)
            raise SharePointSyncError("Invalid response from SharePoint sync service") from exc


sharepoint_client = SharePointSyncClient()

__all__ = ["sharepoint_client", "SharePointSyncClient", "SharePointSyncError"]
