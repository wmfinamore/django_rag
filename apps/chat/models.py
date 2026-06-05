"""
Modelos da app chat.

Hierarquia:
    Conversation  ->  Message

Integração:
    - apps.core.rag_service.RAGService  -- utilizado pelo ChatConsumer para gerar respostas
    - apps.knowledge.models.KnowledgeCollection  -- escopo institucional da conversa
    - apps.accounts.models.CustomUser  -- proprietário da conversa
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    """
    Conversa de um usuário com o assistente RAG.

    Cada conversa tem um escopo definido por:
        - collections: coleções de conhecimento institucional acessíveis
        - use_personal_docs: se inclui documentos pessoais do usuário na busca

    O WebSocket para streaming é aberto por conversa:
        ws://<host>/ws/chat/<conversation_id>/
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        db_comment="UUID da conversa.",
    )
    user = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name="usuário",
        db_comment="Usuário dono da conversa.",
    )
    title = models.CharField(
        "título",
        max_length=255,
        default="Nova conversa",
        help_text="Título exibido na lista de conversas.",
        db_comment="Título descritivo da conversa.",
    )
    collections = models.ManyToManyField(
        "knowledge.KnowledgeCollection",
        blank=True,
        related_name="conversations",
        verbose_name="coleções",
        help_text="Coleções de conhecimento institucional usadas como contexto.",
    )
    use_personal_docs = models.BooleanField(
        "usar documentos pessoais",
        default=False,
        help_text="Se True, inclui documentos pessoais do usuário na busca RAG.",
        db_comment="Inclui UserChunk do usuário na busca vetorial.",
    )

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "-updated_at"], name="chat_conv_user_updated_idx"),
        ]
        db_table_comment = (
            "Conversas dos usuários com o assistente RAG. "
            "Cada conversa define o escopo de busca (coleções + docs pessoais) "
            "e agrega as mensagens do histórico."
        )

    def __str__(self) -> str:
        return f"{self.title} ({self.user.username})"


class Message(models.Model):
    """
    Mensagem dentro de uma Conversation.

    Roles:
        user      -- pergunta enviada pelo usuário
        assistant -- resposta gerada pelo LLM via RAGService

    O campo sources armazena a lista de chunks usados como contexto:
        [{"title": "...", "id": "...", "type": "knowledge|personal", "index": 1}, ...]
    """

    class Role(models.TextChoices):
        USER = "user", "Usuário"
        ASSISTANT = "assistant", "Assistente"

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        db_comment="UUID da mensagem.",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="conversa",
        db_comment="Conversa à qual esta mensagem pertence.",
    )
    role = models.CharField(
        "papel",
        max_length=20,
        choices=Role.choices,
        db_index=True,
        db_comment="Papel do autor: 'user' ou 'assistant'.",
    )
    content = models.TextField(
        "conteúdo",
        help_text="Texto da mensagem.",
        db_comment="Conteúdo textual da mensagem.",
    )
    sources = models.JSONField(
        "fontes",
        default=list,
        blank=True,
        help_text=(
            "Lista de chunks usados como contexto (somente mensagens do assistente). "
            'Formato: [{"title": str, "id": str, "type": str, "index": int}]'
        ),
        db_comment="Fontes RAG usadas para gerar a resposta (JSON).",
    )
    created_at = models.DateTimeField(
        "criado em",
        auto_now_add=True,
        db_index=True,
        db_comment="Timestamp de criação da mensagem.",
    )

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["conversation", "created_at"],
                name="chat_msg_conv_created_idx",
            ),
        ]
        db_table_comment = (
            "Mensagens das conversas. "
            "Mensagens do assistente incluem o campo sources com as fontes RAG usadas."
        )

    def __str__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return f"[{self.role}] {preview}"
