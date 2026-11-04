import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import { codeInlineOnLight } from "@/lib/content-styles";
import { SiteNav } from "@/components/SiteNav";
import { MarketingFooter } from "@/components/marketing/MarketingFooter";
import { GITHUB_URL, FOUNDER_EMAIL } from "@/lib/constants";

export const metadata = {
  title: "Technical reference",
  description:
    "Architecture, APIs, data model, and integration guide for ZizkaDB — the operational database for AI agents.",
};

const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "problem", label: "Problem domain" },
  { id: "architecture", label: "Architecture" },
  { id: "data-model", label: "Data model" },
  { id: "capabilities", label: "Capabilities" },
  { id: "time-travel", label: "Time travel" },
  { id: "integrity", label: "Integrity & retention" },
  { id: "performance", label: "Performance" },
  { id: "security", label: "Security" },
  { id: "integration", label: "Integration" },
  { id: "api", label: "REST API" },
  { id: "deployment", label: "Deployment" },
  { id: "comparison", label: "Comparison" },
  { id: "licensing", label: "Licensing" },
  { id: "limits", label: "Plan limits" },
  { id: "faq", label: "FAQ" },
  { id: "contact", label: "Contact" },
] as const;

export default function TrustPage() {
  return (
    <div
      style={{
        fontFamily: "Inter, system-ui, sans-serif",
        color: "#111",
        background: "#fff",
        minHeight: "100vh",
      }}
    >
      <SiteNav active="trust" suffix="Technical" />

      <div style={{ display: "flex", maxWidth: 1100, margin: "0 auto" }}>
        <aside
          style={{
            width: 200,
            flexShrink: 0,
            padding: "32px 16px",
            position: "sticky",
            top: 56,
            height: "calc(100vh - 56px)",
            overflowY: "auto",
            borderRight: "1px solid #f0f0f0",
            display: "none",
          }}
          className="trust-sidebar"
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "#000000",
              letterSpacing: 1,
              textTransform: "uppercase",
              marginBottom: 10,
            }}
          >
            On this page
          </div>
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              style={{
                display: "block",
                fontSize: 13,
              color: "#000000",
                textDecoration: "none",
                padding: "6px 10px",
                borderRadius: 6,
                marginBottom: 2,
              }}
            >
              {s.label}
            </a>
          ))}
        </aside>

        <main
          style={{
            flex: 1,
            maxWidth: 780,
            padding: "48px 28px 96px",
            minWidth: 0,
          }}
        >
          <p style={{ fontSize: 12, color: "#000000", marginBottom: 8 }}>
            Technical reference · May 2026
          </p>
          <h1
            style={{
              fontSize: 34,
              fontWeight: 700,
              letterSpacing: -0.6,
              margin: "0 0 12px",
              lineHeight: 1.2,
            }}
          >
            ZizkaDB
          </h1>
          <p
            style={{
              fontSize: 17,
              color: "#000000",
              lineHeight: 1.75,
              marginBottom: 48,
            }}
          >
            The operational database for AI agents. This document describes
            architecture, APIs, data semantics, and how ZizkaDB fits alongside
            vector databases, application databases, and tracing tools.
          </p>

          <Section id="overview" title="Overview">
            <p style={p}>
              ZizkaDB stores <strong>agent events</strong> — decisions, tool
              calls, messages, and outcomes — with causal links, semantic search
              over history, reconstruction of logged state at a timestamp, and
              statistical behavioral baselines per agent.
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Layer</th>
                  <th style={th}>Role</th>
                  <th style={th}>Examples</th>
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "Vector retrieval",
                    "Document / knowledge search by embedding",
                    "Pinecone, Qdrant, Weaviate",
                  ],
                  [
                    "Application state",
                    "Transactional app data",
                    "Postgres, Redis",
                  ],
                  [
                    "Agent operations",
                    "Decision history, lineage, drift",
                    "ZizkaDB",
                  ],
                  [
                    "Traces & evals",
                    "Spans, experiments, framework hooks",
                    "LangSmith, OpenTelemetry",
                  ],
                ].map(([layer, role, ex]) => (
                  <tr key={layer}>
                    <td style={td}>
                      <strong>{layer}</strong>
                    </td>
                    <td style={td}>{role}</td>
                    <td style={td}>{ex}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <pre style={pre}>{`Agent runtime
 ├── Vector DB          →  RAG / knowledge retrieval
 ├── Postgres / Redis   →  app state, caches, users
 ├── ZizkaDB            →  events, why(), at(), baseline, forget()
 └── OTel / LangSmith   →  distributed traces (optional)`}</pre>
          </Section>

          <Section id="problem" title="Problem domain">
            <p style={p}>Production agent systems typically need:</p>
            <ul style={ul}>
              <li>
                <strong>Causal debugging</strong> — walk from a bad output to
                the event that triggered it
              </li>
              <li>
                <strong>State at time T</strong> — what was logged for an agent
                before a given timestamp
              </li>
              <li>
                <strong>Cross-session memory</strong> — under your API keys,
                retention policy, and erasure controls
              </li>
              <li>
                <strong>Behavior change detection</strong> — compare recent
                sessions to that agent&apos;s historical pattern
              </li>
            </ul>
            <p style={p}>
              ZizkaDB targets these operational concerns. It does not replace
              embedding indexes for document RAG or distributed tracing for
              infrastructure spans.
            </p>
          </Section>

          <Section id="architecture" title="Architecture">
            <h3 style={h3}>Managed (db.zizka.ai)</h3>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Component</th>
                  <th style={th}>Technology</th>
                  <th style={th}>Notes</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["Dashboard", "Next.js 14", "PM2 :3001, nginx TLS"],
                  ["API", "FastAPI (Python)", "Docker :8000"],
                  [
                    "Primary store",
                    "Postgres + pgvector",
                    "Events, tenants, metadata",
                  ],
                  ["Vector search", "Qdrant", "Semantic search index"],
                  ["Cache", "Redis", "Sessions / cache"],
                  [
                    "Edge",
                    "nginx",
                    "/ → dashboard, /v1/ → API, /swagger → OpenAPI",
                  ],
                ].map(([c, t, n]) => (
                  <tr key={c}>
                    <td style={td}>{c}</td>
                    <td style={td}>{t}</td>
                    <td style={td}>{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3 style={h3}>Self-hosted</h3>
            <p style={p}>
              Docker Compose stack: Postgres (pgvector), Qdrant, Redis, API.
              Dashboard is optional (run from{" "}
              <code style={codeInline}>dashboard/</code>). Requires{" "}
              <code style={codeInline}>OPENAI_API_KEY</code> for
              embedding-backed features; logging and causal APIs work without
              it.
            </p>
            <Code>{`git clone https://github.com/ZIZKA-AI-SL/ZizkaDB
cp .env.example infra/.env
docker compose -f infra/docker-compose.yml up -d`}</Code>
          </Section>

          <Section id="data-model" title="Data model">
            <p style={p}>
              Every record is an <strong>event</strong>:
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Field</th>
                  <th style={th}>Type</th>
                  <th style={th}>Description</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["agent", "string", "Agent identifier (fleet key)"],
                  [
                    "event",
                    "string",
                    "Event type label (e.g. tool_call, user_message)",
                  ],
                  ["data", "JSON object", "Arbitrary payload you control"],
                  ["parent_id", "UUID (optional)", "Causal parent event"],
                  [
                    "session_id",
                    "string (optional)",
                    "Groups a run / conversation",
                  ],
                  [
                    "metadata",
                    "JSON (optional)",
                    "Tenant tags (user_id, env, etc.)",
                  ],
                  [
                    "timestamp",
                    "ISO 8601",
                    "Server-assigned or client-provided",
                  ],
                  ["checksum", "SHA-256", "Hash over canonical payload bytes"],
                ].map(([f, t, d]) => (
                  <tr key={f}>
                    <td style={td}>
                      <code style={codeInline}>{f}</code>
                    </td>
                    <td style={td}>{t}</td>
                    <td style={td}>{d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              <strong>Multi-tenancy:</strong> API keys (
              <code style={codeInline}>zizkadb_live_*</code>) scope all reads
              and writes to a tenant. Dashboard auth uses email OTP → JWT.
            </p>
          </Section>

          <Section id="capabilities" title="Capabilities">
            {[
              {
                name: "Event logging",
                api: "POST /v1/events",
                sdk: "db.log(...)",
                detail: "Append events. Use parent_id to build causal trees.",
              },
              {
                name: "Causal lineage",
                api: "GET /v1/events/{id}/why",
                sdk: "db.why(event_id)",
                detail: "Returns ancestor chain from event to root.",
              },
              {
                name: "Semantic search",
                api: "POST /v1/search",
                sdk: "db.search(query, agent=..., limit=...)",
                detail:
                  "Embeddings via OpenAI text-embedding-3-small on ingest.",
              },
              {
                name: "Time travel",
                api: "GET /v1/events/at",
                sdk: "db.at(agent, timestamp)",
                detail:
                  "Aggregate logged events ≤ timestamp into a state snapshot.",
              },
              {
                name: "Context injection",
                api: "POST /v1/memory/context",
                sdk: "db.context_for(agent, task, max_tokens=...)",
                detail:
                  "Recent + semantically relevant events formatted for system prompts.",
              },
              {
                name: "Behavioral baseline",
                api: "GET /v1/agents/{id}/baseline",
                sdk: "db.baseline(agent, recent_window=...)",
                detail:
                  "Drift score vs historical event distribution; needs session volume.",
              },
              {
                name: "Session diff",
                api: "GET /v1/memory/diff/{session_id}",
                sdk: "db.memory_diff(session_id)",
                detail:
                  "Summary of event counts, errors, and changes within a session.",
              },
              {
                name: "GDPR erasure",
                api: "DELETE /v1/memory/forget",
                sdk: "db.forget(filter_key, filter_value)",
                detail:
                  "Deletes events (and vectors) matching metadata filter.",
              },
            ].map((cap) => (
              <div
                key={cap.name}
                style={{
                  marginBottom: 20,
                  paddingBottom: 20,
                  borderBottom: "1px solid #f0f0f0",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6 }}>
                  {cap.name}
                </div>
                <div style={{ fontSize: 13, color: "#000000", marginBottom: 6 }}>
                  <code style={codeInline}>{cap.api}</code> ·{" "}
                  <code style={codeInline}>{cap.sdk}</code>
                </div>
                <p style={{ ...p, margin: 0 }}>{cap.detail}</p>
              </div>
            ))}
          </Section>

          <Section id="time-travel" title="Time travel semantics">
            <p style={p}>
              <code style={codeInline}>at(agent, timestamp)</code> reconstructs{" "}
              <strong>logged state</strong> by aggregating all events for that
              agent with <code style={codeInline}>timestamp ≤ T</code>. The
              returned structure reflects what was recorded in ZizkaDB, not a
              re-execution of the LLM or tools.
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Property</th>
                  <th style={th}>Behavior</th>
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "Determinism",
                    "Same event log → same reconstructed snapshot",
                  ],
                  [
                    "LLM outputs",
                    "Not re-generated; only stored payloads are returned",
                  ],
                  [
                    "Checksums",
                    "Per-event SHA-256 enables integrity verification of stored payloads",
                  ],
                  [
                    "Completeness",
                    "Depends on what your integration logged (gaps = missing parent events)",
                  ],
                ].map(([prop, beh]) => (
                  <tr key={prop}>
                    <td style={td}>{prop}</td>
                    <td style={td}>{beh}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section id="integrity" title="Integrity & retention">
            <ul style={ul}>
              <li>
                Events are stored in <strong>Postgres</strong> with an
                append-by-default write pattern; each payload includes a{" "}
                <strong>SHA-256 checksum</strong> for integrity verification.
              </li>
              <li>
                <code style={codeInline}>forget()</code> removes events matching
                metadata filters (GDPR right to erasure) — storage is not
                WORM/immutable.
              </li>
              <li>
                Managed plans enforce <strong>retention windows</strong> (90
                days Pro, 1 year Team); self-hosted retention is
                operator-defined.
              </li>
              <li>
                Bulk signed audit export is on the product roadmap; today,
                export via API/query and verify checksums per event.
              </li>
            </ul>
          </Section>

          <Section id="performance" title="Performance expectations">
            <p style={p}>
              ZizkaDB is built for normal agent loops — tool calls, messages,
              and decisions — not for blockchain-scale write throughput. Stack:
              Postgres (events), Qdrant (vectors), Redis (cache).
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Mode</th>
                  <th style={th}>Write path</th>
                  <th style={th}>Typical use</th>
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "Logging only",
                    "Postgres INSERT + SHA-256 checksum",
                    "Fast enough for typical agent step loops",
                  ],
                  [
                    "With semantic search",
                    "Above + OpenAI embedding + Qdrant upsert (per log)",
                    "Fine for normal volume; embedding adds network latency",
                  ],
                  [
                    "why() / query",
                    "Postgres reads on explicit event_id or filters",
                    "No hidden session state on the server",
                  ],
                ].map(([mode, path, use]) => (
                  <tr key={mode}>
                    <td style={td}>
                      <strong>{mode}</strong>
                    </td>
                    <td style={td}>{path}</td>
                    <td style={td}>{use}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              High-frequency fleets (thousands of parallel writes per second)
              are not a v1 target. Async embedding queues and published
              benchmarks are on the roadmap as usage grows.
            </p>
          </Section>

          <Section id="security" title="Security (early stage)">
            <p style={p}>
              ZizkaDB v1 is aimed at developers and small teams — not formal
              compliance certification yet. Here is what we do today:
            </p>
            <ul style={ul}>
              <li>
                <strong>In transit:</strong> TLS on managed cloud (
                <code style={codeInline}>db.zizka.ai</code> via nginx).
              </li>
              <li>
                <strong>Tenant isolation:</strong> every API call scoped by API
                key or JWT to a <code style={codeInline}>tenant_id</code>; no
                cross-tenant reads.
              </li>
              <li>
                <strong>At rest:</strong> standard Postgres / disk encryption on
                the operator&apos;s infrastructure (AWS EBS on managed; your
                disk when self-hosted).
              </li>
              <li>
                <strong>BYOK embedding keys:</strong> encrypted in Postgres
                (Fernet) when you bring your own OpenAI key.
              </li>
              <li>
                <strong>GDPR erasure:</strong>{" "}
                <code style={codeInline}>forget()</code> deletes matching events
                and vectors.
              </li>
              <li>
                <strong>Telemetry:</strong> one anonymous SDK/MCP ping per process
                after first use (log or tool call) — opt out with{" "}
                <code style={codeInline}>ZIZKADB_TELEMETRY=false</code>.
              </li>
            </ul>
            <p style={p}>
              <strong>Not claimed today:</strong> SOC 2, HIPAA, or formal DPAs.
              For security questions, contact{" "}
              <a href={`mailto:${FOUNDER_EMAIL}`} style={link}>
                {FOUNDER_EMAIL}
              </a>
              .
            </p>
          </Section>

          <Section id="integration" title="Integration">
            <p style={p}>
              <strong>Concurrency:</strong> Python and TypeScript SDKs are{" "}
              <strong>stateless HTTP clients</strong> — no thread-local storage
              or <code style={codeInline}>contextvars</code>. Pass{" "}
              <code style={codeInline}>agent</code>,{" "}
              <code style={codeInline}>session_id</code>,{" "}
              <code style={codeInline}>parent_id</code>, and{" "}
              <code style={codeInline}>event_id</code> explicitly on each call.
              Safe in FastAPI, Celery, and parallel async workers.
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Surface</th>
                  <th style={th}>Install</th>
                  <th style={th}>Use case</th>
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "Python SDK",
                    "pip install zizkadb-sdk",
                    "FastAPI, notebooks, batch",
                  ],
                  [
                    "TypeScript SDK",
                    "npm install zizkadb-sdk",
                    "Node, Bun, Deno, edge",
                  ],
                  [
                    "MCP server",
                    "uvx zizkadb-mcp",
                    "Claude Desktop, Cursor — no app refactor",
                  ],
                  ["REST", "curl / any HTTP", "Go, Rust, Java, mobile"],
                ].map(([s, i, u]) => (
                  <tr key={s}>
                    <td style={td}>{s}</td>
                    <td style={td}>
                      <code style={codeInline}>{i}</code>
                    </td>
                    <td style={td}>{u}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              Cloud default host:{" "}
              <code style={codeInline}>https://db.zizka.ai</code>. Self-host:
              pass <code style={codeInline}>host=</code> to the SDK or set{" "}
              <code style={codeInline}>ZIZKADB_HOST</code> for MCP.
            </p>
            <Code lang="python">{`from zizkadb import ZizkaDB

db = ZizkaDB("zizkadb_live_xxxx")  # managed
# db = ZizkaDB(host="http://localhost:8000")  # self-host

msg = await db.log(agent="bot", event="user_message", data={"text": "..."})
tool = await db.log(agent="bot", event="tool_call", data={...}, parent_id=msg.event_id)
chain = await db.why(tool.event_id)`}</Code>
            <h3 style={h3}>Cursor MCP (30 seconds)</h3>
            <p style={p}>
              Add to <code style={codeInline}>~/.cursor/mcp.json</code> or
              project <code style={codeInline}>.cursor/mcp.json</code>, then
              reload MCP:
            </p>
            <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": { "ZIZKADB_API_KEY": "zizkadb_live_xxxx" }
    }
  }
}`}</Code>
            <p style={p}>
              Self-host: use this block instead (dev key auto-injected on
              localhost):
            </p>
            <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": { "ZIZKADB_HOST": "http://localhost:8000" }
    }
  }
}`}</Code>
            <h3 style={h3}>MCP tools</h3>
            <p style={p}>
              <code style={codeInline}>log_event</code>,{" "}
              <code style={codeInline}>search_memory</code>,{" "}
              <code style={codeInline}>get_context</code>,{" "}
              <code style={codeInline}>why</code>,{" "}
              <code style={codeInline}>query_events</code>,{" "}
              <code style={codeInline}>time_travel</code>,{" "}
              <code style={codeInline}>memory_diff</code>,{" "}
              <code style={codeInline}>forget</code> — see{" "}
              <Link href="/docs" style={link}>
                setup guide
              </Link>{" "}
              for config.
            </p>
            <p style={p}>
              Telemetry: one anonymous SDK ping per process after first log (name,
              version, OS, cloud vs self-host). Opt out:{" "}
              <code style={codeInline}>ZIZKADB_TELEMETRY=false</code>.
            </p>
          </Section>

          <Section id="api" title="REST API">
            <p style={p}>
              Base URL <code style={codeInline}>https://db.zizka.ai</code>.
              Auth:{" "}
              <code style={codeInline}>
                Authorization: Bearer &lt;token&gt;
              </code>
              . Interactive schema:{" "}
              <Link href="/swagger" style={link}>
                /swagger
              </Link>
              .
            </p>
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Method</th>
                  <th style={th}>Path</th>
                  <th style={th}>Purpose</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["POST", "/v1/events", "Log event"],
                  ["GET", "/v1/events", "Query events"],
                  ["GET", "/v1/events/{id}/why", "Causal chain"],
                  ["GET", "/v1/events/at", "State at timestamp"],
                  ["POST", "/v1/search", "Semantic search"],
                  ["POST", "/v1/memory/context", "Prompt context block"],
                  ["GET", "/v1/memory/diff/{session_id}", "Session summary"],
                  ["DELETE", "/v1/memory/forget", "Metadata erasure"],
                  ["GET", "/v1/agents", "List agents"],
                  ["GET", "/v1/agents/{id}/baseline", "Drift / baseline"],
                  ["GET", "/health", "Health check"],
                ].map(([m, path, purpose]) => (
                  <tr key={path}>
                    <td style={td}>
                      <code style={codeInline}>{m}</code>
                    </td>
                    <td style={td}>
                      <code style={codeInline}>{path}</code>
                    </td>
                    <td style={td}>{purpose}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section id="deployment" title="Deployment">
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Mode</th>
                  <th style={th}>Cost</th>
                  <th style={th}>Data residency</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["Self-hosted", "Free (AGPL core)", "Your VPC / machine"],
                  ["Managed Pro", "€29/mo", "Zizka cloud"],
                  ["Managed Team", "€69/mo", "Zizka cloud + higher limits"],
                ].map(([m, c, d]) => (
                  <tr key={m}>
                    <td style={td}>{m}</td>
                    <td style={td}>{c}</td>
                    <td style={td}>{d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              Onboarding:{" "}
              <Link href="/signup" style={link}>
                signup
              </Link>{" "}
              → email OTP → Settings → API key → SDK or MCP env. For self-hosted
              deployment, see the{" "}
              <Link href="/docs" style={link}>
                setup guide
              </Link>
              .
            </p>
          </Section>

          <Section id="comparison" title="Comparison">
            <p style={p}>
              Capability matrix (May 2026). Verify competitor docs before
              external debates; ~ = partial support.
            </p>
            <div style={{ overflowX: "auto" }}>
              <table style={table}>
                <thead>
                  <tr>
                    <th style={th}>Capability</th>
                    <th style={{ ...th, textAlign: "center" }}>LangSmith</th>
                    <th style={{ ...th, textAlign: "center" }}>Mem0</th>
                    <th style={{ ...th, textAlign: "center" }}>Pinecone</th>
                    <th
                      style={{
                        ...th,
                        textAlign: "center",
                        background: "#f5fffe",
                      }}
                    >
                      ZizkaDB
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Agent event logging", "✓", "✗", "✗", "✓"],
                    ["Causal lineage", "~", "✗", "✗", "✓"],
                    ["Time travel (logged state at T)", "✗", "✗", "✗", "✓"],
                    ["Semantic search on agent history", "✗", "✓", "✓", "✓"],
                    ["Any framework / model", "~", "✓", "✓", "✓"],
                    ["Behavioral baseline / drift", "✗", "✗", "✗", "✓"],
                    ["Cross-agent fleet queries", "✗", "✗", "✗", "✓"],
                    ["Per-event checksum", "✗", "✗", "✗", "✓"],
                    ["Self-host (free tier)", "✓", "✓", "✗", "✓"],
                  ].map(([cap, ...vals]) => (
                    <tr key={cap}>
                      <td style={td}>{cap}</td>
                      {vals.map((v, j) => (
                        <td
                          key={j}
                          style={{
                            ...td,
                            textAlign: "center",
                            background: j === 3 ? "#f5fffe" : undefined,
                            fontWeight: j === 3 ? 600 : 400,
                          }}
                        >
                          {v}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section id="licensing" title="Licensing">
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Component</th>
                  <th style={th}>License</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["Core API + self-host stack", "AGPL-3.0"],
                  ["Python SDK", "AGPL-3.0"],
                  ["TypeScript SDK", "AGPL-3.0"],
                  ["MCP server", "MIT"],
                ].map(([c, l]) => (
                  <tr key={c}>
                    <td style={td}>{c}</td>
                    <td style={td}>{l}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              AGPL applies when you modify and distribute the server. MCP is MIT
              for IDE integration. Commercial embedding requires legal review.
            </p>
          </Section>

          <Section id="limits" title="Plan limits">
            <table style={table}>
              <thead>
                <tr>
                  <th style={th}>Plan</th>
                  <th style={th}>Events / month</th>
                  <th style={th}>Retention</th>
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "Self-hosted",
                    "Unlimited (your hardware)",
                    "Operator-defined",
                  ],
                  ["Pro (€29/mo)", "50k", "90 days"],
                  ["Team (€69/mo)", "100k", "1 year"],
                ].map(([plan, ev, ret]) => (
                  <tr key={plan}>
                    <td style={td}>{plan}</td>
                    <td style={td}>{ev}</td>
                    <td style={td}>{ret}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={p}>
              Baseline/drift requires sufficient{" "}
              <code style={codeInline}>session_id</code> coverage; early agents
              report <code style={codeInline}>warming_up</code> or{" "}
              <code style={codeInline}>insufficient_data</code>.
            </p>
          </Section>

          <Section id="faq" title="FAQ">
            {[
              {
                q: "How is this different from LangSmith?",
                a: "LangSmith focuses on LangChain-centric tracing and evals. ZizkaDB is a standalone operational store with causal trees, time travel over logged state, fleet baselines, and framework-agnostic ingestion.",
              },
              {
                q: "How is this different from Mem0?",
                a: "Mem0 optimizes long-term memory retrieval for prompts. ZizkaDB adds causal lineage, session replay, drift baselines, and checksum-backed event storage.",
              },
              {
                q: "Do we replace Pinecone?",
                a: "No. Pinecone indexes documents for RAG. ZizkaDB indexes agent decision history. Most teams use both.",
              },
              {
                q: "Does ZizkaDB wrap LLM calls?",
                a: "No. You call log() at existing decision points. Optional parent_id links causality.",
              },
              {
                q: "Latency impact?",
                a: "Logging is async HTTP. Embedding runs on ingest; hot path is typically fire-and-forget await db.log(...).",
              },
              {
                q: "PII and GDPR?",
                a: "You control data and metadata. forget() deletes by metadata filter. Self-host keeps data in your infrastructure.",
              },
              {
                q: "PyPI package name?",
                a: "Install zizkadb-sdk (not the unrelated agentdb package). Import: from zizkadb import ZizkaDB.",
              },
            ].map((item) => (
              <div key={item.q} style={{ marginBottom: 20 }}>
                <div
                  style={{ fontWeight: 600, fontSize: 14.5, marginBottom: 6 }}
                >
                  {item.q}
                </div>
                <p style={{ ...p, margin: 0 }}>{item.a}</p>
              </div>
            ))}
          </Section>

          <Section id="contact" title="Contact">
            <table style={table}>
              <tbody>
                {[
                  ["Product", "https://db.zizka.ai"],
                  ["Docs", "https://db.zizka.ai/docs"],
                  ["API", "https://db.zizka.ai/swagger"],
                  ["GitHub", GITHUB_URL],
                  ["Email", FOUNDER_EMAIL],
                ].map(([k, v]) => (
                  <tr key={k}>
                    <td style={{ ...td, width: "28%", fontWeight: 500 }}>
                      {k}
                    </td>
                    <td style={td}>
                      {k === "Email" ? (
                        <a href={`mailto:${FOUNDER_EMAIL}`} style={link}>
                          {FOUNDER_EMAIL}
                        </a>
                      ) : (
                        <a
                          href={v}
                          style={link}
                          target={k === "GitHub" ? "_blank" : undefined}
                          rel="noreferrer"
                        >
                          {v}
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </main>
      </div>

      <MarketingFooter />

      <style>{`
        @media (min-width: 900px) {
          .trust-sidebar { display: block !important; }
        }
      `}</style>
    </div>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} style={{ marginBottom: 56, scrollMarginTop: 72 }}>
      <h2
        style={{
          fontSize: 22,
          fontWeight: 700,
          marginBottom: 16,
          letterSpacing: -0.3,
          paddingTop: id === "overview" ? 0 : 8,
          borderTop: id === "overview" ? "none" : "1px solid #f0f0f0",
        }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function Code({ children, lang }: { children: string; lang?: string }) {
  return (
    <pre style={pre}>
      {lang && (
        <span
          style={{
            display: "block",
            fontSize: 10,
            color: "#999",
            marginBottom: 8,
            textTransform: "uppercase",
            letterSpacing: 1,
            fontFamily: "sans-serif",
          }}
        >
          {lang}
        </span>
      )}
      {children}
    </pre>
  );
}

const p: CSSProperties = {
  fontSize: 15,
  color: "#000000",
  lineHeight: 1.75,
  margin: "0 0 14px",
};
const ul: CSSProperties = {
  margin: "0 0 14px",
  paddingLeft: 22,
  fontSize: 15,
  color: "#000000",
  lineHeight: 1.8,
};
const h3: CSSProperties = {
  fontSize: 15,
  fontWeight: 600,
  margin: "24px 0 10px",
  color: "#000000",
};
const codeInline: CSSProperties = codeInlineOnLight;
const link: CSSProperties = { color: "#000000", fontWeight: 500 };
const table: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 14,
  margin: "12px 0",
};
const th: CSSProperties = {
  textAlign: "left",
  padding: "10px 12px",
  borderBottom: "2px solid #eee",
  color: "#000000",
  fontWeight: 600,
  fontSize: 13,
};
const td: CSSProperties = {
  padding: "10px 12px",
  borderBottom: "1px solid #f0f0f0",
  color: "#000000",
  verticalAlign: "top",
};
const pre: CSSProperties = {
  margin: "12px 0",
  padding: 16,
  background: "#fafafa",
  border: "1px solid #eee",
  borderRadius: 10,
  fontSize: 12.5,
  lineHeight: 1.65,
  overflowX: "auto",
  fontFamily: "JetBrains Mono, Menlo, monospace",
};
