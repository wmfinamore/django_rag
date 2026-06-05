"""
ChatConsumer — WebSocket para streaming de respostas RAG.

Protocolo de mensagens (JSON):

    Cliente → Servidor:
        {"message": "<texto da pergunta>"}

    Servidor → Cliente:
        {"type": "token",  "token": "<fragmento>"}           — durante streaming
        {"type": "done",   "sources": [...], "message_id": "<uuid>"}  — ao finalizar
        {"type": "error",  "error": "<mensagem>"}            — em caso de falha
        {"type": "info",   "text": "<mensagem>"}             — mensagens informativas

Autenticação:
    Usa sessão Django via AuthMiddlewareStack (config/asgi.py).
    Conexões não autenticadas ou sem acesso à conversa são recusadas (close 4001/4003).

Streaming:
    O RAGService.stream() é síncrono. Para não bloquear o event loop do asyncio,
    a geração é executada em uma thread separada. Os tokens são enviados ao consumer
    via asyncio.Queue usando loop.call_soon_threadsafe().
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

# Código de fechamento customizado — não autenticado
WS_CLOSE_UNAUTHORIZED = 4001
# Código de fechamento customizado — conversa não encontrada / sem permissão
WS_CLOSE_FORBIDDEN = 4003


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Consumer WebSocket para uma Conversation específica.

    Ciclo de vida:
        connect()   → autentica usuário, valida conversa, aceita conexão
        receive()   → processa query, faz streaming RAG, salva mensagens
        disconnect() → cleanup (noop por ora)
    """

    # ------------------------------------------------------------------
    # Ciclo de vida da conexão
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self.conversation_id: str = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.conversation = None

        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            logger.warning("ChatConsumer: conexão recusada — usuário não autenticado.")
            await self.close(code=WS_CLOSE_UNAUTHORIZED)
            return

        self.conversation = await self._get_conversation(user, self.conversation_id)
        if self.conversation is None:
            logger.warning(
                "ChatConsumer: conversa %s não encontrada ou sem permissão para %s.",
                self.conversation_id,
                user.username,
            )
            await self.close(code=WS_CLOSE_FORBIDDEN)
            return

        await self.accept()
        logger.info(
            "ChatConsumer: %s conectado à conversa %s.",
            user.username,
            self.conversation_id,
        )

    async def disconnect(self, close_code: int) -> None:
        logger.debug("ChatConsumer: desconectado (code=%s).", close_code)

    # ------------------------------------------------------------------
    # Recebimento de mensagens
    # ------------------------------------------------------------------

    async def receive(self, text_data: str) -> None:
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("Payload inválido — esperado JSON.")
            return

        query = (data.get("message") or "").strip()
        if not query:
            await self._send_error("Campo 'message' ausente ou vazio.")
            return

        user = self.scope["user"]
        logger.info(
            "ChatConsumer: query recebida de %s na conversa %s: %.80s…",
            user.username,
            self.conversation_id,
            query,
        )

        # Salva mensagem do usuário
        await self._save_message("user", query)

        # Streaming RAG
        full_response, sources, error = await self._stream_rag(query)

        if error:
            await self._send_error(error)
            return

        # Salva resposta do assistente
        assistant_msg = await self._save_message("assistant", full_response, sources)

        # Sinaliza conclusão ao cliente
        await self.send(
            text_data=json.dumps(
                {
                    "type": "done",
                    "sources": sources,
                    "message_id": str(assistant_msg.id),
                },
                ensure_ascii=False,
            )
        )

    # ------------------------------------------------------------------
    # Streaming RAG via thread + asyncio.Queue
    # ------------------------------------------------------------------

    async def _stream_rag(
        self, query: str
    ) -> tuple[str, list[dict], str | None]:
        """
        Executa o pipeline RAG em uma thread separada e envia tokens via WebSocket.

        Retorna (full_response, sources, error_message).
        """
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        # Snapshot dos atributos da conversa para uso na thread
        collection_ids = await self._get_collection_ids()
        use_personal_docs = await self._get_use_personal_docs()
        user = self.scope["user"]

        def _producer() -> None:
            """Roda em thread separada; envia itens para a queue via call_soon_threadsafe."""
            try:
                from apps.core.rag_service import SYSTEM_PROMPT, RAGService

                service = RAGService(
                    user=user,
                    collection_ids=[str(cid) for cid in collection_ids],
                    use_personal_docs=use_personal_docs,
                )

                # Monta contexto (embedding + retrieval + reranking)
                ctx = service.build_context(query)
                sources = ctx.sources

                # Instancia LLM e faz streaming síncrono
                llm = service._ollama_client()
                full_prompt = f"{SYSTEM_PROMPT}\n\n{ctx.prompt}"
                full_response = ""

                for token in llm.stream(full_prompt):
                    full_response += token
                    loop.call_soon_threadsafe(queue.put_nowait, ("token", token))

                loop.call_soon_threadsafe(
                    queue.put_nowait, ("done", full_response, sources)
                )

            except Exception as exc:  # noqa: BLE001
                logger.exception("ChatConsumer: erro no pipeline RAG: %s", exc)
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

        # Inicia thread produtora
        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()

        # Consome tokens da queue e envia ao cliente
        full_response = ""
        sources: list[dict] = []
        error: str | None = None

        while True:
            item = await queue.get()
            kind = item[0]

            if kind == "token":
                token: str = item[1]
                full_response += token
                await self.send(
                    text_data=json.dumps(
                        {"type": "token", "token": token},
                        ensure_ascii=False,
                    )
                )

            elif kind == "done":
                full_response = item[1]
                sources = item[2]
                break

            elif kind == "error":
                error = item[1]
                break

        # Aguarda a thread finalizar (já deve ter terminado)
        thread.join(timeout=5)

        return full_response, sources, error

    # ------------------------------------------------------------------
    # Helpers de banco de dados (sync_to_async)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _get_conversation(self, user, conversation_id: str):
        from apps.chat.models import Conversation

        try:
            return Conversation.objects.get(id=conversation_id, user=user)
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_collection_ids(self) -> list:
        return list(
            self.conversation.collections.values_list("id", flat=True)
        )

    @database_sync_to_async
    def _get_use_personal_docs(self) -> bool:
        return self.conversation.use_personal_docs

    @database_sync_to_async
    def _save_message(self, role: str, content: str, sources: list | None = None):
        from apps.chat.models import Message

        msg = Message.objects.create(
            conversation=self.conversation,
            role=role,
            content=content,
            sources=sources or [],
        )
        # Atualiza updated_at da conversa (auto_now via save)
        self.conversation.save(update_fields=["updated_at"])
        return msg

    # ------------------------------------------------------------------
    # Helpers de envio
    # ------------------------------------------------------------------

    async def _send_error(self, message: str) -> None:
        await self.send(
            text_data=json.dumps(
                {"type": "error", "error": message},
                ensure_ascii=False,
            )
        )
