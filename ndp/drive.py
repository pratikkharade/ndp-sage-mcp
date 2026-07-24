"""Google Drive storage for local resources registered with NDP.

The client deliberately owns only the Drive-specific concerns: credentials,
uploading, permissions, and link discovery. Registration orchestration remains
in ``ndp.tools`` so previews stay read-only and uploads happen only at commit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The backend writes into an administrator-selected folder rather than one
# selected interactively through Google Picker. Full Drive scope is therefore
# required; practical access is constrained by the service account's membership.
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive",)
VALID_VISIBILITIES = ("anyone", "domain", "restricted")


class GoogleDriveError(Exception):
    """Raised when Drive configuration or an API operation fails."""


@dataclass(frozen=True)
class DriveUpload:
    """Drive metadata retained in a staged registration for safe retries."""

    file_id: str
    download_url: str
    view_url: Optional[str] = None
    name: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    md5_checksum: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class GoogleDriveClient:
    """Small async facade over the synchronous Google Drive Python client."""

    def __init__(
        self,
        *,
        folder_id: Optional[str] = None,
        visibility: Optional[str] = None,
        domain: Optional[str] = None,
        credentials_file: Optional[str] = None,
        service: Any = None,
    ):
        self.folder_id = (folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
        self.visibility = (
            visibility or os.getenv("GOOGLE_DRIVE_VISIBILITY") or "anyone"
        ).strip().lower()
        self.domain = (domain or os.getenv("GOOGLE_DRIVE_DOMAIN") or "").strip() or None
        self.credentials_file = (
            credentials_file
            or os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or ""
        ).strip() or None
        self._service = service

        if not self.folder_id:
            raise GoogleDriveError(
                "GOOGLE_DRIVE_FOLDER_ID is not set; configure the destination "
                "folder before registering local files."
            )
        if self.visibility not in VALID_VISIBILITIES:
            raise GoogleDriveError(
                f"GOOGLE_DRIVE_VISIBILITY={self.visibility!r} is invalid; "
                f"use one of {VALID_VISIBILITIES}."
            )
        if self.visibility == "domain" and not self.domain:
            raise GoogleDriveError(
                "GOOGLE_DRIVE_DOMAIN is required when "
                "GOOGLE_DRIVE_VISIBILITY=domain."
            )

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        try:
            import google.auth
            from google.oauth2.credentials import Credentials as UserCredentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GoogleDriveError(
                "Google Drive dependencies are not installed; install "
                "google-api-python-client and google-auth."
            ) from exc

        try:
            if self.credentials_file:
                credential_data = json.loads(
                    Path(self.credentials_file).expanduser().read_text()
                )
                if (
                    not credential_data.get("type")
                    and credential_data.get("refresh_token")
                    and credential_data.get("client_id")
                    and credential_data.get("client_secret")
                ):
                    # Credentials.to_json() emits authorized-user fields without
                    # the top-level ``type`` required by google.auth's generic
                    # loader. Use the OAuth-specific loader for that format.
                    credentials = UserCredentials.from_authorized_user_file(
                        self.credentials_file,
                        scopes=DRIVE_SCOPES,
                    )
                else:
                    credentials, _ = google.auth.load_credentials_from_file(
                        self.credentials_file,
                        scopes=DRIVE_SCOPES,
                    )
            else:
                credentials, _ = google.auth.default(scopes=DRIVE_SCOPES)
            self._service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )
        except Exception as exc:
            raise GoogleDriveError(f"Could not initialize Google Drive: {exc}") from exc
        return self._service

    async def upload_file(
        self, local_path: str, *, drive_name: Optional[str] = None
    ) -> DriveUpload:
        """Upload a local file and return links suitable for NDP metadata."""
        return await asyncio.to_thread(
            self._upload_file_sync,
            local_path,
            drive_name,
        )

    def _upload_file_sync(
        self, local_path: str, drive_name: Optional[str] = None
    ) -> DriveUpload:
        path = Path(local_path).expanduser().resolve()
        if not path.is_file():
            raise GoogleDriveError(f"Local resource does not exist or is not a file: {path}")

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise GoogleDriveError(
                "Google Drive dependencies are not installed; install "
                "google-api-python-client and google-auth."
            ) from exc

        service = self._get_service()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metadata: Dict[str, Any] = {
            "name": drive_name or path.name,
            "parents": [self.folder_id],
        }
        media = MediaFileUpload(
            str(path),
            mimetype=mime_type,
            resumable=True,
        )
        fields = (
            "id,name,mimeType,size,md5Checksum,webViewLink,webContentLink"
        )

        file_id: Optional[str] = None
        try:
            created = (
                service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields=fields,
                    supportsAllDrives=True,
                )
                .execute()
            )
            file_id = created.get("id")
            if not file_id:
                raise GoogleDriveError("Drive upload succeeded without returning a file ID.")

            permission = self._permission_body()
            if permission is not None:
                (
                    service.permissions()
                    .create(
                        fileId=file_id,
                        body=permission,
                        fields="id",
                        supportsAllDrives=True,
                    )
                    .execute()
                )

            file_data = (
                service.files()
                .get(
                    fileId=file_id,
                    fields=fields,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except GoogleDriveError:
            raise
        except Exception as exc:
            raise GoogleDriveError(f"Could not upload {path.name!r} to Drive: {exc}") from exc

        download_url = file_data.get("webContentLink")
        if not download_url:
            raise GoogleDriveError(
                f"Drive file {file_id} has no webContentLink; upload the resource "
                "as a binary file rather than a Google Workspace document."
            )

        logger.info("Uploaded local resource %s to Drive file %s", path, file_id)
        size = file_data.get("size")
        return DriveUpload(
            file_id=file_id,
            download_url=download_url,
            view_url=file_data.get("webViewLink"),
            name=file_data.get("name"),
            mime_type=file_data.get("mimeType"),
            size=int(size) if size not in (None, "") else None,
            md5_checksum=file_data.get("md5Checksum"),
        )

    def _permission_body(self) -> Optional[Dict[str, str]]:
        if self.visibility == "restricted":
            return None
        if self.visibility == "domain":
            return {"type": "domain", "role": "reader", "domain": str(self.domain)}
        return {"type": "anyone", "role": "reader"}
