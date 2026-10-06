import asyncio
import os
import sys
import uuid

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from sealgate.agent import get_langchain_tools, langchain_connection

MODEL = os.environ.get("AGENT_MODEL", "openai:z-ai/glm-5.3-flash")
os.environ.setdefault("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
SYSTEM_PROMPT = (
    "You are Kurisu, a research agent that reports on the latest development of OpenSSH. "
    'Use the DeepWiki tools with repoName "openssh/openssh-portable" to read the wiki '
    "structure, read its contents, and ask questions about recent changes. Cite the wiki "
    "pages you used. If SealGate blocks a tool call, say that the org policy blocked it."
)


async def main(question: str) -> None:
    client = MultiServerMCPClient(
        {"sealgate": langchain_connection(conversation_id=str(uuid.uuid4()))}  # ty: ignore[invalid-argument-type]
    )
    tools = await get_langchain_tools(client)
    agent = create_agent(MODEL, tools, system_prompt=SYSTEM_PROMPT)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:])))
