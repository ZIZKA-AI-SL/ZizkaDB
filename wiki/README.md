# ZizkaDB Wiki (source)

Markdown files in this folder are the **source** for the [GitHub Wiki](https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki).

## Publish to GitHub Wiki

1. Enable wiki: **Repo → Settings → Features → Wikis** ✓
2. Run:

```bash
bash wiki/push-wiki.sh
```

3. Open https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki

## Pages

| File | Topic |
|------|-------|
| `Home.md` | Landing page |
| `Getting-Started.md` | First agent + first event |
| `Agents-and-API-Keys.md` | Per-agent vs tenant-wide keys |
| `Python-SDK.md` | Python integration |
| `TypeScript-SDK.md` | TypeScript / Node |
| `MCP-and-Cursor.md` | Cursor MCP setup |
| `REST-API.md` | HTTP API reference |
| `Multi-Agent-Apps.md` | SaaS / zizka.ai pattern |
| `Self-Hosting.md` | Docker local + VPS |
| `Production-Deployment.md` | db.zizka.ai EC2 |
| `Troubleshooting.md` | Common errors |
| `Architecture.md` | System design |

Edit these files, commit to main, then run `push-wiki.sh` to sync.
