"""
Serviço RAG central do projeto django_rag.

Responsabilidades:
    1. Gerar embedding da query do usuário.
    2. Buscar chunks candidatos no pgvector (KnowledgeChunk e/ou UserChunk).
    3. Rerankear os candidatos com CrossEncoder.
    4. Construir o prompt para o LLM.
    5. Fazer streaming da resposta via Ollama.

Uso::

    from apps.core.rag_service import RAGService

    service = RAGService(user=request.user, conversation=conv)
    async for token in service.stream("Qual é a política de férias?"):
        yield token  # WebSocket / SSE

Configuração relevante em settings:
    OLLAMA_BASE_URL, OLLAMA_LLM_MODEL, OLLAMA_NUM_CTX,
    OLLAMA_NUM_THREAD, OLLAMA_TEMPERATURE,
    EMBEDDING_MODEL, RAG_TOP_K, RAG_RERANK_FACTOR,
    RAG_RERANKER_MODEL
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, AsyncIterator, Iterator

from django.conf import settings

from apps.core.exceptions import EmbeddingError, LLMError, RAGError
from apps.core.reranker import rerank

if TYPE_CHECKING:
    from apps.accounts.models import CustomUser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Você é um assistente especializado que responde perguntas com base "
    "exclusivamente nos trechos de documentos fornecidos abaixo. "
    "Se a resposta não puder ser derivada dos trechos, diga que não encontrou "
    "informação suficiente nos documentos disponíveis. "
    "Seja objetivo e preciso. Responda em português do Brasil."
)

CONTEXT_TEMPLATE = """Trechos de documentos relevantes:

{context}

---

Pergunta: {question}

Resposta:"""


# ---------------------------------------------------------------------------
# Singleton do modelo de embeddings
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_embedding_model(model_name: str):
    """
    Carrega o modelo SentenceTransformer para geração de embeddings.
    Singleton por processo — carregado uma vez na primeira chamada.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingError(
            "sentence-transformers não está instalado. "
            "Execute: uv add sentence-transformers",
            model_name=model_name,
            original=exc,
        ) from exc

    logger.info("Carregando modelo de embedding '%s'…", model_name)
    try:
        model = SentenceTransformer(model_name, device="cpu")
        logger.info("Modelo de embedding '%s' carregado.", model_name)
        return model
    except Exception as exc:
        raise EmbeddingError(
            f"Falha ao carregar o modelo de embedding '{model_name}': {exc}",
            model_name=model_name,
            original=exc,
        ) from exc


# ---------------------------------------------------------------------------
# Geração de embedding
# ---------------------------------------------------------------------------


