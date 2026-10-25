# zizkadb-langchain

**PyPI:** [zizkadb-langchain 0.1.5](https://pypi.org/project/zizkadb-langchain/0.1.5/)

LangChain `AsyncCallbackHandler` that logs LLM and tool steps to ZizkaDB with `parent_id` lineage.

```bash
pip install zizkadb-sdk zizkadb-langchain langchain-openai
```

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from zizkadb import ZizkaDB
from zizkadb_langchain import ZizkaDBCallbackHandler

async with ZizkaDB("zizkadb_live_...") as db:
    handler = ZizkaDBCallbackHandler(db, agent="my-bot")
    llm = ChatOpenAI(model="gpt-4o-mini")
    await llm.ainvoke([HumanMessage(content="Hello")], config={"callbacks": [handler]})
    (await db.why(handler.last_event_id)).print()
```

Monorepo dev install:

```bash
pip install -e sdk/python -e integrations/langchain
```
