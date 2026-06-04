"""
Admin da app documents.

Registra UserDocument e UserChunk no Django Admin com interfaces para
inspecao e gerenciamento dos documentos pessoais dos usuarios.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.documents.models import UserChunk, UserDocument


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@admin.action(description="Indexar documentos selecionados")
def action_index_documents(modeladmin, request, queryset):
    count = 0
    for doc in queryset.exclude(status="indexing"):
        doc.trigger_indexing()
        count += 1
    modeladmin.message_user(request, f"{count} documento(s) enfileirado(s) para indexacao.")


@admin.action(description="Re-indexar documentos selecionados")
def action_reindex_documents(modeladmin, request, queryset):
    count = 0
    for doc in queryset.exclude(status="indexing"):
        doc.trigger_reindex()
        count += 1
    modeladmin.message_user(request, f"{count} documento(s) enfileirado(s) para re-indexacao.")


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class UserChunkInline(admin.TabularInline):
    """Exibe os chunks de um documento na pagina do documento (somente leitura)."""

    model = UserChunk
    extra = 0
    fields = ("chunk_index", "content_preview")
    readonly_fields = ("chunk_index", "content_preview")
    ordering = ("chunk_index",)
    max_num = 0  # apenas leitura, sem adicionar

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Previa do conteudo")
    def content_preview(self, obj):
        preview = obj.content[:120]
        if len(obj.content) > 120:
            preview += "..."
        return preview


# ---------------------------------------------------------------------------
# UserDocumentAdmin
# ---------------------------------------------------------------------------


@admin.register(UserDocument)
class UserDocumentAdmin(admin.ModelAdmin):
    """
    Admin do UserDocument.

    Mostra o proprietario, tipo de arquivo, status de indexacao e
    permite acionar reindexacao em massa.
    """

    list_display = (
        "title", "owner", "file_type", "status_badge",
        "chunks_count", "created_at",
    )
    list_filter = ("status", "file_type")
    search_fields = ("title", "owner__username", "owner__email", "error_message")
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "owner", "file", "file_type", "status",
        "chunks_count", "error_message", "created_at", "updated_at",
    )
    actions = [action_index_documents, action_reindex_documents]
    inlines = [UserChunkInline]

    fieldsets = (
        (None, {
            "fields": ("id", "owner", "title"),
        }),
        ("Arquivo", {
            "fields": ("file", "file_type"),
        }),
        ("Indexacao", {
            "fields": ("status", "chunks_count", "error_message"),
        }),
        ("Auditoria", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
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
# UserChunkAdmin
# ---------------------------------------------------------------------------


@admin.register(UserChunk)
class UserChunkAdmin(admin.ModelAdmin):
    """Admin do UserChunk -- somente leitura para inspecao e debug."""

    list_display = ("__str__", "document", "chunk_index", "content_preview", "user_id")
    list_filter = ("document__file_type",)
    search_fields = ("content", "document__title", "document__owner__username")
    ordering = ("document", "chunk_index")
    readonly_fields = (
        "id", "document", "user_id", "chunk_index", "content", "embedding_dim",
    )

    fieldsets = (
        (None, {
            "fields": ("id", "document", "user_id", "chunk_index"),
        }),
        ("Conteudo", {
            "fields": ("content",),
        }),
        ("Embedding", {
            "fields": ("embedding_dim",),
            "classes": ("collapse",),
            "description": "O vetor de embedding nao e exibido diretamente (384 dimensoes).",
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Previa do conteudo")
    def content_preview(self, obj):
        preview = obj.content[:100]
        if len(obj.content) > 100:
            preview += "..."
        return preview

    @admin.display(description="Dimensoes do embedding")
    def embedding_dim(self, obj):
        if obj.embedding is not None:
            return f"{len(obj.embedding)} dimensoes"
        return "-"
