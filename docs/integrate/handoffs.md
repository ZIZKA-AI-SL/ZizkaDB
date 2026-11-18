# Agent-to-agent handoffs (ZizkaDB Cloud)

> **ZizkaDB Cloud only.** Handoffs connect two or more agents, and multiple agents are a [ZizkaDB Cloud](https://db.zizka.ai) feature. Self-hosted ZizkaDB (this repository) runs **one agent with one API key**; against a self-hosted server `handoff()` raises `CloudOnlyError` (TypeScript: throws it; MCP `send_handoff` returns that error).

When one agent hands work to another (another process, framework, language or LLM), record the message and pass a **handoff token** with it. The receiver continues from the token, so `why()` on the receiver walks back into the sender.

```python
# Sender (e.g. a LangChain agent on OpenAI)
h = await db.handoff(agent="planner", to="billing", message="Refund order 4411")
httpx.post(billing_url, json=payload, headers=h.headers)   # X-ZizkaDB-Handoff: zdbh1.…

# Receiver (any framework, any process)
token = zizkadb.token_from_headers(request.headers)
async with db.continue_from(token, agent="billing"):
    await db.log(agent="billing", event="tool_call", data={...})   # linked to the message
```

```ts
const h = await db.handoff({ agent: 'planner', to: 'billing', message: 'Refund order 4411' })
await fetch(url, { method: 'POST', body, headers: h.headers })
// receiver
const ctx = await db.continueFrom(tokenFromHeaders(req.headers)!, { agent: 'billing' })
await ctx.run(() => db.log({ agent: 'billing', event: 'tool_call', data: {} }))
```

MCP: `send_handoff(agent, to_agent, message)` returns a token; the other agent calls `continue_handoff(handoff_token, agent)`.

- **Token:** `zdbh1.` + base64url JSON with ids only (message event, session, sender, recipient). Not a secret: the server checks every link belongs to your account. Python, TypeScript and MCP share the format.
- **Both sides are recorded** by ZizkaDB Cloud (`/v1/a2a/messages`): `a2a.message` in the sender's chain, `a2a.received` in the recipient's. Each agent uses its own agent-scoped API key.
- **Agent version:** `ZizkaDB(agent_version="prompt-v7")` (TS `agentVersion`, or `ZIZKADB_AGENT_VERSION`) records `metadata.agent_version` on every event.
