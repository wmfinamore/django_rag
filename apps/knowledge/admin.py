"""
Admin da app knowledge.

Registra KnowledgeCollection, KnowledgeDocument, KnowledgeChunk e BulkImportJob
no Django Admin com interfaces para gerenciamento da base de conhecimento institucional,
incluindo acompanhamento de importacoes em lote a partir de diretorios no disco.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.knowledge.models import (
    BulkImportJob,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class KnowledgeDocumentInline(admin.TabularInline):
    """Exibe os documentos de uma colecao diretamente na pagina da colecao."""

    model = KnowledgeDocument
    extra = 0
    fields = (
        "title", "file_type", "status", "chunks_count",
        "ingested_by", "bulk_import_job", "created_at",
    )
    readonly_fields = ("status", "chunks_count", "bulk_import_job", "created_at")
    show_change_link = True
    ordering = ("-created_at",)


class BulkImportJobInline(admin.TabularInline):
    """Exibe os jobs de importacao em lote diretamente na pagina da colecao."""

    model = BulkImportJob
    extra = 0
    fields = (
        "source_directory", "status_badge_inline",
        "total_files", "indexed_files", "failed_files",
        "triggered_by", "created_at",
    )
    readonly_fields = (
        "source_directory", "status_badge_inline",
        "total_files", "indexed_files", "failed_files",
        "triggered_by", "created_at",
    )
    show_change_link = True
    ordering = ("-created_at",)

    @admin.display(description="Status")
    def status_badge_inline(self, obj):
        colors = {
            "pending": "#888",
            "running": "#1565c0",
            "completed": "#2e7d32",
            "completed_with_errors": "#e65100",
            "failed": "#c62828",
        }
        color = colors.get(obj.status, "#888")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )


# ---------------------------------------------------------------------------
# KnowledgeCollectionAdmin
# ---------------------------------------------------------------------------


@admin.register(KnowledgeCollection)
class KnowledgeCollectionAdmin(admin.ModelAdmin):
    """Admin de KnowledgeCollection com inlines de documentos e jobs de importacao."""

    list_display = ("name", "is_active", "document_count", "groups_display", "created_at")
    list_filter = ("is_active", "allowed_groups")
    search_fields = ("name", "description")
    ordering = ("name",)
    filter_horizontal = ("allowed_groups",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [KnowledgeDocumentInline, BulkImportJobInline]

    fieldsets = (
        (None, {
            "fields": ("name", "description", "is_active"),
        }),
        ("Controle de Acesso", {
            "fields": ("allowed_groups",),
            "description": (
                "Deixe em branco para acesso publico a qualquer usuario autenticado. "
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
        count = obj.documents.count()
        ready = obj.documents.filter(status="ready").count()
        return f"{ready}/{count} prontos"

    @admin.display(description="Grupos")
    def groups_display(self, obj):
        groups = obj.allowed_groups.all()
        if not groups:
            return format_html('<span style="color: #888;">publico</span>')
        return ", ".join(g.name for g in groups)


# ---------------------------------------------------------------------------
# KnowledgeDocumentAdmin
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


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    """
    Admin do KnowledgeDocument.

    Mostra origem do documento (upload manual vs importacao em lote),
    status de indexacao e permite acionar reindexacao em massa.
    """

    list_display = (
        "title", "collection", "file_type", "status_badge",
        "chunks_count", "ingested_by", "has_bulk_job", "created_at",
    )
    list_filter = ("status", "file_type", "collection")
    search_fields = ("title", "file_path", "error_message")
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "status", "chunks_count", "error_message",
        "bulk_import_job", "created_at", "updated_at",
    )
    actions = [action_index_documents, action_reindex_documents]

    fieldsets = (
        (None, {
            "fields": ("id", "collection", "title", "ingested_by"),
        }),
        ("Arquivo", {
            "fields": ("file_path", "file_type"),
        }),
        ("Indexacao", {
            "fields": ("status", "chunks_count", "error_message"),
        }),
        ("Importacao em Lote", {
            "fields": ("bulk_import_job",),
            "classes": ("collapse",),
            "description": "Preenchido quando o documento foi criado por um job de importacao em lote.",
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

    @admin.display(description="Lote?", boolean=True)
    def has_bulk_job(self, obj):
        """Indica se o documento veio de uma importacao em lote."""
        return obj.bulk_import_job_id is not None


# ---------------------------------------------------------------------------
# KnowledgeChunkAdmin
# ---------------------------------------------------------------------------


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    """Admin do KnowledgeChunk -- somente leitura para inspecao e debug."""

    list_display = ("__str__", "document", "chunk_index", "content_preview", "collection_id")
    list_filter = ("document__collection", "document__file_type")
    search_fields = ("content", "document__title")
    ordering = ("document", "chunk_index")
    readonly_fields = (
        "id", "document", "collection_id", "chunk_index", "content", "embedding_dim",
    )

    fieldsets = (
        (None, {
            "fields": ("id", "document", "collection_id", "chunk_index"),
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


# ---------------------------------------------------------------------------
# BulkImportJobAdmin
# ---------------------------------------------------------------------------


@admin.action(description="Cancelar jobs selecionados (marcar como falha)")
def action_cancel_bulk_jobs(modeladmin, request, queryset):
    """Marca jobs pendentes/em execucao como falha para cancelamento manual."""
    count = queryset.filter(
        status__in=[BulkImportJob.Status.PENDING, BulkImportJob.Status.RUNNING]
    ).update(
        status=BulkImportJob.Status.FAILED,
        error_message="Cancelado manualmente pelo administrador.",
    )
    modeladmin.message_user(request, f"{count} job(s) cancelado(s).")


@admin.register(BulkImportJob)
class BulkImportJobAdmin(admin.ModelAdmin):
    """
    Admin do BulkImportJob.

    Permite acompanhar o progresso de importacoes em lote a partir de diretorios
    no disco, visualizar contadores de sucesso/falha e cancelar jobs travados.
    """

    list_display = (
        "id", "collection", "short_directory", "status_badge",
        "progress_display", "triggered_by", "created_at",
    )
    list_filter = ("status", "collection", "recursive")
    search_fields = ("source_directory", "collection__name", "triggered_by__username")
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "status", "total_files", "indexed_files", "failed_files",
        "progress_display", "error_message", "celery_task_id",
        "created_at", "updated_at",
    )
    actions = [action_cancel_bulk_jobs]

    fieldsets = (
        (None, {
            "fields": ("id", "collection", "triggered_by"),
        }),
        ("Configuracao", {
            "fields": ("source_directory", "recursive", "file_extensions"),
        }),
        ("Progresso", {
            "fields": (
                "status", "total_files", "indexed_files",
                "failed_files", "progress_display",
            ),
        }),
        ("Diagnostico", {
            "fields": ("error_message", "celery_task_id"),
            "classes": ("collapse",),
        }),
        ("Auditoria", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Diretorio")
    def short_directory(self, obj):
        d = obj.source_directory
        if len(d) > 60:
            return f"...{d[-57:]}"
        return d

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "pending": "#888",
            "running": "#1565c0",
            "completed": "#2e7d32",
            "completed_with_errors": "#e65100",
            "failed": "#c62828",
        }
        color = colors.get(obj.status, "#888")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Progresso")
    def progress_display(self, obj):
        if obj.total_files == 0:
            return "-"
        pct = obj.progress_pct
        done = obj.indexed_files + obj.failed_files
        bar_filled = int(pct / 5)
        bar = "#" * bar_filled + "." * (20 - bar_filled)
        label = f"{done}/{obj.total_files} ({pct}%)"
        fail_note = f" | {obj.failed_files} falha(s)" if obj.failed_files else ""
        return format_html(
            '<span style="font-family: monospace;">{} {}{}</span>',
            bar, label, fail_note,
        )
