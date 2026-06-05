"""
Admin da app chat.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.chat.models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("id", "role", "content_preview", "sources", "created_at")
    fields = ("role", "content_preview", "sources", "created_at")
    can_delete = False

    def content_preview(self, obj: Message) -> str:
        return obj.content[:120] + ("…" if len(obj.content) > 120 else "")

    content_preview.short_description = "conteúdo"  # type: ignore[attr-defined]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "message_count", "use_personal_docs", "updated_at")
    list_filter = ("use_personal_docs", "created_at")
    search_fields = ("title", "user__username", "user__email")
    readonly_fields = ("id", "created_at", "updated_at")
    filter_horizontal = ("collections",)
    inlines = [MessageInline]

    def message_count(self, obj: Conversation) -> int:
        return obj.messages.count()

    message_count.short_description = "mensagens"  # type: ignore[attr-defined]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("role_badge", "conversation", "content_preview", "has_sources", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("content", "conversation__title", "conversation__user__username")
    readonly_fields = ("id", "conversation", "role", "content", "sources", "created_at")

    def role_badge(self, obj: Message) -> str:
        color = "#0d6efd" if obj.role == Message.Role.USER else "#198754"
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px">{}</span>',
            color,
            obj.get_role_display(),
        )

    role_badge.short_description = "papel"  # type: ignore[attr-defined]

    def content_preview(self, obj: Message) -> str:
        return obj.content[:100] + ("…" if len(obj.content) > 100 else "")

    content_preview.short_description = "conteúdo"  # type: ignore[attr-defined]

    def has_sources(self, obj: Message) -> str:
        return "✓" if obj.sources else "—"

    has_sources.short_description = "fontes"  # type: ignore[attr-defined]

    def has_add_permission(self, request):
        return False
