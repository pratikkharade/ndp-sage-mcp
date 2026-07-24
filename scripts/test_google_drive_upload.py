#!/usr/bin/env python3
"""Upload a zero-byte CSV to the configured Google Drive folder.

This is an explicit live smoke test: it writes one real file to Drive and
leaves it there so the operator can confirm its folder and sharing behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ndp.drive import GoogleDriveClient, GoogleDriveError


def _default_name() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"ndp-drive-empty-smoke-test-{timestamp}.csv"


async def _upload(name: str) -> int:
    try:
        client = GoogleDriveClient()
        with tempfile.NamedTemporaryFile(suffix=".csv") as empty_csv:
            uploaded = await client.upload_file(
                empty_csv.name,
                drive_name=name,
            )
    except GoogleDriveError as exc:
        print(f"Drive smoke test FAILED: {exc}", file=sys.stderr)
        return 1

    print("Drive smoke test PASSED")
    print(f"  file id:       {uploaded.file_id}")
    print(f"  uploaded name: {uploaded.name or name}")
    print(f"  size:          {uploaded.size if uploaded.size is not None else 0} bytes")
    print(f"  view URL:      {uploaded.view_url or '(not returned)'}")
    print(f"  download URL:  {uploaded.download_url}")
    print("The test file was intentionally left in Drive; delete it manually when done.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload a real zero-byte CSV using the Drive settings in the environment."
        )
    )
    parser.add_argument(
        "--name",
        default=_default_name(),
        help="Drive filename for the test CSV.",
    )
    args = parser.parse_args()
    return asyncio.run(_upload(args.name))


if __name__ == "__main__":
    raise SystemExit(main())
