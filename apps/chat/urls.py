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
    # Conversas
    path("hx/conversations/", views.hx_conversation_list, name="hx-conv-list"),
    path("hx/conversations/new/", views.hx_conversation_create, name="hx-conv-create"),
    path("hx/conversations/<uuid:pk>/", views.hx_conversation_detail, name="hx-conv-detail"),
    path("hx/conversations/<uuid:pk>/delete/", views.hx_conversation_delete, name="hx-conv-delete"),
    path("hx/conversations/<uuid:pk>/rename/", views.hx_conversation_rename, name="hx-conv-rename"),
    # Painel de documentos pessoais
    path("hx/documents/", views.hx_documents_panel, name="hx-docs-panel"),
    path("hx/documents/list/", views.hx_documents_list, name="hx-docs-list"),
    path("hx/documents/upload/", views.hx_document_upload, name="hx-doc-upload"),
    path("hx/documents/<uuid:pk>/delete/", views.hx_document_delete, name="hx-doc-delete"),
    path("hx/documents/<uuid:pk>/reindex/", views.hx_document_reindex, name="hx-doc-reindex"),
]
