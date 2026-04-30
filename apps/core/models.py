"""
Modelos base do core.

Define o TimeStampedModel abstrato, herdado por todos os modelos do projeto
para garantir rastreabilidade de criação e atualização sem repetição.
"""

from django.db import models


class TimeStampedModel(models.Model):
    """
    Modelo abstrato com campos de auditoria de tempo.

    Todos os modelos concretos do projeto devem herdar deste para ter
    ``created_at`` e ``updated_at`` populados automaticamente.

    Uso::

        class MinhaEntidade(TimeStampedModel):
            nome = models.CharField(max_length=100)
    """

    created_at = models.DateTimeField(
        "criado em",
        auto_now_add=True,
        db_comment="Timestamp de criação do registro (UTC, preenchido automaticamente).",
    )
    updated_at = models.DateTimeField(
        "atualizado em",
        auto_now=True,
        db_comment="Timestamp da última atualização do registro (UTC, atualizado automaticamente).",
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]
