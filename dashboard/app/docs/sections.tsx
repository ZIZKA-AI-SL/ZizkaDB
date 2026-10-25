"use client";

import Link from "next/link";
import { type ReactNode } from "react";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { codeInlineLight, codeMonoDark } from "@/lib/content-styles";

// ── Shared UI (matches docs page) ───────────────────────────────────────────

export const S = {
  h1: {
    fontSize: 32,
    fontWeight: 700,
    margin: "0 0 8px",
    letterSpacing: -0.5,
    color: "#fff",
  } as const,
  h2: {
    fontSize: 22,
    fontWeight: 700,
    margin: "48px 0 6px",
    letterSpacing: -0.3,
    color: "#fff",
  } as const,
  h3: {
    fontSize: 15,
    fontWeight: 600,
    margin: "28px 0 8px",
    color: "#ffffff",
  } as const,
  lead: {
    fontSize: 16,
    color: "#ffffff",
    lineHeight: 1.7,
    margin: "0 0 32px",
  } as const,
  p: {
    fontSize: 14.5,
    color: "#ffffff",
    lineHeight: 1.7,
    margin: "0 0 14px",
  } as const,
};

export function Code({
  children,
  lang = "",
}: {
  children: string;
  lang?: string;
}) {
  const { copied, copy } = useCopyToClipboard();
  return (
    <div style={{ position: "relative", margin: "10px 0 20px" }}>
      <pre
        style={{
          background: "#111827",
          border: "1px solid rgba(255,255,255,0.12)",
          borderRadius: 10,
          padding: "18px 20px",
          fontSize: 13,
          lineHeight: 1.75,
          overflowX: "auto",
          fontFamily: "JetBrains Mono, Fira Code, Menlo, monospace",
          color: "#ffffff",
          margin: 0,
        }}
      >
        {lang && (
          <span
            style={{
              position: "absolute",
              top: 10,
              left: 16,
              fontSize: 10,
              color: "#ffffff",
              fontFamily: "sans-serif",
              textTransform: "uppercase",
              letterSpacing: 1,
            }}
          >
            {lang}
          </span>
        )}
        <span
          style={{
            display: lang ? "block" : undefined,
            marginTop: lang ? 12 : 0,
          }}
        >
          {children}
        </span>
      </pre>
      <button
        type="button"
        onClick={() => copy(children)}
        style={{
          position: "absolute",
          top: 10,
          right: 12,
          background: copied ? "#22c55e" : "rgba(255,255,255,0.1)",
          border: "none",
          borderRadius: 6,
          padding: "3px 10px",
          fontSize: 11,
          cursor: "pointer",
          color: copied ? "#fff" : "#ffffff",
        }}
      >
        {copied ? "✓ Copied" : "Copy"}
      </button>
    </div>
  );
}

export function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: ReactNode;
}) {
  return (
    <div style={{ display: "flex", gap: 16, marginBottom: 32 }}>
      <div
        style={{
          flexShrink: 0,
          width: 28,
          height: 28,
          borderRadius: "50%",
          background: "#f97316",
          color: "#fff",
          fontSize: 13,
          fontWeight: 700,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginTop: 2,
        }}
      >
        {n}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8, color: "#fff" }}>
          {title}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Callout({
  type,
  children,
}: {
  type: "tip" | "warning" | "info";
  children: ReactNode;
}) {
  const styles = {
    tip: { bg: "rgba(34,197,94,0.12)", border: "rgba(34,197,94,0.35)", text: "#86efac" },
    warning: { bg: "rgba(251,191,36,0.12)", border: "rgba(251,191,36,0.35)", text: "#fde68a" },
    info: { bg: "rgba(249,115,22,0.12)", border: "rgba(249,115,22,0.35)", text: "#fdba74" },
  };
  const s = styles[type];
  return (
    <div
      style={{
        background: s.bg,
        border: `1px solid ${s.border}`,
        borderRadius: 10,
        padding: "12px 16px",
        fontSize: 13.5,
        color: s.text,
        margin: "0 0 20px",
        lineHeight: 1.6,
      }}
    >
      {children}
    </div>
  );
}

function MethodRow({ method, desc }: { method: string; desc: string }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        padding: "12px 16px",
        background: "#111827",
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.1)",
      }}
    >
      <code
        style={{
          fontFamily: "monospace",
          fontSize: 13,
          fontWeight: 600,
          color: "#fb923c",
          minWidth: 200,
          flexShrink: 0,
        }}
      >
        {method}
      </code>
      <span style={{ fontSize: 13.5, color: "#ffffff", lineHeight: 1.5 }}>
        {desc}
      </span>
    </div>
  );
}

const SDK_METHODS = [
  {
    method: "log()",
    desc: "Log any event. Use parent_id / parentId for causal links.",
  },
  {
    method: "why(event_id)",
    desc: "Walk the causal chain from an event to its root.",
  },
  {
    method: "search(query)",
    desc: "Search agent history in plain English (keyword, or hybrid with embeddings).",
  },
  {
    method: "at(agent, timestamp)",
    desc: "Reconstruct logged state at a past timestamp.",
  },
  {
    method: "query(agent)",
    desc: "List recent events, optionally filtered by event type.",
  },
  {
    method: "context_for(agent, task)",
    desc: "Memory block formatted for system prompt injection.",
  },
  {
    method: "baseline(agent)",
    desc: "Behavioral baseline and drift score vs historical sessions.",
  },
  {
    method: "memory_diff(session_id)",
    desc: "Summary of what happened in one session.",
  },
  { method: "forget(key, value)", desc: "GDPR erasure by metadata filter." },
  { method: "agents()", desc: "List all agents for your tenant." },
];

// ── Overview ────────────────────────────────────────────────────────────────

