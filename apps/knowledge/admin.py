"""
Admin da app knowledge.

Registra KnowledgeCollection, KnowledgeDocument e KnowledgeChunk no Django Admin
com interfaces adequadas para gerenciamento da base de conhecimento institucional.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.knowledge.models import KnowledgeChunk, KnowledgeCollection, KnowledgeDocument


# ---------------------------------------------------------------------------
# Inline: documentos dentro de uma coleção
# ---------------------------------------------------------------------------


class KnowledgeDocumentInline(admin.TabularInline):
    """Exibe os documentos de uma coleção diretamente na página da coleção."""

    model = KnowledgeDocument
    extra = 0
    fields = ("title", "file_type", "status", "chunks_count", "ingested_by", "created_at")
    readonly_fields = ("status", "chunks_count", "created_at")
    show_change_link = True
    ordering = ("-created_at",)


# ---------------------------------------------------------------------------
# KnowledgeCollectionAdmin
# ---------------------------------------------------------------------------


@admin.register(KnowledgeCollection)
class KnowledgeCollectionAdmin(admin.ModelAdmin):
    """
    Admin da KnowledgeCollection.

    Permite criar e gerenciar coleções, definir grupos de acesso e
    visualizar os documentos associados.
    """

    list_display = ("name", "is_active", "document_count", "groups_display", "created_at")
    list_filter = ("is_active", "allowed_groups")
    search_fields = ("name", "description")
    ordering = ("name",)
    filter_horizontal = ("allowed_groups",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [KnowledgeDocumentInline]

    fieldsets = (
        (None, {
            "fields": ("name", "description", "is_active"),
        }),
        ("Controle de Acesso", {
            "fields": ("allowed_groups",),
            "description": (
                "Deixe em branco para acesso público a qualquer usuário autenticado. "
                "Selecione grupos para restringir o acesso."
            ),
        }),
        ("Auditoria", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Documentos")
    def document_count(self, obj):
        """Exibe a contagem de documentos na coleção."""
        count = obj.documents.count()
        ready = obj.documents.filter(status="ready").count()
        return f"{ready}/{count} prontos"

    @admin.display(description="Grupos")
    def groups_display(self, obj):
        """Exibe os grupos com acesso à coleção."""
        groups = obj.allowed_groups.all()
        if not groups:
            return format_html('<span style="color: #888;">público</span>')
        return ", ".join(g.name for g in groups)


# ---------------------------------------------------------------------------
# KnowledgeDocumentAdmin
# ---------------------------------------------------------------------------


@admin.action(description="Indexar documentos selecionados")
def action_index_documents(modeladmin, request, queryset):
    """Enfileira a indexação Celery para cada documento selecionado."""
    count = 0
    for doc in queryset.exclude(status="indexing"):
        doc.trigger_indexing()
        count += 1
    modeladmin.message_user(request, f"{count} documento(s) enfileirado(s) para indexação.")


@admin.action(description="Re-indexar documentos selecionados")
def action_reindex_documents(modeladmin, request, queryset):
    """Enfileira a re-indexação (delete + index) para cada documento selecionado."""
    count = 0
    for doc in queryset.exclude(status="indexing"):
        doc.trigger_reindex()
        count += 1
    modeladmin.message_user(request, f"{count} documento(s) enfileirado(s) para re-indexação.")


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    """
    Admin do KnowledgeDocument.

    Exibe o estado de indexação de cada documento, permite acionar
    indexação/re-indexação via ações e mostra os chunks associados.
    """

    list_display = (
        "title",
        "collection",
        "file_type",
        "status_badge",
        "chunks_count",
        "ingested_by",
        "created_at",
    )
    list_filter = ("status", "file_type", "collection")
    search_fields = ("title", "file_path", "error_message")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "status",
        "chunks_count",
        "error_message",
        "created_at",
        "updated_at",
    )
    actions = [action_index_documents, action_reindex_documents]

    fieldsets = (
        (None, {
            "fields": ("id", "collection", "title", "ingested_by"),
        }),
        ("Arquivo", {
            "fields": ("file_path", "file_type"),
        }),
        ("Indexação", {
            "fields": ("status", "chunks_count", "error_message"),
        }),
        ("Auditoria", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        """Exibe o status do documento com cor indicativa."""
        colors = {
            "pending": "#888",
            "indexing": "#1565c0",
            "ready": "#2e7d32",
            "error": "#c62828",
        }
        color = colors.get(obj.status, "#888")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )


# ---------------------------------------------------------------------------
# KnowledgeChunkAdmin
# ---------------------------------------------------------------------------


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    """
    Admin do KnowledgeChunk — somente leitura.

    Chunks são gerados automaticamente pela task de indexação.
    O admin serve apenas para inspeção e debug.
    """

    list_display = ("__str__", "document", "chunk_index", "content_preview", "collection_id")
    list_filter = ("document__collection", "document__file_type")
    search_fields = ("content", "document__title")
    ordering = ("document", "chunk_index")
    readonly_fields = ("id", "document", "collection_id", "chunk_index", "content", "embedding_dim")

    fieldsets = (
        (None, {
            "fields": ("id", "document", "collection_id", "chunk_index"),
        }),
        ("Conteúdo", {
            "fields": ("content",),
        }),
        ("Embedding", {
            "fields": ("embedding_dim",),
            "classes": ("collapse",),
            "description": "O vetor de embedding não é exibido diretamente (384 dimensões).",
        }),
    )

    def has_add_permission(self, request):
        """Chunks são gerados automaticamente — não permitir criação manual."""
        return False

    def has_change_permission(self, request, obj=None):
        """Chunks não devem ser editados manualmente."""
        return False

    @admin.display(description="Prévia do conteúdo")
    def content_preview(self, obj):
        """Exibe os primeiros 100 caracteres do conteúdo do chunk."""
        preview = obj.content[:100]
        if len(obj.content) > 100:
            preview += "…"
        return preview

    @admin.display(description="Dimensões do embedding")
    def embedding_dim(self, obj):
        """Exibe o número de dimensões do embedding (sem mostrar o vetor inteiro)."""
        if obj.embedding is not None:
            return f"{len(obj.embedding)} dimensões"
        return "—"
