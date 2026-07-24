# SciDX streaming tools

Additive `stream_*` MCP tools that turn the live SAGE feed into filtered,
derived Kafka streams registered at NDP. Layered onto the same FastMCP instance
as the Sage and NDP tools — **no existing file is modified**.

## The idea

SAGE publishes one HTTP SSE feed of *all* nodes. A **derived stream** is a
private Kafka topic fed by a background producer that reads that feed, applies
your filters, and forwards only matching rows. The topic is registered at NDP as
a `kafka` resource carrying its source lineage + filters, so it is discoverable
and consumable.

```
SAGE SSE  ──►  producer (filter)  ──►  derived Kafka topic  ──►  consumer
(all nodes)    in THIS process         registered at NDP         (snapshot)
```

## Tools — the lifecycle

| Tool | Purpose | Writes? |
|---|---|---|
| `stream_ensure_sage_source` | Idempotently register org/dataset/`SAGE Data` api_stream | `confirm=True` |
| `stream_profile_sage` | Sample the live feed → fields + observed values (so filters use real values, not guesses) | read-only |
| `stream_create_derived` | Compile filters → new Kafka topic + NDP resource + start fan-in | `confirm=True` |
| `stream_list` | List your derived streams | read-only |
| `stream_sample` | Consume a topic briefly → snapshot records | read-only |
| `stream_delete` | Stop producer, delete NDP resource + Kafka topic (frees the broker) | `confirm=True` |

Typical agent flow: `ensure → profile → create → sample → delete`.

## Design choices

- **SAGE plumbing is server config, not agent reasoning.** The SSE URL and field
  `mapping` are baked into `SAGE_SOURCE_TEMPLATE` (`client.py`). The agent only
  ever supplies **filters** and **descriptions** — it can't hallucinate a URL
  that would silently yield an empty stream.
- **`stream_profile_sage` is the linchpin.** The resource metadata gives field
  *names*; it does not give field *values*. Profiling samples the live feed so an
  agent can see `vsn ∈ {W06C, W029, …}` and filter on real values.
- **Session-scoped lifetime.** A derived producer runs as an in-process daemon
  thread inside this MCP server. The Kafka topic + NDP resource survive a
  restart, but the SAGE→Kafka fan-in stops — a topic is "live" only while this
  server is up and holds its producer. A durable, server-side runner
  (`scidx_streaming_v2.runtime.RemoteRunner`) is a skeleton today; that's the
  seam to implement for "create now, consume tomorrow from anywhere."

## Config

Reuses the NDP env vars, plus needs the streaming package installed:

```bash
NDP_API_URL=...     # NDP endpoint API base URL
NDP_API_KEY=...     # bearer token (NDP_API_TOKEN accepted too)
NDP_SERVER=local    # 'local' or 'pre_ckan'

pip install -e streaming_v2   # installs scidx_streaming_v2 + Kafka deps (see init_setup.sh)
```

## Run

Point your MCP launcher at the composition entrypoint that wires all three
families:

```bash
python sage_stream_mcp.py    # Sage + NDP + streaming
```

It imports `sage_ndp_mcp` (Sage + NDP) and attaches the `stream_*` tools to the
same cached server singleton. If `scidx_streaming_v2` is not installed, the
streaming tools are skipped with a loud stderr banner and the Sage + NDP tools
still work (set `STREAMING_REQUIRED=1` to make that a hard failure).
