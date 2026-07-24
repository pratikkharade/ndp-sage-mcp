"""Unit tests for Google Drive configuration, upload metadata, and sharing."""

from __future__ import annotations

import asyncio
import json

import pytest

from ndp import drive as drive_module
from ndp.drive import DRIVE_SCOPES, GoogleDriveClient, GoogleDriveError


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    def __init__(self):
        self.create_kwargs = None
        self.get_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return FakeRequest({"id": "drive-file-1"})

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return FakeRequest(
            {
                "id": "drive-file-1",
                "name": "measurements.csv",
                "mimeType": "text/csv",
                "size": "24",
                "md5Checksum": "abc123",
                "webViewLink": "https://drive.google.com/view/drive-file-1",
                "webContentLink": "https://drive.google.com/download/drive-file-1",
            }
        )


class FakePermissions:
    def __init__(self):
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return FakeRequest({"id": "permission-1"})


class FakeDriveService:
    def __init__(self):
        self.files_api = FakeFiles()
        self.permissions_api = FakePermissions()

    def files(self):
        return self.files_api

    def permissions(self):
        return self.permissions_api


def test_upload_returns_download_link_and_creates_public_reader(tmp_path):
    source = tmp_path / "measurements.csv"
    source.write_text("timestamp,value\n1,2\n")
    service = FakeDriveService()
    client = GoogleDriveClient(folder_id="folder-1", service=service)

    uploaded = asyncio.run(
        client.upload_file(str(source), drive_name="measurements.csv")
    )

    assert uploaded.file_id == "drive-file-1"
    assert uploaded.download_url == "https://drive.google.com/download/drive-file-1"
    assert uploaded.view_url == "https://drive.google.com/view/drive-file-1"
    assert uploaded.size == 24
    assert service.files_api.create_kwargs["body"] == {
        "name": "measurements.csv",
        "parents": ["folder-1"],
    }
    assert service.files_api.create_kwargs["supportsAllDrives"] is True
    assert service.permissions_api.create_kwargs["body"] == {
        "type": "anyone",
        "role": "reader",
    }


def test_restricted_visibility_does_not_create_permission(tmp_path):
    source = tmp_path / "measurements.csv"
    source.write_text("value\n1\n")
    service = FakeDriveService()
    client = GoogleDriveClient(
        folder_id="folder-1",
        visibility="restricted",
        service=service,
    )

    asyncio.run(client.upload_file(str(source)))

    assert service.permissions_api.create_kwargs is None


def test_domain_visibility_requires_domain():
    with pytest.raises(GoogleDriveError, match="GOOGLE_DRIVE_DOMAIN"):
        GoogleDriveClient(folder_id="folder-1", visibility="domain")


def test_folder_id_is_required(monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)

    with pytest.raises(GoogleDriveError, match="GOOGLE_DRIVE_FOLDER_ID"):
        GoogleDriveClient()


def test_oauth_token_without_type_uses_authorized_user_loader(
    monkeypatch, tmp_path
):
    token_file = tmp_path / "google-drive-token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "access-token",
                "refresh_token": "refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id.apps.googleusercontent.com",
                "client_secret": "client-secret",
                "scopes": list(DRIVE_SCOPES),
            }
        )
    )
    captured = {}

    def fake_build(api, version, *, credentials, cache_discovery):
        captured["api"] = api
        captured["version"] = version
        captured["credentials"] = credentials
        captured["cache_discovery"] = cache_discovery
        return object()

    monkeypatch.setattr("googleapiclient.discovery.build", fake_build)
    client = GoogleDriveClient(
        folder_id="folder-1",
        credentials_file=str(token_file),
    )

    service = client._get_service()

    assert service is client._service
    assert captured["api"] == "drive"
    assert captured["version"] == "v3"
    assert captured["credentials"].refresh_token == "refresh-token"
    assert set(captured["credentials"].scopes) == set(DRIVE_SCOPES)
