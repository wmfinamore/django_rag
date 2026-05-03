# Garante que o app Celery seja carregado quando o Django iniciar,
# tornando o decorator @shared_task disponível em todos os módulos.
from config.celery import app as celery_app

__all__ = ("celery_app",)
