"""
URLs HTML da app chat (prefixo /rag/chat/).

    GET    /chat/                                → chat_index
    GET    /chat/hx/conversations/               → hx_conversation_list
    GET    /chat/hx/conversations/<id>/          → hx_conversation_detail
    POST   /chat/hx/conversations/new/           → hx_conversation_create
    DELETE /chat/hx/conversations/<id>/delete/   → hx_conversation_delete
    POST   /chat/hx/conversations/<id>/rename/   → hx_conversation_rename
"""

from django.urls import path

from apps.chat import views

app_name = "chat"

urlpatterns = [
    path("", views.chat_index, name="index"),
    path("hx/conversations/", views.hx_conversation_list, name="hx-conv-list"),
    path("hx/conversations/new/", views.hx_conversation_create, name="hx-conv-create"),
    path("hx/conversations/<uuid:pk>/", views.hx_conversation_detail, name="hx-conv-detail"),
    path("hx/conversations/<uuid:pk>/delete/", views.hx_conversation_delete, name="hx-conv-delete"),
    path("hx/conversations/<uuid:pk>/rename/", views.hx_conversation_rename, name="hx-conv-rename"),
]
