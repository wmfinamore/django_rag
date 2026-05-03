"""
Serializers DRF da app knowledge.

KnowledgeCollectionSerializer  — listagem e criação de coleções.
KnowledgeDocumentSerializer    — listagem e upload de documentos.
KnowledgeDocumentUploadSerializer — validação do payload de upload.
"""

from __future__ import annotations

import os

from django.conf import settings
from rest_framework import serializers

from apps.knowledge.models import KnowledgeChunk, KnowledgeCollection, KnowledgeDocument


class KnowledgeCollectionSerializer(serializers.ModelSerializer):
    """Serializer de leitura/criação de KnowledgeCollection."""

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
    """Serializer de leitura de KnowledgeDocument."""

    collection_name = serializers.CharField(source="collection.name", read_only=True)
    ingested_by_username = serializers.CharField(
        source="ingested_by.username", read_only=True, default=None
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

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
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "chunks_count",
            "error_message",
            "ingested_by",
            "created_at",
            "updated_at",
        ]


class KnowledgeDocumentUploadSerializer(serializers.Serializer):
    """
    Serializer para upload de um novo documento.

    Aceita um arquivo via multipart/form-data e os metadados necessários.
    O arquivo é salvo em MEDIA_ROOT/knowledge/<collection_id>/ e o
    file_path é resolvido automaticamente.
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
                f"Extensão '{ext}' não suportada. Use: {', '.join(sorted(self.ALLOWED_EXTENSIONS))}."
            )
        max_bytes = self.MAX_SIZE_MB * 1024 * 1024
        if value.size > max_bytes:
            raise serializers.ValidationError(
                f"Arquivo muito grande ({value.size / 1024 / 1024:.1f} MB). "
                f"Máximo permitido: {self.MAX_SIZE_MB} MB."
            )
        return value

    def validate_collection_id(self, value):
        try:
            collection = KnowledgeCollection.objects.get(pk=value, is_active=True)
        except KnowledgeCollection.DoesNotExist:
            raise serializers.ValidationError("Coleção não encontrada ou inativa.")
        self._collection = collection
        return value

    def validate(self, attrs):
        # Verifica acesso do usuário à coleção
        request = self.context.get("request")
        if request and hasattr(self, "_collection"):
            if not self._collection.is_accessible_by(request.user):
                raise serializers.ValidationError(
                    {"collection_id": "Você não tem permissão para enviar documentos a esta coleção."}
                )
        return attrs

    def save_file(self) -> tuple[str, str]:
        """
        Salva o arquivo no filesystem e retorna (file_path, file_type).
        """
        import pathlib

        file = self.validated_data["file"]
        collection_id = self.validated_data["collection_id"]

        ext = os.path.splitext(file.name)[1].lstrip(".").lower()
        dest_dir = pathlib.Path(settings.MEDIA_ROOT) / "knowledge" / str(collection_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Usa o nome original sanitizado; adiciona UUID para evitar colisões
        import uuid as _uuid
        safe_name = f"{_uuid.uuid4().hex}_{file.name}"
        dest_path = dest_dir / safe_name

        with open(dest_path, "wb") as f:
            for chunk in file.chunks():
                f.write(chunk)

        return str(dest_path), ext
