"""LangChain async callback handler → ZizkaDB causal events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from zizkadb import ZizkaDB
from zizkadb.models import LogResult

_RUN_PARENT_MAX = 2_000  # prevent unbounded growth across long sessions


class ZizkaDBCallbackHandler(AsyncCallbackHandler):
    """
    Log LangChain LLM and tool steps to ZizkaDB with parent_id lineage.

    Usage:
        handler = ZizkaDBCallbackHandler(db, agent="my-bot")
        await llm.ainvoke(messages, config={"callbacks": [handler]})
    """

    def __init__(
        self,
        db: ZizkaDB,
        agent: str,
        session_id: str | None = None,
    ) -> None:
        self.db = db
        self.agent = agent
        self.session_id = session_id
        self.last_event_id: str | None = None
        self._run_parents: dict[UUID, str] = {}
        # Tool name per run, so tool_end / tool_error say which tool they belong to.
        self._run_tools: dict[UUID, str] = {}

    # ── LLM ───────────────────────────────────────────────────────────────

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        flat = []
        for batch in messages:
            for m in batch:
                role = getattr(m, "type", m.__class__.__name__)
                content = getattr(m, "content", str(m))
                flat.append({"role": role, "content": str(content)[:2000]})
        result = await self._log(
            event="llm_start",
            data={"messages": flat, "model": serialized.get("name", "chat")},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self._run_parents[run_id] = result.event_id
        self.last_event_id = result.event_id

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        texts = []
        for gen_list in response.generations:
            for gen in gen_list:
                texts.append(gen.text[:4000] if gen.text else "")
        data: dict[str, Any] = {"text": "\n".join(texts), "generation_count": len(texts)}
        llm_output = response.llm_output or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(token_usage, dict) and token_usage:
            data["token_usage"] = token_usage
        result = await self._log(
            event="llm_end",
            data=data,
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        result = await self._log(
            event="llm_error",
            data={"error": str(error), "type": type(error).__name__},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id

    # ── Chain ─────────────────────────────────────────────────────────────

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        chain_name = serialized.get("name") or serialized.get("id", ["chain"])[-1]
        result = await self._log(
            event="chain_start",
            data={"chain": chain_name, "input_keys": list(inputs.keys())},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self._run_parents[run_id] = result.event_id
        self.last_event_id = result.event_id

    async def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        result = await self._log(
            event="chain_end",
            data={"output_keys": list(outputs.keys())},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id
        self._run_parents.pop(run_id, None)

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        result = await self._log(
            event="chain_error",
            data={"error": str(error), "type": type(error).__name__},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id
        self._run_parents.pop(run_id, None)

    # ── Retriever / RAG ───────────────────────────────────────────────────

    async def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        result = await self._log(
            event="retrieval",
            data={
                "query": query[:2000],
                "retriever": serialized.get("name", "retriever"),
            },
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self._run_parents[run_id] = result.event_id
        self.last_event_id = result.event_id

    async def on_retriever_end(
        self,
        documents: list[Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        doc_ids = []
        scores: list[float] = []
        for i, doc in enumerate(documents[:20]):
            meta = getattr(doc, "metadata", None) or {}
            doc_ids.append(str(meta.get("id") or meta.get("chunk_id") or i))
            score = meta.get("score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        result = await self._log(
            event="retrieval",
            data={
                "chunk_count": len(documents),
                "doc_ids": doc_ids,
                "scores": scores[:20],
            },
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id
        self._run_parents.pop(run_id, None)

    # ── Tool ──────────────────────────────────────────────────────────────

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        tool = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        if len(self._run_tools) >= _RUN_PARENT_MAX:
            self._run_tools.clear()
        self._run_tools[run_id] = tool
        result = await self._log(
            event="tool_start",
            data={"tool": tool, "input": input_str[:2000]},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self._run_parents[run_id] = result.event_id
        self.last_event_id = result.event_id

    async def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        text = output if isinstance(output, str) else str(output)
        result = await self._log(
            event="tool_end",
            data={**self._tool_field(run_id), "output": text[:4000]},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id
        self._run_parents.pop(run_id, None)

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Log tool failures — previously untracked, breaking causal lineage on errors."""
        result = await self._log(
            event="tool_error",
            data={**self._tool_field(run_id), "error": str(error), "type": type(error).__name__},
            run_id=run_id,
            parent_run_id=parent_run_id,
        )
        self.last_event_id = result.event_id
        self._run_parents.pop(run_id, None)

    # ── Internal ──────────────────────────────────────────────────────────

    def _tool_field(self, run_id: UUID) -> dict[str, str]:
        tool = self._run_tools.pop(run_id, None)
        return {"tool": tool} if tool else {}

    async def _log(
        self,
        event: str,
        data: dict[str, Any],
        run_id: UUID,
        parent_run_id: UUID | None,
    ) -> LogResult:
        parent_id = None
        if event.endswith(("_end", "_error")):
            parent_id = self._run_parents.get(run_id)
        if parent_id is None and parent_run_id and parent_run_id in self._run_parents:
            parent_id = self._run_parents[parent_run_id]
        data = {**data, "run_id": str(run_id)}
        result = await self.db.log(
            agent=self.agent,
            event=event,
            data=data,
            parent_id=parent_id,
            session_id=self.session_id,
        )
        if event.endswith("_start"):
            self._run_parents[run_id] = result.event_id
        elif event.endswith(("_end", "_error")):
            self._run_parents.pop(run_id, None)
        # Evict oldest entries if the dict grows too large
        if len(self._run_parents) > _RUN_PARENT_MAX:
            oldest = next(iter(self._run_parents))
            del self._run_parents[oldest]
        return result
