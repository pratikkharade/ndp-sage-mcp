# Sage MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server for the
[Sage Grande Testbed](https://sagecontinuum.org/) — cyberinfrastructure for
AI@Edge. It exposes ~30 MCP tools, resources, and prompts for querying sensor
data, submitting edge-computing jobs, discovering plugins, and browsing Sage
documentation from any MCP-compatible client (Claude Desktop, Cursor,
custom agents, etc.).

Built on [FastMCP 2.10+](https://github.com/jlowin/fastmcp) and the
[MCP SDK 1.12+](https://github.com/modelcontextprotocol/python-sdk); supports
`stdio`, `sse`, and `streamable-http` transports.

---

## Choose your setup

There are three ways to run this — pick the one that fits.

| # | Path | Who it's for | You need |
|---|------|--------------|----------|
| 1 | [**Hosted (mcp.sagecontinuum.org)**](#1-hosted-mcpsagecontinuumorg) | Everyone. Zero install. | An MCP client + a Sage token. |
| 2 | [**Local — stdio**](#2-local--stdio-for-ide-clients) | IDE users who want to run the server themselves. | Python 3.11+ and this repo. |
| 3 | [**Local — HTTP**](#3-local--http-for-development-or-non-ide-clients) | Devs, custom agents, curl testing. | Python 3.11+ or Docker. |

All three expose the exact same tools. The only difference between hosted and
local is *how much data you can reach*: without Sage credentials, every tool
still runs but falls back to **public data only**. See
[Authentication & credentials](#authentication--credentials) for how to add
your token.

---

## 1. Hosted (mcp.sagecontinuum.org)

The server is already running at `https://mcp.sagecontinuum.org/mcp`. Point
any MCP client at it — no install needed.

**Cursor** (`~/.cursor/mcp.json`) or **Claude Desktop**
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "sage": {
      "url": "https://mcp.sagecontinuum.org/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_USERNAME:YOUR_ACCESS_TOKEN"
      }
    }
  }
}
```

Get `YOUR_ACCESS_TOKEN` from
<https://portal.sagecontinuum.org/account/access>. The `Bearer` value must be
in `username:token` form for anything beyond public data.

Restart your client, then ask natural questions:

> "Show me temperature readings from node W023 in the last hour."
> "Find nodes in Chicago with recent camera images."
> "What's the highest temperature recorded today across all nodes?"

---

## 2. Local — stdio (for IDE clients)

Run the server as a subprocess of your IDE. Nothing binds to a network port.
This is the most private mode — data and credentials never leave your machine.

**Install:**

```bash
git clone https://github.com/waggle-sensor/sage-mcp.git
cd sage-mcp
pip install -r requirements.txt
cp .env.example .env       # edit and add SAGE_USER / SAGE_PASS if you have them
```

**Wire it into Cursor/Claude Desktop:**

```json
{
  "mcpServers": {
    "sage-local": {
      "command": "/absolute/path/to/sage-mcp/scripts/run-local-stdio.sh",
      "env": {
        "SAGE_USER": "your-sage-username",
        "SAGE_PASS": "your-sage-access-token"
      }
    }
  }
}
```

`env:` values here override anything in `.env` — useful when you want
different tokens per IDE profile. Omit them entirely to run against public
data only.

---

## 3. Local — HTTP (for development or non-IDE clients)

Bind the server to a local port. Useful for `curl`, custom agents, browser
extensions, or anything that connects to a URL rather than spawning a
subprocess.

### Option A — bare Python

```bash
pip install -r requirements.txt
cp .env.example .env       # optional
./scripts/run-local-http.sh
```

Server is now on `http://127.0.0.1:8000/mcp`. Quick sanity check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### Option B — Docker

```bash
cp .env.example .env       # optional
docker compose up          # http://127.0.0.1:8000/mcp — laptop-safe default
```

The default `docker compose up` publishes only to `127.0.0.1`. To expose it
on all interfaces (cloud deployment), use the `cloud` profile:

```bash
docker compose --profile cloud up -d
```

For older MCP clients that only speak SSE:

```bash
docker compose --profile sse up   # http://127.0.0.1:8001/sse
```

### Point a client at your local server

```json
{
  "mcpServers": {
    "sage-local-http": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

---

## Authentication & credentials

The server never *requires* a Sage token — it just does less without one.

| Scenario | What works | What doesn't |
|----------|------------|--------------|
| No token | Public sensor data, node listings, docs, plugin discovery, image proxy for public images | Protected datasets, `/proxy/image` for restricted content, job submission (`sesctl` still needs a real token) |
| `username:token` provided | Everything the token's Sage account has access to | Anything not shared with your account |

**How to provide a token, in order of preference:**

1. **`.env` file** (`SAGE_USER=…` + `SAGE_PASS=…`) — picked up by both
   `./scripts/run-local-http.sh` and `./scripts/run-local-stdio.sh` and by
   `docker compose`.
2. **Per-request HTTP header** — for the hosted server or any HTTP transport:
   - `Authorization: Bearer username:token` (recommended)
   - `Authorization: Basic <base64(username:token)>`
   - `X-SAGE-Token: username:token`
3. **Query parameter** — `?token=username:token` as a last resort (shows up
   in server logs, so avoid for anything sensitive).

Get your token from
<https://portal.sagecontinuum.org/account/access>. Access to protected data
also requires signing the Sage Data Use Agreement.

---

## Configuration reference

Every option is an environment variable — see [`.env.example`](.env.example)
for the full list with comments.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_TRANSPORT` | `streamable-http` | `stdio` / `sse` / `streamable-http` / `http` (alias) |
| `MCP_HOST` | `0.0.0.0` | Bind address (network transports only) |
| `MCP_PORT` | `8000` | Bind port (network transports only) |
| `MCP_PATH` | `/mcp` | HTTP path (streamable-http only) |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SAGE_USER`, `SAGE_PASS` | — | Basic auth for `/proxy/image` |
| `SAGE_PROXY_BASE_URL` | `http://localhost:8000` | Public URL prefix in generated proxy links |
| `ADMIN_API_KEY` | — | Required to hit `/analytics/*` |
| `SAGE_MCP_SKIP_REGISTRY_REFRESH` | — | Skip ECR call on startup (offline/CI) |

---

## HTTP endpoints (non-MCP)

| Path | Auth | Purpose |
|------|------|---------|
| `GET /health` | none | Liveness / readiness probe |
| `GET /proxy/image?url=…` | `SAGE_USER`/`SAGE_PASS` or per-request token | Authenticated image proxy to `storage.sagecontinuum.org` |
| `GET /analytics/summary` | admin key | Aggregate usage counts |
| `GET /analytics/users` | admin key | Per-user stats |
| `GET /analytics/tools` | admin key | Per-tool stats |
| `GET /analytics/user/{id}` | admin key | One user + their tool usage |
| `GET /analytics/activity` | admin key | Recent activity feed (`?limit=`) |

Admin API key is accepted via `X-Admin-API-Key` header,
`Authorization: Bearer <key>`, or `?api_key=<key>`.

---

## Tools, resources & prompts

29 tools, 2 resources, 7 prompts. Highlights:

- **Sensor data:** `get_node_all_data`, `get_node_iio_data`,
  `get_environmental_summary`, `list_available_nodes`, `search_measurements`,
  `get_node_temperature`, `get_temperature_summary`
- **Node metadata:** `get_node_info`, `list_all_nodes`, `get_sensor_details`
- **Jobs:** `submit_sage_job`, `submit_plugin_job`, `submit_multi_plugin_job`,
  `check_job_status`, `query_job_data`, `force_remove_job`, `suspend_job`
- **Geography:** `get_nodes_by_location`, `get_measurement_stat_by_location`
- **Plugins:** `find_plugins_for_task`, `get_plugin_data`, `query_plugin_data_nl`,
  `create_plugin`
- **Images:** `get_cloud_images`, `get_image_data`, `get_image_proxy_url`
- **Docs:** `ask_sage_docs`, `sage_faq`, `search_sage_docs`
- **Resources:** `query://{plugin}`, `stats://temperature`
- **Prompts:** `getting_started_guide`, `plugin_development_guide`,
  `data_analysis_guide`, `troubleshooting_guide`, and three "suggest" prompts.

More docs in the `docs/` folder — [Getting Started](docs/GETTING_STARTED.md),
[Authentication](docs/AUTHENTICATION.md),
[Custom Functions](docs/CUSTOM_FUNCTIONS.md),
[Docker Deployment](docs/DOCKER_DEPLOY.md).

---

## Testing

```bash
pip install -r requirements.txt
python -m pytest tests/          # 61 tests, no network required
```

The test suite mocks `sage_data_client` and the ECR registry — nothing hits
Sage's servers during CI. Legacy interactive smoke scripts (`test_auth.py`,
`test_server.py`, `test_image_proxy.py`, ...) still work as manual tests;
they're excluded from `pytest` collection via `pytest.ini`.

---

## Extending

Add a tool by decorating a function in one of the modules under
`sage_mcp_server/tools/`:

```python
# sage_mcp_server/tools/sensor_tools.py

@mcp.tool
def my_custom_analysis(data_query: str, analysis_type: str = "basic") -> str:
    """Perform a custom analysis on Sage data."""
    df = data_service.query_data(...)
    return f"Analysis results: ..."
```

See [Custom Functions Guide](docs/CUSTOM_FUNCTIONS.md) for the full workflow
(fork → add → deploy).

---

## Project layout

```
sage_mcp.py                          # 30-line entrypoint; exposes `mcp`
sage_mcp_server/                     # FastMCP factory + services
├── server.py                        # build_server(), main()
├── auth.py                          # HTTP request auth extraction
├── analytics_service.py             # in-memory analytics
├── data_service.py                  # sage-data-client wrapper
├── docs_helper.py                   # docs search + FAQ
├── job_service.py                   # sesctl wrapper
├── job_templates.py                 # pre-baked plugin job templates
├── models.py                        # pydantic v2 domain models
├── plugin_generator.py              # cookiecutter-style plugin scaffolding
├── plugin_metadata.py               # ECR plugin registry
├── plugin_query_service.py          # NL plugin query
├── plugin_registry.py               # measurement/plugin catalog
├── utils.py                         # time parsing, timestamp formatting
└── tools/                           # MCP tools split by concern
    ├── sensor_tools.py
    ├── job_tools.py
    ├── geo_tools.py
    ├── plugin_tools.py
    ├── docs_tools.py
    ├── prompts.py
    └── http_routes.py               # /health, /analytics/*, /proxy/image
```

---

## License

MIT. See `LICENSE`.
