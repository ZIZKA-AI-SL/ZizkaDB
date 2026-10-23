# What ZizkaDB sends, and how to turn it off

This page lists every network call ZizkaDB makes on its own, outside of the requests you send it. It covers the open-source edition: the server image, the dashboard, the SDKs, the MCP server and the install scripts.

## Summary

| Component | Sends by default | To | Turn off |
|---|---|---|---|
| Server: API, database, single-container image | Nothing | n/a | n/a |
| Dashboard (in the visitor's browser) | Font requests | Google Fonts | Not configurable yet (see below) |
| Python SDK, TypeScript SDK, MCP server, integrations | One anonymous install ping per process | `https://db.zizka.ai/v1/telemetry` | `ZIZKADB_TELEMETRY=false` |
| `scripts/quickstart-remote.sh` and `scripts/setup-local.sh` | One anonymous install ping per run | `https://db.zizka.ai/v1/telemetry` | `ZIZKADB_TELEMETRY=false` |
| Optional features you enable | Your data, to the provider you configure | OpenAI or Anthropic | Leave the feature off |

## Server

The API and the single-container image make no outbound calls of their own.

- **Next.js telemetry:** the dashboard's server runs with `NEXT_TELEMETRY_DISABLED=1`.
- **Qdrant:** when Qdrant is configured, the API connects to it at startup, lists its collections and logs its version. Qdrant runs on your own network, and the single-container image does not use it.

## Dashboard

- **Google Fonts.** The dashboard's stylesheet loads the Inter and JetBrains Mono fonts from `fonts.googleapis.com`, so each visitor's browser requests them from Google. That request reveals the visitor's IP address to Google, as any third-party font does. Hosting the fonts in the image is planned.
- **No analytics.** The dashboard loads no analytics, tracking or advertising scripts.

## SDKs, MCP server and integrations

Each package sends one anonymous ping per process, the first time it is used, to count installs. It does not repeat on every call.

- **Packages:** the SDK client (`zizkadb-sdk`, Python and TypeScript) and the MCP server (`zizkadb-mcp`). The integrations use the SDK client, so they are counted through its ping; the bundled LangChain and CrewAI integrations and `zizkadb-livekit` also report their own package name.
- **Sent:**
  - A random install ID, generated once and stored in `~/.zizkadb/install_id`.
  - The package name and version.
  - The Python or Node version and the operating system.
  - Whether the client points at ZizkaDB Cloud or a self-hosted server.
- **Never sent:** API keys, agent names, event data or the server address.
- **Stored by the receiving server:** the fields above, a first-seen and last-seen time, a ping count, and a country code. The country code is derived from the request; the IP address itself is not stored.
- **Turn off:** set `ZIZKADB_TELEMETRY=false` (or `0`, `no`, `off`) in the environment of the process that uses the SDK.

## Install scripts

`scripts/quickstart-remote.sh` and `scripts/setup-local.sh` run `scripts/telemetry-install-ping.sh` once, after the stack is up. That ping sends:

- an install ID stored in `~/.zizkadb/install_id`
- `sdk=docker`
- the version label
- the operating system

To turn it off, run either script with `ZIZKADB_TELEMETRY=false`.

## Optional features

These features send data only when you turn them on and give them a key:

- **Embeddings and semantic search** (`EMBEDDINGS_ENABLED=true` with `OPENAI_API_KEY`). Event text is sent to the OpenAI embeddings API. The single-container image ships with embeddings off.
- **AI suggestions** (with `ANTHROPIC_API_KEY`). A summary of the agent's activity is sent to the Anthropic API, or to `ANTHROPIC_BASE_URL` if you set it.
- **Email** (with `EMAIL_HOST`/`EMAIL_USER`/`EMAIL_PASS`). Sign-in codes and notices go through the SMTP server you configure.

## Changes to this page

Any change to what ZizkaDB sends by default is listed here and in the CHANGELOG before it ships.
