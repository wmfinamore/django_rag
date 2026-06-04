"""
Tasks da app documents.

As tasks de indexacao/delecao/reindexacao de documentos pessoais sao
implementadas em apps.core.tasks com doc_type="personal".

Este modulo re-exporta as tasks do core para facilitar imports diretos
a partir da app documents, mantendo consistencia com a arquitetura.

Uso:
    from apps.documents.tasks import index_document, delete_document, reindex_document
    index_document.delay(str(doc.id), "personal")
"""

from apps.core.tasks import delete_document, index_document, reindex_document

__all__ = ["index_document", "delete_document", "reindex_document"]
