"""
Serializers DRF da app documents.

UserDocumentSerializer       -- leitura de documentos pessoais.
UserDocumentUploadSerializer -- validacao do payload de upload (HTTP multipart).
"""

from __future__ import annotations

import os
import uuid as _uuid

from django.conf import settings
from rest_framework import serializers

from apps.documents.models import UserDocument


class UserDocumentSerializer(serializers.ModelSerializer):
    """Serializer de leitura de UserDocument."""

    owner_username = serializers.CharField(source="owner.username", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = UserDocument
        fields = [
            "id",
            "owner",
            "owner_username",
            "title",
            "file",
            "file_url",
            "file_type",
            "status",
            "status_display",
            "chunks_count",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "owner_username",
            "file",
            "file_url",
            "file_type",
            "status",
            "status_display",
            "chunks_count",
            "error_message",
            "created_at",
            "updated_at",
        ]

    def get_file_url(self, obj) -> str | None:
        """Retorna URL absoluta do arquivo se disponivel."""
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None


class UserDocumentUploadSerializer(serializers.Serializer):
    """
    Serializer para upload de um novo documento pessoal via HTTP multipart.

    Aceita titulo e arquivo. O arquivo e salvo via FileField do modelo e a
    indexacao e disparada de forma assincrona via Celery.
    """

    ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}
    MAX_SIZE_MB = 50

    title = serializers.CharField(max_length=300)
    file = serializers.FileField()

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
