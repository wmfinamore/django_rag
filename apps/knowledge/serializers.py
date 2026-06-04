"""
Serializers DRF da app knowledge.

KnowledgeCollectionSerializer     -- listagem e criacao de colecoes.
KnowledgeDocumentSerializer       -- leitura de documentos (upload e importacao em lote).
KnowledgeDocumentUploadSerializer -- validacao do payload de upload manual (HTTP multipart).
BulkImportJobSerializer           -- leitura de BulkImportJob (status e progresso).
BulkImportJobCreateSerializer     -- validacao do payload de disparo de importacao em lote.
"""

from __future__ import annotations

import os

from django.conf import settings
from rest_framework import serializers

from apps.knowledge.models import (
    BulkImportJob,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
)


class KnowledgeCollectionSerializer(serializers.ModelSerializer):
    """Serializer de leitura/criacao de KnowledgeCollection."""

    allowed_groups = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field="name",
    )
    document_count = serializers.SerializerMethodField()
    ready_count = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeCollection
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "allowed_groups",
            "document_count",
            "ready_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_document_count(self, obj) -> int:
        return obj.documents.count()

    def get_ready_count(self, obj) -> int:
        return obj.documents.filter(status=KnowledgeDocument.Status.READY).count()


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    """
    Serializer de leitura de KnowledgeDocument.

    Inclui bulk_import_job_id para identificar se o documento
    foi originado por uma carga em lote ou por upload manual.
    """

    collection_name = serializers.CharField(source="collection.name", read_only=True)
    ingested_by_username = serializers.CharField(
        source="ingested_by.username", read_only=True, default=None
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    bulk_import_job_id = serializers.UUIDField(
        source="bulk_import_job.id", read_only=True, default=None
    )

    class Meta:
        model = KnowledgeDocument
        fields = [
            "id",
            "collection",
            "collection_name",
            "title",
            "file_path",
            "file_type",
            "status",
            "status_display",
            "chunks_count",
            "error_message",
            "ingested_by",
            "ingested_by_username",
            "bulk_import_job_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "chunks_count",
            "error_message",
            "ingested_by",
            "bulk_import_job_id",
            "created_at",
            "updated_at",
        ]


class KnowledgeDocumentUploadSerializer(serializers.Serializer):
    """
    Serializer para upload de um novo documento via HTTP multipart.

    Aceita um arquivo via multipart/form-data e os metadados necessarios.
    O arquivo e salvo em MEDIA_ROOT/knowledge/<collection_id>/ e o
    file_path e resolvido automaticamente.
    """

    ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}
    MAX_SIZE_MB = 50

    title = serializers.CharField(max_length=300)
    file = serializers.FileField()
    collection_id = serializers.UUIDField()

    def validate_file(self, value):
        ext = os.path.splitext(value.name)[1].lstrip(".").lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                "Extensao '%s' nao suportada. Use: %s." % (
                    ext, ", ".join(sorted(self.ALLOWED_EXTENSIONS))
                )
            )
        max_bytes = self.MAX_SIZE_MB * 1024 * 1024
        if value.size > max_bytes:
            raise serializers.ValidationError(
                "Arquivo muito grande (%.1f MB). Maximo permitido: %d MB." % (
                    value.size / 1024 / 1024, self.MAX_SIZE_MB
                )
            )
        return value

    def validate_collection_id(self, value):
        try:
            collection = KnowledgeCollection.objects.get(pk=value, is_active=True)
        except KnowledgeCollection.DoesNotExist:
            raise serializers.ValidationError("Colecao nao encontrada ou inativa.")
        self._collection = collection
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        if request and hasattr(self, "_collection"):
            if not self._collection.is_accessible_by(request.user):
                raise serializers.ValidationError(
                    {"collection_id": "Voce nao tem permissao para enviar documentos a esta colecao."}
                )
        return attrs

    def save_file(self) -> tuple[str, str]:
        """Salva o arquivo no filesystem e retorna (file_path, file_type)."""
        import pathlib
        import uuid as _uuid

        file = self.validated_data["file"]
        collection_id = self.validated_data["collection_id"]

        ext = os.path.splitext(file.name)[1].lstrip(".").lower()
        dest_dir = pathlib.Path(settings.MEDIA_ROOT) / "knowledge" / str(collection_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "%s_%s" % (_uuid.uuid4().hex, file.name)
        dest_path = dest_dir / safe_name

        with open(dest_path, "wb") as f:
            for chunk in file.chunks():
                f.write(chunk)

        return str(dest_path), ext


class BulkImportJobSerializer(serializers.ModelSerializer):
    """
    Serializer de leitura de BulkImportJob.

    Expoe o progresso do job de importacao em lote.
    Usado em GET /collections/<id>/bulk-import/ e
    GET /collections/<id>/bulk-import/<job_id>/.
    """

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    triggered_by_username = serializers.CharField(
        source="triggered_by.username", read_only=True, default=None
    )
    collection_name = serializers.CharField(source="collection.name", read_only=True)
    progress_pct = serializers.IntegerField(read_only=True)

    class Meta:
        model = BulkImportJob
        fields = [
            "id",
            "collection",
            "collection_name",
            "source_directory",
            "recursive",
            "file_extensions",
            "status",
            "status_display",
            "total_files",
            "indexed_files",
            "failed_files",
            "progress_pct",
            "error_message",
            "celery_task_id",
            "triggered_by",
            "triggered_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "collection",
            "collection_name",
            "source_directory",
            "recursive",
            "file_extensions",
            "status",
            "status_display",
            "total_files",
            "indexed_files",
            "failed_files",
            "progress_pct",
            "error_message",
            "celery_task_id",
            "triggered_by",
            "triggered_by_username",
            "created_at",
            "updated_at",
        ]


class BulkImportJobCreateSerializer(serializers.Serializer):
    """
    Serializer de disparo de um novo BulkImportJob.

    Valida que o diretorio existe no servidor e que as extensoes sao suportadas.

    Campos:
        source_directory -- Caminho absoluto do diretorio no servidor.
        recursive        -- Percorrer subdiretorios? (padrao: True)
        file_extensions  -- Lista de extensoes aceitas sem ponto (ex: ["pdf","docx"]).
                            Vazio = todas as extensoes suportadas.
    """

    SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md"}

    source_directory = serializers.CharField(
        max_length=1000,
        help_text="Caminho absoluto do diretorio no servidor.",
    )
    recursive = serializers.BooleanField(default=True)
    file_extensions = serializers.ListField(
        child=serializers.CharField(max_length=10),
        default=list,
        allow_empty=True,
        help_text="Extensoes aceitas sem ponto (ex: ['pdf', 'docx']). Vazio = todas suportadas.",
    )

    def validate_source_directory(self, value: str) -> str:
        import pathlib
        path = pathlib.Path(value).resolve()
        if not path.exists():
            raise serializers.ValidationError(
                "Diretorio nao encontrado no servidor: %s" % value
            )
        if not path.is_dir():
            raise serializers.ValidationError(
                "O caminho nao e um diretorio: %s" % value
            )
        return str(path)

    def validate_file_extensions(self, value: list) -> list:
        normalized = [e.lower().lstrip(".") for e in value]
        unsupported = set(normalized) - self.SUPPORTED_EXTENSIONS
        if unsupported:
            raise serializers.ValidationError(
                "Extensao(oes) nao suportada(s): %s. Suportadas: %s." % (
                    ", ".join(sorted(unsupported)),
                    ", ".join(sorted(self.SUPPORTED_EXTENSIONS)),
                )
            )
        return normalized
