"""
RAG + streaming logic for the chat assistant.
Keep I/O (DB queries, OpenAI calls) here; views stay thin.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db import models as dj_models

from .models import ChatKnowledgeArticle, ChatMessage, ChatSession

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_SESSION = 30
MAX_HISTORY_MESSAGES = 8
MAX_ARTICLES_IN_CONTEXT = 4
SEARCH_WORDS_LIMIT = 6

_SYSTEM_PROMPT_BASE = """Eres el asistente virtual de Hub de Servicios, una plataforma SaaS \
todo-en-uno para empresas modernas. Tu función es responder preguntas sobre la plataforma, \
sus planes, características y servicios.

Instrucciones:
- Responde en el mismo idioma que usa el usuario (español o inglés).
- Sé conciso, amigable y profesional.
- Si no encuentras la respuesta en el contexto, sugiere contactar al soporte.
- No inventes precios, fechas ni características que no aparezcan en el contexto.
- No respondas preguntas ajenas a la plataforma.

Contexto sobre la empresa:
{articles_context}
"""


def get_relevant_articles(user_message: str) -> list[ChatKnowledgeArticle]:
    """Return up to MAX_ARTICLES_IN_CONTEXT active articles relevant to the message."""
    base_qs = ChatKnowledgeArticle.objects.filter(is_active=True)
    words = [w for w in user_message.lower().split() if len(w) > 2][:SEARCH_WORDS_LIMIT]

    if words:
        query = dj_models.Q()
        for word in words:
            query |= (
                dj_models.Q(title__icontains=word)
                | dj_models.Q(content__icontains=word)
                | dj_models.Q(keywords__contains=[word])
            )
        results = list(base_qs.filter(query)[:MAX_ARTICLES_IN_CONTEXT])
        if results:
            return results

    return list(base_qs.order_by('order')[:3])


def build_system_prompt(articles: list[ChatKnowledgeArticle]) -> str:
    if articles:
        context = '\n\n'.join(f'### {a.title}\n{a.content}' for a in articles)
    else:
        context = 'No hay información específica disponible en este momento.'
    return _SYSTEM_PROMPT_BASE.format(articles_context=context)


def stream_chat_response(session: ChatSession, user_message: str):
    """
    Generator that yields SSE-formatted strings.
    Saves the assistant message to DB after streaming completes.
    """
    from openai import OpenAI, OpenAIError

    articles = get_relevant_articles(user_message)
    system_prompt = build_system_prompt(articles)

    history = list(session.messages.order_by('-created_at')[:MAX_HISTORY_MESSAGES])[::-1]

    messages = [
        {'role': 'system', 'content': system_prompt},
        *[{'role': m.role, 'content': m.content} for m in history],
        {'role': 'user', 'content': user_message},
    ]

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        stream = client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=messages,
            stream=True,
            max_tokens=600,
            temperature=0.4,
        )
    except OpenAIError as exc:
        logger.error('OpenAI API error: %s', exc)
        yield f'data: {json.dumps({"error": "El asistente no está disponible en este momento."})}\n\n'
        yield 'data: [DONE]\n\n'
        return

    full_response = ''
    total_tokens = 0

    for chunk in stream:
        delta = chunk.choices[0].delta
        token = delta.content or ''
        if token:
            full_response += token
            yield f'data: {json.dumps({"token": token})}\n\n'
        if hasattr(chunk, 'usage') and chunk.usage:
            total_tokens = chunk.usage.total_tokens

    ChatMessage.objects.create(
        session=session,
        role='assistant',
        content=full_response,
        tokens_used=total_tokens or None,
    )
    yield 'data: [DONE]\n\n'
