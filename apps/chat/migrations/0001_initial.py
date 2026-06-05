"""
Migration inicial da app chat.

Cria as tabelas:
    chat_conversation
    chat_conversation_collections  (M2M)
    chat_message
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("knowledge", "0002_bulkimportjob_knowledgedocument_bulk_import_job"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Conversation",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="criado em",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="atualizado em",
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID da conversa.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        db_comment="Título descritivo da conversa.",
                        default="Nova conversa",
                        help_text="Título exibido na lista de conversas.",
                        max_length=255,
                        verbose_name="título",
                    ),
                ),
                (
                    "use_personal_docs",
                    models.BooleanField(
                        db_comment="Inclui UserChunk do usuário na busca vetorial.",
                        default=False,
                        help_text="Se True, inclui documentos pessoais do usuário na busca RAG.",
                        verbose_name="usar documentos pessoais",
                    ),
                ),
                (
                    "collections",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Coleções de conhecimento institucional usadas como contexto.",
                        related_name="conversations",
                        to="knowledge.knowledgecollection",
                        verbose_name="coleções",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_comment="Usuário dono da conversa.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="usuário",
                    ),
                ),
            ],
            options={
                "verbose_name": "conversa",
                "verbose_name_plural": "conversas",
                "ordering": ["-updated_at"],
                "db_table_comment": (
                    "Conversas dos usuários com o assistente RAG. "
                    "Cada conversa define o escopo de busca (coleções + docs pessoais) "
                    "e agrega as mensagens do histórico."
                ),
            },
        ),
        migrations.CreateModel(
            name="Message",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID da mensagem.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("user", "Usuário"), ("assistant", "Assistente")],
                        db_comment="Papel do autor: 'user' ou 'assistant'.",
                        db_index=True,
                        max_length=20,
                        verbose_name="papel",
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        db_comment="Conteúdo textual da mensagem.",
                        help_text="Texto da mensagem.",
                        verbose_name="conteúdo",
                    ),
                ),
                (
                    "sources",
                    models.JSONField(
                        blank=True,
                        db_comment="Fontes RAG usadas para gerar a resposta (JSON).",
                        default=list,
                        help_text=(
                            "Lista de chunks usados como contexto (somente mensagens do assistente). "
                            'Formato: [{"title": str, "id": str, "type": str, "index": int}]'
                        ),
                        verbose_name="fontes",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_comment="Timestamp de criação da mensagem.",
                        db_index=True,
                        verbose_name="criado em",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        db_comment="Conversa à qual esta mensagem pertence.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="chat.conversation",
                        verbose_name="conversa",
                    ),
                ),
            ],
            options={
                "verbose_name": "mensagem",
                "verbose_name_plural": "mensagens",
                "ordering": ["created_at"],
                "db_table_comment": (
                    "Mensagens das conversas. "
                    "Mensagens do assistente incluem o campo sources com as fontes RAG usadas."
                ),
            },
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["user", "-updated_at"],
                name="chat_conv_user_updated_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "created_at"],
                name="chat_msg_conv_created_idx",
            ),
        ),
    ]