def get_embedding(text: str, model_name: str | None = None) -> list[float]:
    """
    Gera o embedding de um texto usando sentence-transformers.

    Parâmetros
    ----------
    text:
        Texto a ser vetorizado.
    model_name:
        Nome do modelo (padrão: ``settings.EMBEDDING_MODEL``).

    Retorno
    -------
    list[float]
        Vetor de 384 dimensões (all-MiniLM-L6-v2).
    """
    _model_name = model_name or getattr(settings, "EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    model = _get_embedding_model(_model_name)

    try:
        vector = model.encode(text, convert_to_numpy=True)
        return vector.tolist()
    except Exception as exc:
        raise EmbeddingError(
            f"Falha ao gerar embedding com '{_model_name}': {exc}",
            model_name=_model_name,
            original=exc,
        ) from exc


def get_embeddings_batch(texts: list[str], model_name: str | None = None) -> list[list[float]]:
    """
    Gera embeddings em lote (mais eficiente que chamar get_embedding em loop).

    Parâmetros
    ----------
    texts:
        Lista de textos.
    model_name:
        Nome do modelo (padrão: ``settings.EMBEDDING_MODEL``).

    Retorno
    -------
    list[list[float]]
        Lista de vetores, um por texto de entrada.
    """
    if not texts:
        return []

    _model_name = model_name or getattr(settings, "EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    model = _get_embedding_model(_model_name)

    try:
        vectors = model.encode(texts, convert_to_numpy=True, batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vectors]
    except Exception as exc:
        raise EmbeddingError(
            f"Falha ao gerar embeddings em lote com '{_model_name}': {exc}",
            model_name=_model_name,
            original=exc,
        ) from exc


# ---------------------------------------------------------------------------
# Resultado RAG
# ---------------------------------------------------------------------------


@dataclass
class RAGContext:
    """
    Contexto montado pelo RAGService antes da chamada ao LLM.

    Atributos
    ---------
    chunks:
        Lista dos chunks finais (pós-reranking) usados no prompt.
    sources:
        Lista de dicts com informações de fonte para salvar em Message.sources.
    prompt:
        Prompt completo montado para o LLM.
    """

    chunks: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    prompt: str = ""


# ---------------------------------------------------------------------------
# RAGService
# ---------------------------------------------------------------------------


class RAGService:
    """
    Orquestra o pipeline RAG completo para uma conversa.

    Parâmetros
    ----------
    user:
        Usuário autenticado (CustomUser). Usado para filtrar UserChunk.
    collection_ids:
        Lista de UUIDs de KnowledgeCollection acessíveis à conversa.
        Se vazia, não busca na base institucional.
    use_personal_docs:
        Se True, inclui UserChunk do usuário na busca.
    top_k:
        Chunks finais enviados ao prompt (pós-reranking).
        Padrão: ``settings.RAG_TOP_K``.
    rerank_factor:
        Multiplicador de candidatos pré-reranking.
        Padrão: ``settings.RAG_RERANK_FACTOR``.
    """

    def __init__(
        self,
        user: "CustomUser",
        collection_ids: list[str] | None = None,
        use_personal_docs: bool = False,
        top_k: int | None = None,
        rerank_factor: int | None = None,
    ):
        self.user = user
        self.collection_ids = collection_ids or []
        self.use_personal_docs = use_personal_docs
        self.top_k = top_k if top_k is not None else getattr(settings, "RAG_TOP_K", 4)
        self.rerank_factor = rerank_factor if rerank_factor is not None else getattr(
            settings, "RAG_RERANK_FACTOR", 3
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def _retrieve_candidates(self, query_embedding: list[float]) -> list[dict]:
        """
        Busca chunks candidatos no pgvector.

        Retorna lista de dicts com 'content', 'source_title', 'source_id'.
        """
        candidates: list[dict] = []
        candidate_limit = self.top_k * self.rerank_factor

        # Base institucional
        if self.collection_ids:
            try:
                from apps.knowledge.models import KnowledgeChunk

                ks = (
                    KnowledgeChunk.objects.filter(
                        collection_id__in=self.collection_ids
                    )
                    .order_by(
                        KnowledgeChunk.embedding.l2_distance(query_embedding)
                    )[:candidate_limit]
                )
                for chunk in ks:
                    candidates.append(
                        {
                            "content": chunk.content,
                            "source_title": chunk.document.title,
                            "source_id": str(chunk.document.id),
                            "source_type": "knowledge",
                        }
                    )
            except Exception as exc:
                logger.warning("Falha ao buscar KnowledgeChunk: %s", exc)

        # Base pessoal
        if self.use_personal_docs:
            try:
                from apps.documents.models import UserChunk

                us = (
                    UserChunk.objects.filter(user_id=self.user.pk)
                    .order_by(
                        UserChunk.embedding.l2_distance(query_embedding)
                    )[:candidate_limit]
                )
                for chunk in us:
                    candidates.append(
                        {
                            "content": chunk.content,
                            "source_title": chunk.document.title,
                            "source_id": str(chunk.document.id),
                            "source_type": "personal",
                        }
                    )
            except Exception as exc:
                logger.warning("Falha ao buscar UserChunk: %s", exc)

        return candidates

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def build_context(self, query: str) -> RAGContext:
        """
        Monta o contexto RAG para uma query:
            1. Gera embedding da query.
            2. Busca candidatos no pgvector.
            3. Rerankeia com CrossEncoder.
            4. Constrói o prompt.

        Retorno
        -------
        RAGContext
            Contexto pronto para ser usado na chamada ao LLM.
        """
        # 1. Embedding da query
        query_embedding = get_embedding(query)

        # 2. Candidatos via pgvector
        candidates = self._retrieve_candidates(query_embedding)

        if not candidates:
            logger.info("Nenhum chunk candidato encontrado para a query.")
            prompt = CONTEXT_TEMPLATE.format(
                context="(Nenhum trecho de documento disponível.)",
                question=query,
            )
            return RAGContext(chunks=[], sources=[], prompt=prompt)

        # 3. Reranking
        chunk_texts = [c["content"] for c in candidates]
        top_texts = rerank(query=query, chunks=chunk_texts, top_k=self.top_k)

        # Mapeia os chunks rerankeados de volta às suas fontes
        content_to_candidate = {c["content"]: c for c in candidates}
        top_candidates = [content_to_candidate.get(t, {"content": t}) for t in top_texts]

        # 4. Contexto e sources
        context_parts = [
            f"[{i + 1}] {c['content']}"
            for i, c in enumerate(top_candidates)
        ]
        context_str = "\n\n".join(context_parts)

        sources = [
            {
                "title": c.get("source_title", ""),
                "id": c.get("source_id", ""),
                "type": c.get("source_type", ""),
                "index": i + 1,
            }
            for i, c in enumerate(top_candidates)
        ]

        prompt = CONTEXT_TEMPLATE.format(context=context_str, question=query)

        return RAGContext(chunks=top_texts, sources=sources, prompt=prompt)

    # ------------------------------------------------------------------
    # LLM — geração síncrona e em streaming
    # ------------------------------------------------------------------

    def _ollama_client(self):
        """Instancia o cliente Ollama via langchain-ollama."""
        try:
            from langchain_ollama import OllamaLLM
        except ImportError as exc:
            raise LLMError(
                "langchain-ollama não está instalado. "
                "Execute: uv add langchain-ollama",
                original=exc,
            ) from exc

        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        model = getattr(settings, "OLLAMA_LLM_MODEL", "llama3.2:3b")
        num_ctx = getattr(settings, "OLLAMA_NUM_CTX", 2048)
        num_thread = getattr(settings, "OLLAMA_NUM_THREAD", 4)
        temperature = getattr(settings, "OLLAMA_TEMPERATURE", 0.3)

        return OllamaLLM(
            base_url=base_url,
            model=model,
            temperature=temperature,
            num_ctx=num_ctx,
            num_thread=num_thread,
        )

    def generate(self, query: str) -> tuple[str, list[dict]]:
        """
        Gera uma resposta completa (não-streaming).

        Retorno
        -------
        tuple[str, list[dict]]
            (resposta, sources)
        """
        ctx = self.build_context(query)
        llm = self._ollama_client()

        full_prompt = f"{SYSTEM_PROMPT}\n\n{ctx.prompt}"

        try:
            response = llm.invoke(full_prompt)
        except Exception as exc:
            raise LLMError(
                f"Falha ao chamar o LLM: {exc}",
                model_name=getattr(settings, "OLLAMA_LLM_MODEL", ""),
                original=exc,
            ) from exc

        return response, ctx.sources

    def stream(self, query: str) -> Iterator[str]:
        """
        Gera a resposta em streaming (token a token).

        Uso em ChatConsumer::

            for token in service.stream(query):
                await self.send(text_data=token)

        Retorno
        -------
        Iterator[str]
            Tokens da resposta, um por vez.
        """
        ctx = self.build_context(query)
        llm = self._ollama_client()

        full_prompt = f"{SYSTEM_PROMPT}\n\n{ctx.prompt}"

        try:
            for chunk in llm.stream(full_prompt):
                yield chunk
        except Exception as exc:
            raise LLMError(
                f"Falha durante o streaming do LLM: {exc}",
                model_name=getattr(settings, "OLLAMA_LLM_MODEL", ""),
                original=exc,
            ) from exc

    def get_sources_for_last_query(self, query: str) -> list[dict]:
        """
        Retorna apenas as sources (sem chamar o LLM), útil para debug.
        """
        ctx = self.build_context(query)
        return ctx.sources
