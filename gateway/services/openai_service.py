import json
import logging
from typing import Any, AsyncGenerator

import httpx
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential
from gateway.schemas import (
    ChatRequest, ChatResponse, ChatUsage, ToolCallFunction, ToolCallOut,
    EmbeddingRequest, EmbeddingResponse, EmbeddingUsage,
    ServiceTestEntry,
)

logger = logging.getLogger(__name__)


async def _get_credentials(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "openai")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("OpenAI credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _get_client(credentials: dict[str, str]) -> AsyncOpenAI:
    # Generous timeouts for reasoning models (o3, o4-mini) that think for
    # minutes before the first token arrives.
    return AsyncOpenAI(
        api_key=credentials["api_key"],
        timeout=httpx.Timeout(connect=10, read=300, write=30, pool=30),
    )


async def chat_completion(body: ChatRequest, session: AsyncSession) -> ChatResponse:
    """Call OpenAI ChatCompletion API."""
    credentials = await _get_credentials(session)
    client = _get_client(credentials)

    kwargs: dict[str, Any] = {
        "model": body.model,
        "messages": body.messages,
    }
    # o-series and gpt-5 models don't support custom temperature
    if not body.model.startswith("o") and not body.model.startswith("gpt-5"):
        kwargs["temperature"] = body.temperature
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    if body.response_format is not None:
        kwargs["response_format"] = body.response_format
    if body.tools is not None:
        kwargs["tools"] = body.tools
    if body.tool_choice is not None:
        kwargs["tool_choice"] = body.tool_choice

    response = await client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    tool_calls: list[ToolCallOut] | None = None
    if choice.message.tool_calls:
        tool_calls = [
            ToolCallOut(
                id=tc.id,
                type=tc.type,
                function=ToolCallFunction(
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ),
            )
            for tc in choice.message.tool_calls
        ]

    return ChatResponse(
        content=choice.message.content,
        model=response.model,
        usage=ChatUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        ),
        finish_reason=choice.finish_reason,
        tool_calls=tool_calls,
    )


async def chat_completion_stream(
    body: ChatRequest, session: AsyncSession
) -> AsyncGenerator[tuple[str, Any], None]:
    """Stream an OpenAI ChatCompletion.

    Yields:
        ("text_delta", str)   — incremental content token
        ("response", ChatResponse) — final accumulated response (always last)
    """
    credentials = await _get_credentials(session)
    client = _get_client(credentials)

    kwargs: dict[str, Any] = {
        "model": body.model,
        "messages": body.messages,
        "stream": True,
    }
    # o-series and gpt-5 models don't support custom temperature
    if not body.model.startswith("o") and not body.model.startswith("gpt-5"):
        kwargs["temperature"] = body.temperature
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    if body.response_format is not None:
        kwargs["response_format"] = body.response_format
    if body.tools is not None:
        kwargs["tools"] = body.tools
    if body.tool_choice is not None:
        kwargs["tool_choice"] = body.tool_choice

    stream = await client.chat.completions.create(**kwargs)

    content_parts: list[str] = []
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    finish_reason = "stop"
    model = body.model

    async for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta

        if choice.finish_reason:
            finish_reason = choice.finish_reason
        if chunk.model:
            model = chunk.model

        if delta and delta.content:
            content_parts.append(delta.content)
            yield ("text_delta", delta.content)

        if delta and delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                entry = tool_calls_acc[idx]
                if tc_delta.id:
                    entry["id"] = tc_delta.id
                if tc_delta.type:
                    entry["type"] = tc_delta.type
                if tc_delta.function:
                    if tc_delta.function.name:
                        entry["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        entry["function"]["arguments"] += tc_delta.function.arguments

    final_tool_calls: list[ToolCallOut] | None = None
    if tool_calls_acc:
        final_tool_calls = [
            ToolCallOut(
                id=tool_calls_acc[i]["id"],
                type=tool_calls_acc[i]["type"],
                function=ToolCallFunction(
                    name=tool_calls_acc[i]["function"]["name"],
                    arguments=tool_calls_acc[i]["function"]["arguments"],
                ),
            )
            for i in sorted(tool_calls_acc.keys())
        ]

    full_content = "".join(content_parts) if content_parts else None
    yield ("response", ChatResponse(
        content=full_content,
        model=model,
        usage=ChatUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        finish_reason=finish_reason,
        tool_calls=final_tool_calls,
    ))


async def embeddings_completion(body: EmbeddingRequest, session: AsyncSession) -> EmbeddingResponse:
    """Call OpenAI Embeddings API."""
    credentials = await _get_credentials(session)
    client = _get_client(credentials)

    response = await client.embeddings.create(
        model=body.model,
        input=body.input,
    )

    return EmbeddingResponse(
        embeddings=[item.embedding for item in response.data],
        model=response.model,
        usage=EmbeddingUsage(
            prompt_tokens=response.usage.prompt_tokens,
            total_tokens=response.usage.total_tokens,
        ),
    )


async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Run tests for OpenAI integration."""
    tests: list[ServiceTestEntry] = []

    # Test 1: Check credentials exist
    try:
        credentials = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="openai_credentials", success=True, detail="Credentials found"))
    except ValueError as e:
        tests.append(ServiceTestEntry(name="openai_credentials", success=False, detail=str(e)))
        return tests

    # Test 2: Try a simple completion
    try:
        client = _get_client(credentials)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
        )
        tests.append(ServiceTestEntry(
            name="openai_chat_completion",
            success=True,
            detail=f"Response: {response.choices[0].message.content}",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="openai_chat_completion", success=False, detail=str(e)))

    return tests
