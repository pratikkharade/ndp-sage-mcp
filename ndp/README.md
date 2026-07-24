# NDP extension for the Sage MCP server

Purely additive. Drop this folder at `sage_mcp_server/ndp/`. Nothing in the
existing Sage modules is modified.

## Wiring

`server.py` builds the FastMCP instance and calls each tool module's
`register()`. Add one line alongside the existing ones:

```python
from .ndp import tools as ndp_tools

ndp_tools.register(mcp, data_service)
```

If you cannot edit `server.py`, run a wrapper entrypoint instead:

```python
# run_with_ndp.py  (new file, top level)
from sage_mcp_server.server import mcp, data_service
from sage_mcp_server.ndp import tools as ndp_tools

ndp_tools.register(mcp, data_service)

if __name__ == "__main__":
    mcp.run()
```

## Config

```bash
export NDP_API_URL=...      # NDP endpoint API base URL
export NDP_API_KEY=...      # bearer token (NDP_API_TOKEN also accepted)
export NDP_SERVER=local     # target catalog: 'local' (default) or 'pre_ckan'
```

`NDP_SERVER` decides where `ndp_register_*` writes. `local` targets this
endpoint's own catalog and keeps the data off the public platform; `pre_ckan`
targets the public National Data Platform catalog. Making a locally-registered
dataset public is a separate, explicit step (`ndp_publish_dataset`).

Requires `ndp-ep` (`pip install ndp-ep`). The client wraps the official
`ndp_ep.APIClient` rather than hand-rolling HTTP.

### Google Drive live smoke test

Once `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, and
`GOOGLE_DRIVE_VISIBILITY` are configured in `.env`, verify the destination
with a real zero-byte CSV upload:

```bash
./scripts/test-google-drive-upload.sh
```

The script prints the returned Drive view/download links and intentionally
leaves the timestamped test file in Drive for inspection.

## Tools

| Tool | Purpose |
|---|---|
| `ndp_list_organizations` | Pick a valid `owner_org` |
| `ndp_search_datasets` | Search the catalog |
| `ndp_register_local_path` | Register a file or folder |
| `ndp_register_from_sage` | Query Sage, register the result |
| `ndp_finalize_registration` | Apply edits/answers, commit |
| `ndp_publish_dataset` | Push local catalog entry to PRE-CKAN |

## Flow

```
ndp_register_from_sage(plugin="imagesampler", node_id="W023", time_range="-24h")
  → runs the Sage query, extracts Beehive URLs, builds provenance
  → returns PREVIEW + staged_id, writes nothing

ndp_finalize_registration(staged_id, title="...", confirm=True)
  → POST /dataset with all resources inline
  → returns dataset_id

ndp_publish_dataset(dataset_id, confirm=True)
  → POST /dataset/{id}/publish → PRE-CKAN
```

Three deliberate gates: dry-run preview, `confirm=True` to write locally,
`confirm=True` again to publish publicly.

## Design notes

**Registration is idempotent.** Following the NDP reference notebook: the
organization is created only if `list_organizations` doesn't already contain
it, and a name collision found via `search_datasets(keys=["name"])` triggers a
re-slug rather than a failure.

**Registration is one call.** `GeneralDatasetRequest` carries
`resources: List[ResourceRequest]`, so `POST /dataset` creates the dataset and
all its resources at once. The `/url` and `/s3` routes are single-resource
shortcuts that create their own package — not used here.

**Beehive URLs are registered by reference.** Nothing is copied. Sage storage
URLs are stable and unsigned; access is per-request Basic auth with a portal
token, so the URL is a permanent identifier and the credential stays with the
consumer. The generated `notes` includes the `curl -u` idiom.

**Extras are stringified.** `GeneralDatasetRequest.extras` allows arbitrary
values, but nested dicts round-trip lossily through CKAN's extras table, so
dicts and lists are JSON-serialized. Namespaced keys (`sage:vsn`,
`sage:query`) survive intact and stay legible in the catalog UI.

**Provenance is captured at query time.** `ndp_register_from_sage` runs the
Sage query itself rather than consuming a prior tool's output, so the filter
dict, resolved time window, node VSNs, and plugin names are recorded by code
rather than reconstructed from a text summary. This is why it takes query
parameters instead of a path.

**Ambiguity produces a question, not a guess.** Mixed file types in a folder,
a missing title, or public visibility with inferred metadata all return
`status="needs_input"` with the staged state intact. Answering is one call.

**Staging is ephemeral.** In-memory dict, 1-hour TTL, no persistence. It
exists to carry a prepared registration between preview and commit.

## Known gaps

- `parse_time_range` is resolved at registration time by trying
  `sage_mcp_server.utils`, then `..utils`, then `utils`, falling back to a
  local implementation. Check the log for the fallback warning on first run —
  if it fires, the import layout differs from what's assumed.
- `SageDataService.query_data` accepts `user_token` but never passes it to
  `sage_data_client`, so queries return public data only. Protected imagery
  will not appear in results at all.
- `scan_path` caps at 200 resources. Bundle mode is not implemented.
- Local files are registered as `file://` URIs. If NDP needs the bytes, add an
  upload path via `POST /s3` or CKAN's `resource_create` with an attachment.
