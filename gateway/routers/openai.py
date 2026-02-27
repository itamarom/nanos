from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission
from gateway.schemas import ChatRequest, ChatResponse, EmbeddingRequest, EmbeddingResponse
from gateway.services import openai_service

router = APIRouter()


@router.post("/chat", response_model=ChatResponse, name="openai.chat")
async def chat(
    body: ChatRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    """Send a chat completion request to OpenAI."""
    check_permission(nano, "openai.chat")

    try:
        return await openai_service.chat_completion(body, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {e}")


@router.post("/embeddings", response_model=EmbeddingResponse, name="openai.embeddings")
async def embeddings(
    body: EmbeddingRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> EmbeddingResponse:
    """Generate embeddings via OpenAI."""
    check_permission(nano, "openai.embeddings")

    try:
        return await openai_service.embeddings_completion(body, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {e}")
