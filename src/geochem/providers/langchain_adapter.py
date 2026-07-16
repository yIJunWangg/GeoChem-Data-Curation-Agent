"""LangChain chat-model adapter backed by GeoChem's audited LLMClient."""

from __future__ import annotations

import json
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from .llm_client import LLMClient


class GeoChemChatModel(BaseChatModel):
    """Expose the existing provider registry to LangChain without bypassing audit.

    The adapter intentionally contains no provider credentials. Every invocation
    still flows through ``LLMClient``, so routing, secret resolution, token cost,
    reasoning isolation and fallback behavior remain identical to the rest of the
    application.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_client: LLMClient = Field(exclude=True)
    task_name: str = "chat_agent"
    project_id: str = ""
    article_id: str = ""
    agent_name: str = "article_curation_chat"
    skill_name: str = "tool_agent"

    @property
    def _llm_type(self) -> str:
        return "geochem-audited-chat"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"task_name": self.task_name, "project_id": self.project_id, "article_id": self.article_id}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice or "auto", **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        provider_kwargs: dict[str, Any] = {}
        if kwargs.get("tools"):
            provider_kwargs["tools"] = kwargs["tools"]
            provider_kwargs["tool_choice"] = kwargs.get("tool_choice") or "auto"
        if stop:
            provider_kwargs["stop"] = stop
        response = self.llm_client.chat(
            [self._message_payload(message) for message in messages],
            task_name=self.task_name,
            project_id=self.project_id,
            article_id=self.article_id,
            agent_name=self.agent_name,
            skill_name=self.skill_name,
            use_cache=False,
            provider_kwargs=provider_kwargs,
        )
        tool_calls = [
            {
                "name": str(call.get("name") or ""),
                "args": call.get("args") if isinstance(call.get("args"), dict) else {},
                "id": str(call.get("id") or ""),
                "type": "tool_call",
            }
            for call in response.tool_calls
            if call.get("name")
        ]
        message = AIMessage(
            content=response.final_content or "",
            tool_calls=tool_calls,
            response_metadata={
                "provider": response.provider,
                "model": response.model,
                "finish_reason": response.finish_reason,
                "reasoning_present": response.reasoning_present,
            },
            usage_metadata={
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": response.total_tokens,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _message_payload(message: BaseMessage) -> dict[str, Any]:
        content = message.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if isinstance(message, SystemMessage):
            return {"role": "system", "content": content}
        if isinstance(message, HumanMessage):
            return {"role": "user", "content": content}
        if isinstance(message, ToolMessage):
            return {"role": "tool", "content": content, "tool_call_id": message.tool_call_id}
        if isinstance(message, AIMessage):
            payload: dict[str, Any] = {"role": "assistant", "content": content}
            if message.tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": call.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": call.get("name") or "",
                            "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            return payload
        return {"role": "user", "content": content}
