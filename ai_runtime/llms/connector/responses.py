"""OpenAI's Responses API: the protocol its own models are built for.

Chat completions (openai.py) is the protocol every compatible server
copied, and OpenAI still answers it. Responses is the one OpenAI builds
first — its reasoning models think across a tool call only here — so
OpenAI itself, and a gateway that says it speaks this protocol, is
asked this way. The request differs in four places: the system prompt
is ``instructions``, the conversation is ``input``, a tool is declared
flat rather than under ``function``, and the answer is a list of output
items, of which a ``function_call`` is one.

Same client, same key, same address as the chat-completions connector,
which is why this is that class with one method replaced: an embedding
model is asked exactly as it is there.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ai_runtime.llms.connector.openai import OpenAIConnector
from ai_runtime.llms.connector.tools import ModelReply, is_tool_choice_refusal


class OpenAIResponsesConnector(OpenAIConnector):
    #: The least this protocol will take for an output cap.
    LEAST_MAX_TOKENS = 16

    @staticmethod
    def image_block(mime: str, encoded: str) -> dict:
        """A picture, this protocol's way: a data URI again, but as an
        ``input_image`` with the address beside the type, not under it."""
        return {"type": "input_image",
                "image_url": f"data:{mime};base64,{encoded}"}

    @staticmethod
    def _content(role: str, content: Any) -> Any:
        """One message's content. Words as a string go as they are; in
        blocks, a text block is ``input_text`` from the person and
        ``output_text`` from the model."""
        if isinstance(content, str):
            return content
        words = "output_text" if role == "assistant" else "input_text"
        blocks = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                if block.get("text"):
                    blocks.append({"type": words, "text": str(block["text"])})
            else:
                blocks.append(block)
        return blocks

    @classmethod
    def split_messages(cls, messages: list) -> Tuple[str, List[dict]]:
        """The system prompt as ``instructions``, and the rest as
        ``input``: user and assistant turns, unknown roles as user."""
        system_parts: List[str] = []
        converted: List[dict] = []
        for message in messages or []:
            role = message.get("role")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(
                    content if isinstance(content, str)
                    else "\n".join(
                        str(block.get("text") or "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"))
                continue
            role = "assistant" if role == "assistant" else "user"
            converted.append({"role": role, "content": cls._content(role, content)})
        return "\n\n".join(part for part in system_parts if part), converted

    @staticmethod
    def tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """OpenAI's chat tool shape → the same tool, declared flat."""
        declared = []
        for tool in tools or []:
            function = tool.get("function") or {}
            declared.append({
                "type": "function",
                "name": function.get("name"),
                "description": function.get("description") or "",
                "parameters": function.get("parameters")
                or {"type": "object", "properties": {}},
                # The schemas are the agents' own and say what they
                # say; strict mode would refuse most of them.
                "strict": False,
            })
        return declared

    async def chat(self, messages: list, max_tokens: int | None = None,
                   tools: list | None = None) -> ModelReply:
        instructions, converted = self.split_messages(messages)
        kwargs: Dict[str, Any] = {"model": self.model, "input": converted}
        if instructions:
            kwargs["instructions"] = instructions
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max(int(max_tokens), self.LEAST_MAX_TOKENS)
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if tools:
            kwargs["tools"] = self.tools(tools)
            kwargs["tool_choice"] = self.tool_choice
        try:
            response = await self.client.responses.create(**kwargs)
        except Exception as exc:
            if not tools or self.tool_choice == "auto" \
                    or not is_tool_choice_refusal(exc):
                raise
            self.tool_choice = "auto"
            kwargs["tool_choice"] = "auto"
            response = await self.client.responses.create(**kwargs)

        calls, words = [], []
        for item in getattr(response, "output", None) or []:
            kind = getattr(item, "type", None)
            if kind == "function_call":
                calls.append({"name": item.name, "arguments": item.arguments})
            elif kind == "message":
                words.extend(
                    part.text for part in getattr(item, "content", None) or []
                    if getattr(part, "type", None) == "output_text")
        return ModelReply(calls, "".join(words),
                          stop_reason=self.stop_reason(response))

    @staticmethod
    def stop_reason(response: Any) -> str:
        """Why it stopped. This protocol says ``completed`` or
        ``incomplete`` and, beside the second, the reason; a reply cut
        at the cap is ``length``, the word the cycle knows from chat
        completions."""
        details = getattr(response, "incomplete_details", None)
        reason = str(getattr(details, "reason", "") or "")
        if reason == "max_output_tokens":
            return "length"
        return reason or str(getattr(response, "status", "") or "")
