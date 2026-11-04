"use client";

import { useEdition } from "@/hooks/useEdition";

// Note: the old full-width ConnectionStatus banner has been folded into the
// header AccountMenu (see components/dashboard/AccountMenu.tsx) so the content
// area is dedicated to events/debugging. Live health now lives in
// hooks/useConnectionHealth.ts. This module keeps only the onboarding
// checklist shown in the Activity zero-agent state.

export function GettingStartedChecklist() {
  const isOss = useEdition().edition === "oss";
  const snippet = isOss
    ? `# Self-hosted — SDK points at your own instance
pip install zizkadb-sdk
zizkadb demo`
    : `# Managed cloud — use your key from Settings
pip install zizkadb-sdk
python -c "
import asyncio
from zizkadb import ZizkaDB
async def main():
    async with ZizkaDB('zizkadb_live_YOUR_KEY') as db:
        r = await db.log(agent='my-bot', event='started', data={'ok': True})
        print('Logged:', r.event_id)
asyncio.run(main())"`

  const steps = isOss
    ? [
        {
          title: 'Run OSS quickstart',
          desc: 'From the repo: bash scripts/quickstart.sh — starts Docker + db.why() demo',
        },
        {
          title: 'Run the lineage demo',
          desc: 'pip install zizkadb-sdk && zizkadb demo — support-bot order delay scenario',
        },
        {
          title: 'Connect your agent',
          desc: 'See CONNECT.md on GitHub for Python, TypeScript, LangChain, CrewAI, MCP, REST',
        },
      ]
    : [
        {
          title: 'API is running',
          desc: 'Your ZizkaDB API should respond at db.zizka.ai/health',
        },
        {
          title: 'Log your first event',
          desc: 'Run the snippet below. Use the same API key / host as this dashboard account.',
        },
        {
          title: 'Watch it live',
          desc: 'Your agent appears here within 30 seconds — no refresh needed. Click it for events, sessions, and drift.',
        },
      ]

  return (
    <div
      className="rounded-xl p-8"
      style={{ background: "#161c26", border: "1px solid #2a3340" }}
    >
      <h3 className="text-white font-medium mb-1">Getting started</h3>
      <p className="text-sm mb-6" style={{ color: '#e8edf5' }}>
        {isOss
          ? 'OSS quickstart — taste causal lineage, then connect your stack.'
          : 'Three steps to see your first agent in the dashboard.'}
      </p>
      <ol className="space-y-4 mb-6 text-left">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-3">
            <span
              className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
              style={{ background: "#22c55e", color: "#000" }}
            >
              {i + 1}
            </span>
            <div>
              <div className="text-sm font-medium text-white">{step.title}</div>
              <div className="text-xs mt-0.5" style={{ color: "#e8edf5" }}>
                {step.desc}
              </div>
            </div>
          </li>
        ))}
      </ol>
      <pre
        className="text-left rounded-lg p-4 text-xs overflow-x-auto"
        style={{ background: "#10151d", color: "#22c55e" }}
      >
        {snippet}
      </pre>
      <p className="text-xs mt-4" style={{ color: '#e8edf5' }}>
        {isOss ? (
          <>
            Connect guide:{' '}
            <a
              href="https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md"
              target="_blank"
              rel="noreferrer"
              style={{ color: '#e8edf5' }}
            >
              CONNECT.md
            </a>
            {' '}· worked example:{' '}
            <a
              href="https://github.com/ZIZKA-AI-SL/ZizkaDB/tree/main/worked/01-support-order-delay"
              target="_blank"
              rel="noreferrer"
              style={{ color: '#e8edf5' }}
            >
              01-support-order-delay
            </a>
          </>
        ) : (
          <>
            Dashboard empty but SDK works? Your SDK and login must share the same tenant — see{' '}
            <a href="https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki/Self-Hosting" style={{ color: '#e8edf5' }}>Self-host docs</a>.
          </>
        )}
      </p>
    </div>
  );
}
