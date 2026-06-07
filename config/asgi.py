"""
ASGI config for config project.
"""

import os

import django
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

django.setup()

from django.conf import settings  # noqa: E402

from apps.chat.routing import websocket_urlpatterns  # noqa: E402

http_handler = get_asgi_application()
if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
    http_handler = ASGIStaticFilesHandler(http_handler)

application = ProtocolTypeRouter(
    {
        "http": http_handler,
        "websocket": AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