export function OverviewSection({
  onNavigate,
}: {
  onNavigate: (s: string) => void;
}) {
  return (
    <div>
      <h1 style={S.h1}>Documentation</h1>
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 20 }}
      >
        {[
          { label: "Python SDK", pkg: "zizkadb-sdk" },
          { label: "MCP", pkg: "zizkadb-mcp" },
          { label: "LangChain", pkg: "zizkadb-langchain" },
          { label: "CrewAI", pkg: "zizkadb-crewai" },
        ].map(({ label, pkg }) => (
          // eslint-disable-next-line @next/next/no-img-element
          <a
            key={pkg}
            href={`https://pypi.org/project/${pkg}/`}
            target="_blank"
            rel="noreferrer"
          >
            <img
              src={`https://img.shields.io/pypi/v/${pkg}?label=${encodeURIComponent(label)}`}
              alt={`${label} on PyPI`}
              style={{ display: "block", height: 20 }}
            />
          </a>
        ))}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <a
          href="https://www.npmjs.com/package/zizkadb-sdk"
          target="_blank"
          rel="noreferrer"
        >
          <img
            src="https://img.shields.io/npm/v/zizkadb-sdk?label=TypeScript%20SDK"
            alt="TypeScript SDK on npm"
            style={{ display: "block", height: 20 }}
          />
        </a>
      </div>
      <p style={S.lead}>
        Open-source guides for self-hosting ZizkaDB with Docker. Start with the quickstart,
        then connect your stack. Managed cloud docs are at the bottom if you prefer hosted.
      </p>

      <Callout type="tip">
        <strong>OSS quickstart (recommended):</strong>
        <Code lang="bash">{`git clone https://github.com/ZIZKA-AI-SL/ZizkaDB
cd ZizkaDB
bash scripts/quickstart.sh`}</Code>
        Pulls pre-built GHCR images when available — no local compile. Runs the <code style={codeMonoDark}>why()</code> demo
        and opens the dashboard at <code style={codeMonoDark}>http://localhost:3001/login</code> (no signup).
        Full connect guide:{' '}
        <a href="https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md" target="_blank" rel="noreferrer" style={{ color: '#166534', fontWeight: 500 }}>
          CONNECT.md
        </a>
      </Callout>

      <Callout type="tip">
        <strong>Environment variables</strong>. Set <code style={codeMonoDark}>ZIZKADB_HOST</code> only for a self-hosted
        server: when it is set, the SDK sends your key there instead of to ZizkaDB Cloud.
        <Code lang="bash">{`# ZizkaDB Cloud: the key alone
export ZIZKADB_API_KEY=zizkadb_live_...

# Self-hosted server: its address and a key from its dashboard
# (local dev stack: just the address, no key)
export ZIZKADB_HOST=https://your-zizkadb.example.com
export ZIZKADB_API_KEY=zizkadb_live_...

export ZIZKADB_AGENT=my-bot               # optional
export ZIZKADB_TELEMETRY=false            # optional`}</Code>
      </Callout>

      <Callout type="tip">
        SDKs are <strong>stateless</strong> — pass{" "}
        <code style={codeMonoDark}>agent</code>,{" "}
        <code style={codeMonoDark}>session_id</code>, and{" "}
        <code style={codeMonoDark}>event_id</code> explicitly.
        See{" "}
        <Link href="/trust#performance" style={{ color: "#166534" }}>
          performance
        </Link>{" "}
        and{" "}
        <Link href="/trust#security" style={{ color: "#166534" }}>
          security
        </Link>{" "}
        on the technical reference.
      </Callout>

      <h2 style={{ ...S.h2, marginTop: 24 }}>Choose your integration</h2>
      <div style={{ display: "grid", gap: 12, marginBottom: 40 }}>
        {[
          { id: 'selfhost', title: 'Self-host (OSS)', desc: 'bash scripts/quickstart.sh — Docker, pre-built images, db.why() demo', time: '~2 min' },
          { id: 'frameworks', title: 'Framework starters', desc: 'zizkadb init — LangChain, CrewAI, OpenAI, MCP templates', time: '~2 min' },
          { id: 'python', title: 'Python SDK', desc: 'FastAPI, LangChain, notebooks. pip install zizkadb-sdk', time: '~5 min' },
          { id: 'typescript', title: 'TypeScript SDK', desc: 'Node, Bun, Deno. npm install zizkadb-sdk', time: '~5 min' },
          { id: 'mcp', title: 'MCP server', desc: 'Claude Desktop, Cursor, Windsurf — no app code changes', time: '~2 min' },
          { id: 'rest', title: 'REST API', desc: 'Any language via HTTP. curl, Go, Rust, Java', time: '~3 min' },
        ].map(item => (
          <button key={item.id} type="button" onClick={() => onNavigate(item.id)} style={{
            textAlign: 'left', padding: '18px 20px', borderRadius: 12, border: '1px solid #e5e5e5',
            background: '#fff', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16,
          }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
                {item.title}
              </div>
              <div style={{ fontSize: 13.5, color: "#000000" }}>{item.desc}</div>
            </div>
            <span style={{ fontSize: 12, color: "#000000", flexShrink: 0 }}>
              {item.time}
            </span>
          </button>
        ))}
      </div>

      <h2 style={{ ...S.h2, marginTop: 0 }}>OSS path — self-host with Docker</h2>
      <p style={S.p}>
        Full guide in{' '}
        <button type="button" onClick={() => onNavigate('selfhost')} style={{ background: 'none', border: 'none', color: '#1e40af', fontWeight: 500, cursor: 'pointer', padding: 0, fontSize: 'inherit' }}>Self-host →</button>
        {' '}· connect snippets in{' '}
        <a href="https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md" target="_blank" rel="noreferrer" style={{ color: '#1e40af', fontWeight: 500 }}>CONNECT.md</a>
      </p>
      <Step n={1} title="Quickstart (one command)">
        <Code lang="bash">{`git clone https://github.com/ZIZKA-AI-SL/ZizkaDB
cd ZizkaDB
bash scripts/quickstart.sh`}</Code>
        <p style={S.p}>
          Starts API + dashboard. Uses pre-built <code style={codeMonoDark}>ghcr.io/zizka-ai-sl/*</code> images when published;
          otherwise builds locally. Runs <code style={codeMonoDark}>zizkadb demo</code> (causal lineage).
        </p>
      </Step>
      <Step n={2} title="Open dashboard (no email)">
        <p style={S.p}>
          Go to <code style={codeMonoDark}>http://localhost:3001/login</code> → click{' '}
          <strong>Open my dashboard →</strong>. This is your local workspace.
        </p>
      </Step>
      <Step n={3} title="Connect your agent">
        <Code lang="python">{`pip install zizkadb-sdk
# zizkadb demo          # repeat the worked example
# zizkadb init my-agent --template basic

from zizkadb import ZizkaDB
db = ZizkaDB(host="http://localhost:8000")  # auto-uses local dev key
await db.log(agent="my-bot", event="started", data={})`}</Code>
        <Callout type="warning">
          <strong>Important:</strong> SDK and dashboard must use the <strong>same tenant</strong>.
          Local dev: green dashboard button + <code style={codeMonoDark}>host=</code> SDK.
          Production VPS: email OTP + API key from Settings.
        </Callout>
      </Step>
      <Step n={4} title="See your data in the dashboard">
        <p style={S.p}>Agents appear as soon as you log events. Click an agent for events, sessions, and drift.</p>
      </Step>

      <h2 style={{ ...S.h2, marginTop: 48 }}>Managed cloud (optional)</h2>
      <p style={S.p}>Prefer not to run Docker? Use hosted ZizkaDB at <strong>db.zizka.ai</strong>.</p>
      <Step n={1} title="Sign up & create an agent">
        <p style={S.p}>
          <Link href="/signup" style={{ color: "#111", fontWeight: 500 }}>
            Sign up
          </Link>{" "}
          with email OTP →{" "}
          <Link href="/dashboard" style={{ color: "#111", fontWeight: 500 }}>
            Dashboard → Create agent
          </Link>{" "}
          → copy the API key (
          <code
            style={codeInlineLight}
          >
            zizkadb_live_…
          </code>
          ).
        </p>
      </Step>
      <Step n={2} title="Connect SDK, MCP, or REST">
        <p style={S.p}>
          Use your API key and the <strong>same agent name</strong> in every <code style={codeMonoDark}>db.log(agent=…)</code> call.
        </p>
      </Step>
      <Step n={3} title="Open your dashboard">
        <p style={S.p}>
          <Link href="/dashboard" style={{ color: '#111', fontWeight: 500 }}>db.zizka.ai/dashboard</Link> shows the same agents and events your code logs.
        </p>
      </Step>

      <h2 style={{ ...S.h2, marginTop: 48 }}>After connecting</h2>
      <Step n={1} title="Log events with session_id">
        <p style={S.p}>
          Log{" "}
          <code
            style={codeInlineLight}
          >
            user_message
          </code>
          , each{" "}
          <code
            style={codeInlineLight}
          >
            tool_call
          </code>
          , and{" "}
          <code
            style={codeInlineLight}
          >
            assistant_response
          </code>{" "}
          with{" "}
          <code
            style={codeInlineLight}
          >
            parent_id
          </code>{" "}
          links. Use the same{" "}
          <code
            style={codeInlineLight}
          >
            session_id
          </code>{" "}
          for one conversation so baselines and diffs work.
        </p>
      </Step>
      <Step n={2} title="Configure embeddings (managed)">
        <p style={S.p}>
          In Settings → <strong>Embeddings</strong>, pick your OpenAI model
          (platform-hosted or your own key) to add vector results to search and{" "}
          <code style={codeMonoDark}>context_for()</code>; without embeddings both use keyword search.
          Logging and <code style={codeMonoDark}>why()</code>{" "}
          work without embeddings.
        </p>
      </Step>
      <Step n={3} title="Use drift & search">
        <p style={S.p}>
          Once you have enough sessions, use{" "}
          <code style={codeMonoDark}>baseline()</code> and
          semantic search in the dashboard or SDK.
        </p>
      </Step>
    </div>
  );
}

// ── Python SDK ────────────────────────────────────────────────────────────────

export function PythonSection() {
  return (
    <div>
      <h1 style={S.h1}>Python SDK</h1>
      <p style={S.lead}>
        Full guide for integrating ZizkaDB in Python agents — FastAPI services,
        LangChain, CrewAI, notebooks, or scripts.
      </p>

      <Step n={1} title="Prerequisites">
        <p style={S.p}>
          Python <strong>3.10+</strong>. Works in venv, conda, or system Python.
        </p>
        <Code lang="bash">python3 --version # 3.10 or higher</Code>
      </Step>

      <Step n={2} title="Choose your environment">
        <p style={S.p}>
          <strong>Managed cloud:</strong>{" "}
          <Link href="/signup" style={{ color: "#111", fontWeight: 500 }}>
            Sign up
          </Link>{" "}
          → Settings → Create API key (
          <code style={codeMonoDark}>zizkadb_live_…</code>).
        </p>
        <p style={S.p}>
          <strong>Self-host:</strong> use{" "}
          <code style={codeMonoDark}>
            host=&quot;http://localhost:8000&quot;
          </code>{" "}
          (dev key auto-sent). See{" "}
          <Link href="/docs" style={{ color: "#1e40af" }}>
            Self-host docs
          </Link>{" "}
          → click <strong>Open my dashboard →</strong> on the login page.
        </p>
      </Step>

      <Step n={3} title="Install">
        <Code lang="bash">pip install zizkadb-sdk</Code>
        <Callout type="warning">
          Install <strong>zizkadb-sdk</strong> on PyPI — not the unrelated{" "}
          <code style={codeMonoDark}>agentdb</code> package.
        </Callout>
      </Step>

      <Step n={4} title="Initialize the client">
        <Code lang="python">{`from zizkadb import ZizkaDB

# Managed cloud (db.zizka.ai)
db = ZizkaDB("zizkadb_live_xxxx")

# Self-hosted
# db = ZizkaDB(host="http://localhost:8000")`}</Code>
      </Step>

      <Step n={5} title="Log your first event">
        <p style={S.p}>
          The SDK is async-first. Wrap calls in{" "}
          <code style={codeMonoDark}>async def main()</code> and
          run with{" "}
          <code style={codeMonoDark}>asyncio.run(main())</code>.
        </p>
        <Code lang="python">{`import asyncio
from zizkadb import ZizkaDB

db = ZizkaDB("zizkadb_live_xxxx")

async def main():
    result = await db.log(
        agent="support-bot",
        event="tool_call",
        data={"tool": "search", "query": "pricing"},
        session_id="sess_abc123",   # group one conversation
    )
    print("Saved:", result.event_id)

asyncio.run(main())`}</Code>
      </Step>

      <Step n={6} title="Link events causally">
        <p style={S.p}>
          Pass <code style={codeMonoDark}>parent_id</code> so you
          can call <code style={codeMonoDark}>why()</code> later.
        </p>
        <Code lang="python">{`async def main():
    msg = await db.log(
        agent="support-bot", event="user_message",
        data={"text": "why is my bill $200?"},
        session_id="sess_abc123",
    )
    tool = await db.log(
        agent="support-bot", event="tool_call",
        data={"tool": "get_billing"},
        parent_id=msg.event_id,
        session_id="sess_abc123",
    )
    resp = await db.log(
        agent="support-bot", event="assistant_response",
        data={"text": "I found a duplicate charge."},
        parent_id=tool.event_id,
        session_id="sess_abc123",
    )
    chain = await db.why(resp.event_id)
    chain.print()

asyncio.run(main())`}</Code>
      </Step>

      <Step n={7} title="Use search, replay, and memory">
        <Code lang="python">{`async def main():
    # Semantic search
    hits = await db.search("billing complaint", agent="support-bot", limit=5)

    # Reconstruct logged state at a time
    from datetime import datetime, timezone
    state = await db.at("support-bot", datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc))

    # Prompt-ready memory block
    ctx = await db.context_for(
        agent="support-bot",
        task="user asking about refund policy",
        max_tokens=2000,
    )

    # Drift vs baseline (needs enough sessions with session_id)
    baseline = await db.baseline("support-bot", recent_window=50)
    print(baseline)

asyncio.run(main())`}</Code>
      </Step>

      <h2 style={{ ...S.h2, marginTop: 16 }}>
        Framework example: OpenAI tool loop
      </h2>
      <Code lang="python">{`import asyncio
from openai import AsyncOpenAI
from zizkadb import ZizkaDB

db = ZizkaDB("zizkadb_live_xxxx")
openai = AsyncOpenAI()

async def run_turn(user_text: str, session_id: str):
    msg = await db.log(
        agent="my-agent", event="user_message",
        data={"text": user_text}, session_id=session_id,
    )
    parent_id = msg.event_id

    response = await openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": user_text}],
    )
    text = response.choices[0].message.content or ""
    await db.log(
        agent="my-agent", event="assistant_response",
        data={"text": text}, parent_id=parent_id, session_id=session_id,
    )
    return text

asyncio.run(run_turn("Hello", "sess_001"))`}</Code>

      <h2 style={S.h2}>All Python methods</h2>
      <div style={{ display: "grid", gap: 10 }}>
        {SDK_METHODS.map((m) => (
          <MethodRow key={m.method} method={`db.${m.method}`} desc={m.desc} />
        ))}
      </div>

      <h2 style={S.h2}>Troubleshooting</h2>
      <div style={{ display: "grid", gap: 12 }}>
        {[
          {
            q: "SyntaxError: await outside async function",
            a: "Wrap code in async def main() and asyncio.run(main()).",
          },
          {
            q: "401 Invalid API key",
            a: "Copy the full key from Dashboard → Settings → API keys (no spaces). Use ZIZKADB_API_KEY or AGENTDB_API_KEY in your env.",
          },
          {
            q: "403 Agent mismatch",
            a: "Your key is scoped to one agent (e.g. my-bot) but code logged to a different agent name. Use the same name in db.log(), or create a tenant-wide key in Settings.",
          },
          {
            q: "Dashboard empty but SDK works",
            a: "Check you are viewing the same agent name your code logs to. Use Test agent in Settings with ?agent= set. Settings → Send test event logs to dashboard-connection-test, not your app agent.",
          },
          {
            q: "Search returns nothing",
            a: "Configure embeddings in Settings. Log a few events first so there is history to search.",
          },
          {
            q: "baseline shows warming_up",
            a: "Log more sessions with session_id on each event. Drift needs volume.",
          },
        ].map((item) => (
          <div
            key={item.q}
            style={{
              padding: "14px 18px",
              background: "#fafafa",
              borderRadius: 10,
              border: "1px solid #ebebeb",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4, color: "#1a1a1a" }}>
              {item.q}
            </div>
            <div style={{ fontSize: 13.5, color: "#555" }}>{item.a}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── TypeScript SDK ────────────────────────────────────────────────────────────

export function TypeScriptSection() {
  return (
    <div>
      <h1 style={S.h1}>TypeScript SDK</h1>
      <p style={S.lead}>
        Integrate ZizkaDB in Node.js, Bun, Deno, or edge runtimes. Core HTTP client matches Python for{' '}
        <code style={codeMonoDark}>log</code>, <code style={codeMonoDark}>why</code>,{' '}
        <code style={codeMonoDark}>search</code>, and related APIs. LangChain and CrewAI adapters are Python-only — use MCP or REST from Node for those stacks.
      </p>

      <Step n={1} title="Prerequisites">
        <p style={S.p}>
          Node.js <strong>18+</strong> (or Bun / Deno). TypeScript optional but
          recommended.
        </p>
        <Code lang="bash">node --version # v18+</Code>
      </Step>

      <Step n={2} title="Choose your environment">
        <p style={S.p}>
          <strong>Managed cloud:</strong>{" "}
          <Link href="/signup" style={{ color: "#111", fontWeight: 500 }}>
            Sign up
          </Link>{" "}
          → Settings → Create API key.
        </p>
        <p style={S.p}>
          <strong>Self-host:</strong>{" "}
          <code
            style={codeMonoDark}
          >{`new ZizkaDB({ host: 'http://localhost:8000' })`}</code>{" "}
          — see{" "}
          <Link href="/docs" style={{ color: "#1e40af" }}>
            Self-host docs
          </Link>{" "}
          for dashboard setup.
        </p>
      </Step>

      <Step n={3} title="Install">
        <Code lang="bash">
          npm install zizkadb-sdk # or: pnpm add zizkadb-sdk / yarn add
          zizkadb-sdk / bun add zizkadb-sdk
        </Code>
      </Step>

      <Step n={4} title="Initialize the client">
        <Code lang="typescript">{`import { ZizkaDB } from 'zizkadb-sdk'

// Managed cloud
const db = new ZizkaDB({ apiKey: 'zizkadb_live_xxxx' })

// Self-hosted
// const db = new ZizkaDB({ host: 'http://localhost:8000' })`}</Code>
      </Step>

      <Step n={5} title="Log your first event">
        <Code lang="typescript">{`const result = await db.log({
  agent: 'support-bot',
  event: 'tool_call',
  data: { tool: 'search', query: 'pricing' },
  sessionId: 'sess_abc123',
})
console.log(result.eventId)`}</Code>
      </Step>

      <Step n={6} title="Link events causally">
        <Code lang="typescript">{`const msg = await db.log({
  agent: 'support-bot',
  event: 'user_message',
  data: { text: 'why is my bill $200?' },
  sessionId: 'sess_abc123',
})

const tool = await db.log({
  agent: 'support-bot',
  event: 'tool_call',
  data: { tool: 'get_billing' },
  parentId: msg.eventId,
  sessionId: 'sess_abc123',
})

const chain = await db.why(tool.eventId)
chain.print()`}</Code>
      </Step>

      <Step n={7} title="Search, replay, context, baseline">
        <Code lang="typescript">{`// Semantic search
const hits = await db.search({
  query: 'billing complaint',
  agent: 'support-bot',
  limit: 5,
})

// Logged state at timestamp (ISO string)
const state = await db.at({
  agent: 'support-bot',
  timestamp: '2026-05-01T15:00:00Z',
})

// Memory for system prompt
const context = await db.contextFor({
  agent: 'support-bot',
  task: 'user asking about refund',
  maxTokens: 2000,
})

// Behavioral drift
const baseline = await db.baseline({ agent: 'support-bot', recentWindow: 50 })`}</Code>
      </Step>

      <h2 style={{ ...S.h2, marginTop: 16 }}>
        Framework example: Express + OpenAI
      </h2>
      <Code lang="typescript">{`import express from 'express'
import OpenAI from 'openai'
import { ZizkaDB } from 'zizkadb-sdk'

const app = express()
app.use(express.json())
const db = new ZizkaDB({ apiKey: process.env.ZIZKADB_API_KEY! })
const openai = new OpenAI()

app.post('/chat', async (req, res) => {
  const { message, sessionId } = req.body
  const msg = await db.log({
    agent: 'api-bot', event: 'user_message',
    data: { text: message }, sessionId,
  })
  const completion = await openai.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: [{ role: 'user', content: message }],
  })
  const text = completion.choices[0]?.message?.content ?? ''
  await db.log({
    agent: 'api-bot', event: 'assistant_response',
    data: { text }, parentId: msg.eventId, sessionId,
  })
  res.json({ reply: text })
})

app.listen(3000)`}</Code>

      <Callout type="tip">
        Opt out of anonymous SDK telemetry:{" "}
        <code style={codeMonoDark}>ZIZKADB_TELEMETRY=false</code>
      </Callout>

      <h2 style={S.h2}>All TypeScript methods</h2>
      <div style={{ display: "grid", gap: 10 }}>
        {SDK_METHODS.map((m) => (
          <MethodRow
            key={m.method}
            method={`db.${m.method.replace("_", "")}`}
            desc={m.desc}
          />
        ))}
      </div>
    </div>
  );
}

// ── REST API ────────────────────────────────────────────────────────────────

export function RestSection() {
  return (
    <div>
      <h1 style={S.h1}>REST API</h1>
      <p style={S.lead}>
        Call ZizkaDB from any language with HTTP. Use this for Go, Rust, Java,
        Ruby, mobile, or custom integrations.
      </p>

      <Step n={1} title="Base URL & auth">
        <p style={S.p}>
          <strong>Managed:</strong>{" "}
          <code
            style={codeInlineLight}
          >
            https://db.zizka.ai
          </code>
          <br />
          <strong>Self-host:</strong>{" "}
          <code
            style={codeInlineLight}
          >
            http://localhost:8000
          </code>
        </p>
        <p style={S.p}>
          Every request (except{" "}
          <code style={codeMonoDark}>/health</code>) needs:
        </p>
        <Code lang="http">
          Authorization: Bearer zizkadb_live_xxxx Content-Type: application/json
        </Code>
        <Callout type="warning">
          API routes are under <strong>/v1/</strong> — not{" "}
          <code style={codeMonoDark}>/api/v1/</code> when calling
          directly. Interactive reference:{" "}
          <a href="/swagger" style={{ color: "#92400e", fontWeight: 500 }}>
            /swagger
          </a>
        </Callout>
      </Step>

      <Step n={2} title="Health check (no auth)">
        <Code lang="bash">{`curl https://db.zizka.ai/health
# {"status":"ok","version":"0.1.0"}`}</Code>
      </Step>

      <Step n={3} title="Log an event">
        <Code lang="bash">{`curl -X POST https://db.zizka.ai/v1/events \\
  -H "Authorization: Bearer zizkadb_live_xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent": "support-bot",
    "event": "tool_call",
    "data": { "tool": "get_billing" },
    "session_id": "sess_abc123",
    "parent_id": null
  }'
# → { "event_id": "...", "timestamp": "...", "sequence_no": 1 }`}</Code>
      </Step>

      <Step n={4} title="Query events">
        <Code lang="bash">{`curl "https://db.zizka.ai/v1/events?agent=support-bot&limit=20" \\
  -H "Authorization: Bearer zizkadb_live_xxxx"`}</Code>
      </Step>

      <Step n={5} title="Causal chain (why)">
        <Code lang="bash">{`curl "https://db.zizka.ai/v1/events/EVENT_UUID_HERE/why" \\
  -H "Authorization: Bearer zizkadb_live_xxxx"`}</Code>
      </Step>

      <Step n={6} title="Time travel (state at timestamp)">
        <Code lang="bash">{`curl "https://db.zizka.ai/v1/events/at?agent=support-bot&timestamp=2026-05-01T15:00:00Z" \\
  -H "Authorization: Bearer zizkadb_live_xxxx"`}</Code>
      </Step>

      <Step n={7} title="Semantic search">
        <Code lang="bash">{`curl -X POST https://db.zizka.ai/v1/search \\
  -H "Authorization: Bearer zizkadb_live_xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "query": "customer frustrated about billing",
    "agent": "support-bot",
    "limit": 10
  }'`}</Code>
      </Step>

      <Step n={8} title="Context for prompts">
        <Code lang="bash">{`curl -X POST https://db.zizka.ai/v1/memory/context \\
  -H "Authorization: Bearer zizkadb_live_xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent": "support-bot",
    "task": "user asking about refund policy",
    "max_tokens": 2000
  }'`}</Code>
      </Step>

      <Step n={9} title="Agents, baseline, session diff, forget">
        <Code lang="bash">{`# List agents
curl https://db.zizka.ai/v1/agents -H "Authorization: Bearer zizkadb_live_xxxx"

# Behavioral baseline / drift
curl "https://db.zizka.ai/v1/agents/support-bot/baseline?recent_window=50" \\
  -H "Authorization: Bearer zizkadb_live_xxxx"

# Session summary
curl https://db.zizka.ai/v1/memory/diff/sess_abc123 \\
  -H "Authorization: Bearer zizkadb_live_xxxx"

# GDPR erasure by metadata
curl -X DELETE https://db.zizka.ai/v1/memory/forget \\
  -H "Authorization: Bearer zizkadb_live_xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{"filter_key": "user_id", "filter_value": "user_123"}'`}</Code>
      </Step>

      <h2 style={S.h2}>Endpoint reference</h2>
      <div
        style={{
          overflowX: "auto",
          border: "1px solid #e5e5e5",
          borderRadius: 10,
        }}
      >
        <table
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
        >
          <thead>
            <tr style={{ background: "#f7f7f7" }}>
              <th style={{ padding: "10px 14px", textAlign: "left" }}>
                Method
              </th>
              <th style={{ padding: "10px 14px", textAlign: "left" }}>Path</th>
              <th style={{ padding: "10px 14px", textAlign: "left" }}>
                Purpose
              </th>
            </tr>
          </thead>
          <tbody>
            {[
              ["POST", "/v1/events", "Log event"],
              ["GET", "/v1/events", "Query events"],
              ["GET", "/v1/events/{id}/why", "Causal chain"],
              ["GET", "/v1/events/at", "State at timestamp"],
              ["POST", "/v1/search", "Search (keyword, semantic or hybrid)"],
              ["POST", "/v1/memory/context", "Prompt context"],
              ["GET", "/v1/memory/diff/{session_id}", "Session diff"],
              ["DELETE", "/v1/memory/forget", "GDPR erasure"],
              ["GET", "/v1/agents", "List agents"],
              ["GET", "/v1/agents/{id}/baseline", "Drift baseline"],
              ["GET", "/v1/agents/{id}/sessions", "List sessions"],
              ["GET", "/v1/settings/embeddings", "Embedding config"],
              ["PUT", "/v1/settings/embeddings", "Update embeddings (JWT)"],
            ].map(([m, p, d]) => (
              <tr key={p} style={{ borderTop: "1px solid #f0f0f0" }}>
                <td
                  style={{
                    padding: "10px 14px",
                    fontFamily: "monospace",
                    fontSize: 12,
                  }}
                >
                  {m}
                </td>
                <td
                  style={{
                    padding: "10px 14px",
                    fontFamily: "monospace",
                    fontSize: 12,
                  }}
                >
                  {p}
                </td>
                <td style={{ padding: "10px 14px", color: "#555" }}>{d}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={S.h2}>Common errors</h2>
      <div style={{ display: "grid", gap: 12 }}>
        {[
          {
            q: "401 Unauthorized",
            a: "Missing or invalid Bearer token. Use zizkadb_live_ key from Settings.",
          },
          {
            q: "404 on /api/v1/...",
            a: "Use /v1/ directly: https://db.zizka.ai/v1/events",
          },
          {
            q: "400 on /v1/search",
            a: "Embeddings not configured. Set up in Dashboard → Settings → Embeddings.",
          },
        ].map((item) => (
          <div
            key={item.q}
            style={{
              padding: "14px 18px",
              background: "#fafafa",
              borderRadius: 10,
              border: "1px solid #ebebeb",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4, color: "#1a1a1a" }}>
              {item.q}
            </div>
            <div style={{ fontSize: 13.5, color: "#555" }}>{item.a}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── MCP ─────────────────────────────────────────────────────────────────────

export function McpSection() {
  return (
    <div>
      <h1 style={S.h1}>MCP Server</h1>
      <p style={S.lead}>
        Connect ZizkaDB to Claude Desktop, Cursor, or Windsurf in minutes — no
        SDK and no changes to your app code. The AI calls ZizkaDB tools directly
        during conversations.
      </p>

      <Callout type="info">
        <strong>Fastest start — Cursor:</strong> paste this into{" "}
        <code style={codeMonoDark}>~/.cursor/mcp.json</code>,
        reload MCP, done.
      </Callout>
      <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": { "ZIZKADB_API_KEY": "zizkadb_live_xxxx" }
    }
  }
}`}</Code>
      <p style={{ ...S.p, marginTop: -8, marginBottom: 24 }}>
        Self-host: use the full block in <strong>Self-hosted API</strong> below
        (only <code style={codeMonoDark}>ZIZKADB_HOST</code> —
        dev key auto-injected). Requires{" "}
        <code style={codeMonoDark}>uvx</code> — install{" "}
        <a
          href="https://docs.astral.sh/uv/"
          target="_blank"
          rel="noreferrer"
          style={{ color: "#111" }}
        >
          uv
        </a>{" "}
        first.
      </p>

      <Callout type="info">
        <strong>MCP</strong> (Model Context Protocol) lets AI apps use external
        tools. ZizkaDB exposes log, search, why, time travel, and more as native
        MCP tools.
      </Callout>

      <Step n={1} title="Prerequisites">
        <p style={S.p}>
          <strong>uvx</strong> (recommended) — comes with{" "}
          <a
            href="https://docs.astral.sh/uv/"
            target="_blank"
            rel="noreferrer"
            style={{ color: "#111" }}
          >
            uv
          </a>{" "}
          install. Or:{" "}
          <code style={codeMonoDark}>
            pip install zizkadb-mcp
          </code>
        </p>
        <Code lang="bash">
          # macOS / Linux curl -LsSf https://astral.sh/uv/install.sh | sh uvx
          --version
        </Code>
      </Step>

      <Step n={2} title="Create an agent & copy key">
        <p style={S.p}>
          <Link href="/signup" style={{ color: "#111", fontWeight: 500 }}>
            Sign up
          </Link>{" "}
          → Dashboard → Create agent → copy key. Use the same agent name when
          calling <code style={codeMonoDark}>log_event</code>.
        </p>
      </Step>

      <h2 style={{ ...S.h2, marginTop: 32 }}>Claude Desktop</h2>
      <Step n={3} title="Edit Claude config">
        <p style={S.p}>Mac:</p>
        <Code>
          ~/Library/Application Support/Claude/claude_desktop_config.json
        </Code>
        <p style={S.p}>Windows:</p>
        <Code>%APPDATA%\\Claude\\claude_desktop_config.json</Code>
        <p style={S.p}>
          Add ZizkaDB (merge into existing{" "}
          <code style={codeMonoDark}>mcpServers</code> if you
          have other servers):
        </p>
        <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_API_KEY": "zizkadb_live_xxxx"
      }
    }
  }
}`}</Code>
      </Step>
      <Step n={4} title="Restart Claude Desktop">
        <p style={S.p}>
          Quit completely and reopen. ZizkaDB tools appear in the tool list
          (hammer icon). Claude can call them during any chat.
        </p>
      </Step>

      <h2 style={S.h2}>Cursor</h2>
      <Step n={5} title="Edit Cursor MCP config">
        <p style={S.p}>
          Create or edit{" "}
          <code
            style={codeInlineLight}
          >
            ~/.cursor/mcp.json
          </code>{" "}
          (global) or{" "}
          <code
            style={codeInlineLight}
          >
            .cursor/mcp.json
          </code>{" "}
          in your project:
        </p>
        <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_API_KEY": "zizkadb_live_xxxx"
      }
    }
  }
}`}</Code>
      </Step>
      <Step n={6} title="Reload MCP in Cursor">
        <p style={S.p}>
          Quit Cursor completely (
          <code style={codeMonoDark}>Cmd+Q</code>) and reopen, or
          use Command Palette → <strong>MCP: Reload servers</strong>. Open{" "}
          <code style={codeMonoDark}>Cmd+Shift+J</code> →{" "}
          <strong>Tools &amp; MCP</strong> to confirm{" "}
          <code style={codeMonoDark}>zizkadb</code> is connected.
        </p>
      </Step>

      <h2 style={S.h2}>Windsurf & other MCP clients</h2>
      <p style={S.p}>
        Same JSON block — point your client&apos;s MCP config at{" "}
        <code style={codeMonoDark}>uvx zizkadb-mcp</code> with{" "}
        <code style={codeMonoDark}>ZIZKADB_API_KEY</code> in env.
      </p>

      <h2 style={S.h2}>Self-hosted API</h2>
      <p style={S.p}>
        After{" "}
        <code style={codeMonoDark}>
          bash scripts/setup-local.sh
        </code>
        , paste this (no API key needed on localhost):
      </p>
      <Code lang="json">{`{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_HOST": "http://localhost:8000"
      }
    }
  }
}`}</Code>

      <h2 style={S.h2}>Using MCP in practice</h2>
      <p style={S.p}>
        Once connected, ask the AI naturally — it will call tools when needed:
      </p>
      <div style={{ display: "grid", gap: 10, marginBottom: 24 }}>
        {[
          {
            say: '"Log that we decided to use Postgres for the billing service"',
            tool: "log_event",
          },
          {
            say: '"What did we log about billing last week?"',
            tool: "search_memory",
          },
          {
            say: '"Why did the agent call get_billing in event X?"',
            tool: "why",
          },
          {
            say: '"What was the agent state on May 1 at 3pm?"',
            tool: "time_travel",
          },
        ].map((item) => (
          <div
            key={item.tool}
            style={{
              padding: "12px 16px",
              background: "#fafafa",
              borderRadius: 8,
              border: "1px solid #ebebeb",
            }}
          >
            <div style={{ fontSize: 13.5, color: "#333", marginBottom: 4 }}>
              {item.say}
            </div>
            <code style={{ fontSize: 12, color: "#000000" }}>→ {item.tool}</code>
          </div>
        ))}
      </div>

      <h2 style={S.h2}>All MCP tools</h2>
      <div style={{ display: "grid", gap: 8 }}>
        {[
          {
            tool: "log_event",
            desc: "Log action with optional causal parent_id",
          },
          { tool: "search_memory", desc: "Keyword or hybrid search over agent history" },
          { tool: "verify_chain", desc: "Prove the agent's history is untouched" },
          {
            tool: "get_context",
            desc: "Formatted memory block for system prompts",
          },
          { tool: "why", desc: "Causal chain for an event_id" },
          { tool: "query_events", desc: "List/filter recent events" },
          { tool: "time_travel", desc: "Logged state at a past timestamp" },
          { tool: "memory_diff", desc: "Session summary" },
          { tool: "forget", desc: "GDPR delete by metadata filter" },
        ].map((item) => (
          <div
            key={item.tool}
            style={{
              display: "flex",
              gap: 16,
              padding: "11px 16px",
              background: "#fafafa",
              borderRadius: 8,
              border: "1px solid #ebebeb",
            }}
          >
            <code
              style={{
                fontFamily: "monospace",
                fontSize: 12.5,
                fontWeight: 600,
                color: "#fb923c",
                minWidth: 140,
                flexShrink: 0,
              }}
            >
              {item.tool}
            </code>
            <span style={{ fontSize: 13.5, color: "#444" }}>{item.desc}</span>
          </div>
        ))}
      </div>

      <h2 style={S.h2}>Troubleshooting</h2>
      <div style={{ display: "grid", gap: 12 }}>
        {[
          {
            q: "Tools not showing",
            a: "Restart the app after editing config. Check JSON is valid (no trailing commas).",
          },
          {
            q: "uvx not found",
            a: "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh",
          },
          {
            q: "Auth errors",
            a: "Cloud: verify ZIZKADB_API_KEY. Self-host: set ZIZKADB_HOST to http://localhost:8000 and ensure local API is running (curl http://localhost:8000/health). Dev key is zizkadb_dev_local by default.",
          },
        ].map((item) => (
          <div
            key={item.q}
            style={{
              padding: "14px 18px",
              background: "#fafafa",
              borderRadius: 10,
              border: "1px solid #ebebeb",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4, color: "#1a1a1a" }}>
              {item.q}
            </div>
            <div style={{ fontSize: 13.5, color: "#555" }}>{item.a}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Self-host (expanded) ────────────────────────────────────────────────────

export function SelfHostSection() {
  return (
    <div>
      <h1 style={S.h1}>Self-Host</h1>
      <p style={S.lead}>
        Run the full ZizkaDB stack on your server. Free forever (AGPL), no cloud
        account required. SDK, MCP, and dashboard all connect to the same local
        tenant.
      </p>

      <Callout type="warning">
        <strong>One rule:</strong> SDK/MCP and dashboard must use the{" "}
        <strong>same tenant</strong>.
        <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>
          <li>
            <strong>Local dev (just you):</strong> login →{" "}
            <em>Open my dashboard →</em> + SDK with{" "}
            <code style={codeMonoDark}>host=</code>
          </li>
          <li>
            <strong>Production / team on your server:</strong> email OTP login →
            create API key in Settings → use that key in SDK/MCP
          </li>
        </ul>
        Mixing email login with the auto dev key shows an empty dashboard.
      </Callout>

      <Callout type="info">
        Stack: Postgres + pgvector, Qdrant, Redis, FastAPI. Starts in under 60
        seconds with Docker Compose.
      </Callout>

      <Step n={1} title="Quickstart (recommended)">
        <Code lang="bash">{`git clone https://github.com/ZIZKA-AI-SL/ZizkaDB
cd ZizkaDB
bash scripts/quickstart.sh`}</Code>
        <p style={S.p}>
          One command: Docker stack + <code style={codeMonoDark}>db.why()</code> demo + dashboard link.
          Pre-built images from <code style={codeMonoDark}>ghcr.io/zizka-ai-sl/</code> when available.
        </p>
        <p style={S.p}>
          Stack only: <code style={codeMonoDark}>bash scripts/setup-local.sh</code> ·
          Connect guide:{' '}
          <a href="https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md" target="_blank" rel="noreferrer" style={{ color: '#1e40af' }}>CONNECT.md</a>
        </p>
      </Step>

      <Step n={2} title="Run the lineage demo again">
        <Code lang="bash">{`pip install zizkadb-sdk
zizkadb demo`}</Code>
        <p style={S.p}>
          Worked example:{' '}
          <a href="https://github.com/ZIZKA-AI-SL/ZizkaDB/tree/main/worked/01-support-order-delay" target="_blank" rel="noreferrer" style={{ color: '#1e40af' }}>
            worked/01-support-order-delay
          </a>
        </p>
      </Step>

      <Step n={3} title="Manual setup (optional)">
        <Code lang="bash">{`git clone https://github.com/ZIZKA-AI-SL/ZizkaDB
cd ZizkaDB
cp .env.example infra/.env`}</Code>
        <p style={S.p}>
          Edit <code style={codeMonoDark}>infra/.env</code>:
        </p>
        <Code lang="bash">{`OPENAI_API_KEY=sk-...          # for semantic search
JWT_SECRET=your-random-32-char-secret
DEV_API_KEY=zizkadb_dev_local       # default local dev key (SDK auto-sends this)

# Optional: email OTP for production dashboard login
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=465
EMAIL_USER=you@gmail.com
EMAIL_PASS=your-app-password`}</Code>
      </Step>

      <Step n={4} title="Start the API stack">
        <Code lang="bash">docker compose -f infra/docker-compose.yml up -d --build</Code>
        <Code lang="bash">{`curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}`}</Code>
      </Step>

      <Step n={5} title="Connect SDK or MCP">
        <p style={S.p}>
          Self-hosted SDK calls auto-send the local dev key (
          <code style={codeMonoDark}>zizkadb_dev_local</code> by
          default). Create a named API key in the dashboard Settings when you
          move to production.
        </p>
        <Code lang="python">{`from zizkadb import ZizkaDB
db = ZizkaDB(host="http://localhost:8000")`}</Code>
        <p style={S.p}>
          MCP:{" "}
          <code style={codeMonoDark}>
            ZIZKADB_HOST=http://localhost:8000
          </code>{" "}
          (dev key auto-injected on localhost)
        </p>
        <p style={S.p}>
          REST:{" "}
          <code style={codeMonoDark}>
            Authorization: Bearer zizkadb_dev_local
          </code>
        </p>
      </Step>

      <Step n={6} title="Open the dashboard">
        <p style={S.p}>
          With{" "}
          <code style={codeMonoDark}>
            bash scripts/setup-local.sh
          </code>
          , dashboard is at{" "}
          <code style={codeMonoDark}>
            http://localhost:3001/login
          </code>
          . Click <strong>Open my dashboard →</strong> — no email required.
        </p>
        <p style={S.p}>
          Or run manually:{" "}
          <code style={codeMonoDark}>
            docker compose -f infra/docker-compose.yml -f
            infra/docker-compose.dashboard.yml up -d
          </code>
        </p>
      </Step>

      <Step n={7} title="Production on your VPS">
        <p style={S.p}>For production on a VPS:</p>
        <Code lang="bash">{`docker compose -f infra/docker-compose.yml up -d
bash infra/deploy-selfhost.sh
# Set EMAIL_* in infra/.env for team login; set NEXT_PUBLIC_DEV_MODE=false`}</Code>
        <p style={S.p}>
          Point nginx to :3001 (dashboard) and :8000 (API) — see{" "}
          <code style={codeMonoDark}>infra/nginx.conf</code>.
        </p>
      </Step>

      <h2 style={S.h2}>Troubleshooting</h2>
      <div style={{ display: "grid", gap: 12 }}>
        {[
          {
            q: "API not starting",
            a: "docker compose -f infra/docker-compose.yml logs api --tail=50",
          },
          {
            q: "Dashboard empty but SDK logs work",
            a: 'Use "Open my dashboard →" (dev login) with host= SDK — or create an API key in Settings and use it in your agent.',
          },
          {
            q: "Search not working",
            a: "Set OPENAI_API_KEY in infra/.env and restart api container.",
          },
          {
            q: "Port 8000 in use",
            a: "Change port mapping in infra/docker-compose.yml.",
          },
        ].map((item) => (
          <div
            key={item.q}
            style={{
              padding: "14px 18px",
              background: "#fafafa",
              borderRadius: 10,
              border: "1px solid #ebebeb",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4, color: "#1a1a1a" }}>
              {item.q}
            </div>
            <div style={{ fontSize: 13.5, color: "#555" }}>{item.a}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Frameworks (LangChain, CrewAI, OpenAI) ───────────────────────────────────

export function FrameworksSection() {
  return (
    <div>
      <h1 style={S.h1}>Framework integrations</h1>
      <p style={S.lead}>
        Official adapters and starters — same causal{" "}
        <code style={codeMonoDark}>log()</code> +{" "}
        <code style={codeMonoDark}>parent_id</code> pattern for
        every stack.
      </p>

      <Callout type="tip">
        <strong>Fastest start (Python):</strong>{' '}
        <code style={codeMonoDark}>pip install zizkadb-sdk</code> then{' '}
        <code style={codeMonoDark}>zizkadb init my-agent --template langchain</code>.
        The <code style={codeMonoDark}>zizkadb init</code> CLI ships with the Python SDK only.
        Templates: <code style={codeMonoDark}>basic</code>,{' '}
        <code style={codeMonoDark}>openai</code>,{' '}
        <code style={codeMonoDark}>langchain</code>,{' '}
        <code style={codeMonoDark}>crewai</code>,{' '}
        <code style={codeMonoDark}>mcp-cursor</code>.
      </Callout>

      <h2 style={S.h2}>Package overview</h2>
      <div style={{ display: 'grid', gap: 10, marginBottom: 24 }}>
        {[
          ['Python SDK (zizkadb-sdk)', 'log, why, search, context_for, zizkadb init CLI'],
          ['TypeScript SDK (npm)', 'log, why, search, context_for — HTTP client only'],
          ['zizkadb-langchain', 'Auto-log LangChain LLM + tool steps (Python)'],
          ['zizkadb-crewai', 'Log crew kickoff + output (Python)'],
          ['zizkadb-mcp', 'Cursor / Claude Desktop tools — any language via MCP'],
        ].map(([pkg, desc]) => (
          <div key={pkg} style={{ padding: '12px 16px', background: '#fafafa', borderRadius: 10, border: '1px solid #ebebeb', fontSize: 13.5, color: '#1a1a1a' }}>
            <strong>{pkg}</strong> — {desc}
          </div>
        ))}
      </div>

      <h2 style={{ ...S.h2, marginTop: 24 }}>Scaffold a project</h2>
      <Code lang="bash">{`pip install zizkadb-sdk
zizkadb init my-agent --template basic
cd my-agent && cp .env.example .env
pip install -r requirements.txt
python agent.py`}</Code>

      <h2 style={S.h2}>OpenAI / Anthropic (SDK)</h2>
      <p style={S.p}>
        Use <code style={codeMonoDark}>AsyncOpenAI</code> or{" "}
        <code style={codeMonoDark}>AsyncAnthropic</code> inside{" "}
        <code style={codeMonoDark}>async with ZizkaDB(...)</code>
        . Log the user turn, call the model, log the reply with{" "}
        <code style={codeMonoDark}>parent_id=turn.event_id</code>
        .
      </p>
      <Code lang="python">{`# pip install zizkadb-sdk anthropic
import anthropic
from zizkadb import ZizkaDB

async def run(user_input: str):
    async with ZizkaDB("zizkadb_live_...") as db:
        client = anthropic.AsyncAnthropic()
        turn = await db.log(agent="my-bot", event="user_message", data={"text": user_input})
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": user_input}],
        )
        await db.log(
            agent="my-bot",
            event="assistant_response",
            data={"text": response.content[0].text},
            parent_id=turn.event_id,
        )`}</Code>

      <h2 style={S.h2}>LangChain</h2>
      <p style={S.p}>
        Install{" "}
        <code style={codeMonoDark}>zizkadb-langchain</code> from
        PyPI. Pass{" "}
        <code style={codeMonoDark}>ZizkaDBCallbackHandler</code>{" "}
        in{" "}
        <code style={codeMonoDark}>
          config={"{"}&quot;callbacks&quot;: [handler]{"}"}
        </code>
        .
      </p>
      <Code lang="python">{`pip install zizkadb-sdk zizkadb-langchain langchain-openai

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from zizkadb import ZizkaDB
from zizkadb_langchain import ZizkaDBCallbackHandler

async with ZizkaDB("zizkadb_live_...") as db:
    handler = ZizkaDBCallbackHandler(db, agent="my-bot")
    llm = ChatOpenAI(model="gpt-4o-mini")
    await llm.ainvoke([HumanMessage(content="Hello")], config={"callbacks": [handler]})
    (await db.why(handler.last_event_id)).print()`}</Code>

      <h2 style={S.h2}>CrewAI</h2>
      <p style={S.p}>
        Install <code style={codeMonoDark}>zizkadb-crewai</code>{" "}
        from PyPI.
        <code style={codeMonoDark}>ZizkaDBCrewLogger</code> logs
        kickoff, tasks, and final output with lineage.
      </p>
      <Code lang="python">{`pip install zizkadb-sdk zizkadb-crewai crewai

from zizkadb import ZizkaDB
from zizkadb_crewai import ZizkaDBCrewLogger

async with ZizkaDB("zizkadb_live_...") as db:
    logger = ZizkaDBCrewLogger(db, agent="research-crew")
    kickoff = await logger.log_kickoff(goal="Research topic X")
    output = await crew.kickoff_async()
    await logger.log_output(str(output), parent_id=kickoff.event_id)`}</Code>

      <h2 style={S.h2}>Examples in the repo</h2>
      <p style={S.p}>
        Runnable trees under{" "}
        <code style={codeMonoDark}>examples/</code> on{" "}
        <a
          href="https://github.com/ZIZKA-AI-SL/ZizkaDB"
          style={{ color: "#1e40af" }}
        >
          GitHub
        </a>
        : minimal-python, openai-agent, langchain-agent, crewai-agent,
        mcp-cursor.
      </p>
    </div>
  );
}

// ── Concepts ──────────────────────────────────────────────────────────────────

export function ConceptsSection({
  onNavigate,
}: {
  onNavigate: (s: string) => void;
}) {
  return (
    <div>
      <h1 style={S.h1}>Core Concepts</h1>
      <p style={S.lead}>
        How ZizkaDB thinks about agent memory — events, causality, search,
        replay, and drift.
      </p>
      <Callout type="info">
        Runnable examples:{" "}
        <code style={codeMonoDark}>zizkadb init my-agent</code>{" "}
        or the <code style={codeMonoDark}>examples/</code> folder
        on GitHub. Framework guides in{" "}
        <button
          type="button"
          onClick={() => onNavigate("frameworks")}
          style={{
            background: "none",
            border: "none",
            color: "#1e40af",
            fontWeight: 500,
            cursor: "pointer",
            padding: 0,
            fontSize: "inherit",
          }}
        >
          Framework integrations
        </button>
        .
      </Callout>

      {[
        {
          title: "Events",
          body: "Everything is an event: agent name, event type string, JSON data, timestamp. Log what matters — user messages, tool calls, decisions, errors.",
          code: `await db.log(agent="support-bot", event="tool_call",
    data={"tool": "get_billing", "user_id": 123})`,
        },
        {
          title: "Causal lineage",
          body: 'parent_id links child → parent. db.why() walks from any event back to the root. This is how you debug "why did the agent do that?"',
          code: `msg  = await db.log(..., event="user_message", data={...})
tool = await db.log(..., event="tool_call", parent_id=msg.event_id)
await db.why(tool.event_id)  # user_message → tool_call`,
        },
        {
          title: "Sessions & baselines",
          body: "Use session_id on every event in one run. After enough sessions, baseline() compares recent behavior to historical patterns and returns a drift score.",
          code: `baseline = await db.baseline("support-bot", recent_window=50)
# drift.score: 0 = identical, 1 = totally different`,
        },
        {
          title: "Semantic search",
          body: "Events are embedded automatically (OpenAI). Search in plain English — no SQL, no keywords.",
          code: `results = await db.search("angry customer billing", agent="support-bot")`,
        },
        {
          title: "Time travel",
          body: "at() reconstructs logged state from events up to a timestamp. Replays what was recorded, not a re-run of the LLM.",
          code: `from datetime import datetime, timezone
state = await db.at("support-bot", datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc))`,
        },
        {
          title: "Context injection",
          body: "context_for() returns recent + semantically relevant events within a token budget — paste into your system prompt.",
          code: `ctx = await db.context_for(agent="support-bot", task="refund question", max_tokens=2000)`,
        },
      ].map((concept) => (
        <div
          key={concept.title}
          style={{
            marginBottom: 48,
            paddingBottom: 48,
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <h2 style={{ ...S.h2, marginTop: 0 }}>{concept.title}</h2>
          <p style={S.p}>{concept.body}</p>
          <Code lang="python">{concept.code}</Code>
        </div>
      ))}
    </div>
  );
}
