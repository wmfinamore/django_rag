"""
Roteamento WebSocket da app chat.

URL:
    ws://<host>/ws/chat/<conversation_id>/

O AuthMiddlewareStack em config/asgi.py popula scope["user"] via sessão Django.
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r"^ws/chat/(?P<conversation_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/$",
        consumers.ChatConsumer.as_asgi(),
    ),
]
