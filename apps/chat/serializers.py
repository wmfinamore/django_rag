"""
Serializers da app chat.

ConversationSerializer         -- leitura/listagem de conversas
ConversationCreateSerializer   -- criação/atualização de conversa
MessageSerializer              -- leitura de mensagens
"""

from __future__ import annotations

from rest_framework import serializers

from apps.chat.models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    """Serializer de leitura para Message."""

    class Meta:
        model = Message
        fields = ["id", "role", "content", "sources", "created_at"]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    """
    Serializer de leitura para Conversation.

    Inclui contagem de mensagens e a última mensagem para exibição em lista.
    """

    message_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    collection_ids = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "title",
            "use_personal_docs",
            "collection_ids",
            "message_count",
            "last_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_message_count(self, obj: Conversation) -> int:
        return obj.messages.count()

    def get_last_message(self, obj: Conversation) -> dict | None:
        msg = obj.messages.order_by("-created_at").first()
        if msg is None:
            return None
        return {
            "role": msg.role,
            "content": msg.content[:200],
            "created_at": msg.created_at,
        }

    def get_collection_ids(self, obj: Conversation) -> list[str]:
        return [str(cid) for cid in obj.collections.values_list("id", flat=True)]


class ConversationCreateSerializer(serializers.ModelSerializer):
    """
    Serializer de escrita para Conversation (criação e atualização parcial).

    Campos:
        title            -- obrigatório na criação; opcional no PATCH
        use_personal_docs -- opcional (default False)
        collection_ids   -- lista de UUIDs de KnowledgeCollection
    """

    collection_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Lista de UUIDs de KnowledgeCollection a incluir no escopo da conversa.",
    )

    class Meta:
        model = Conversation
        fields = ["title", "use_personal_docs", "collection_ids"]

    def validate_collection_ids(self, value: list) -> list:
        if not value:
            return value
        from apps.knowledge.models import KnowledgeCollection

        user = self.context["request"].user
        existing = set(
            KnowledgeCollection.objects.filter(
                id__in=value, is_active=True
            ).values_list("id", flat=True)
        )
        # Filtra por acesso do usuário (reutiliza lógica de is_accessible_by)
        accessible = [
            col_id
            for col_id in value
            if col_id in existing
            and KnowledgeCollection.objects.get(id=col_id).is_accessible_by(user)
        ]
        invalid = [str(cid) for cid in value if cid not in accessible]
        if invalid:
            raise serializers.ValidationError(
                f"Coleções não encontradas ou inacessíveis: {', '.join(invalid)}"
            )
        return accessible

    def create(self, validated_data: dict) -> Conversation:
        collection_ids = validated_data.pop("collection_ids", [])
        validated_data["user"] = self.context["request"].user
        conversation = Conversation.objects.create(**validated_data)
        if collection_ids:
            conversation.collections.set(collection_ids)
        return conversation

    def update(self, instance: Conversation, validated_data: dict) -> Conversation:
        collection_ids = validated_data.pop("collection_ids", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if collection_ids is not None:
            instance.collections.set(collection_ids)
        return instance
