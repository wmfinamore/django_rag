"""
Testes do apps/core.

Cobertura:
    - Hierarquia de excecoes
    - TimeStampedModel
    - utils: extract_text, truncate_text, normalize_whitespace
    - privacy_filter: mask
    - reranker: rerank, rerank_with_scores
    - rag_service: get_embedding, get_embeddings_batch, build_context

Testes lentos (carregam modelos reais de ML) sao marcados com
@pytest.mark.slow e podem ser ignorados com:  pytest -m "not slow"
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase, override_settings


# =============================================================================
# Exceptions
# =============================================================================


class TestExceptionHierarchy(TestCase):
    """Verifica que a hierarquia de excecoes esta correta."""

    def test_base_exception(self):
        from apps.core.exceptions import DjangoRAGError
        exc = DjangoRAGError("algo errado")
        self.assertIsInstance(exc, Exception)
        self.assertEqual(str(exc), "algo errado")

    def test_document_processing_is_base(self):
        from apps.core.exceptions import DocumentProcessingError, DjangoRAGError
        self.assertTrue(issubclass(DocumentProcessingError, DjangoRAGError))

    def test_text_extraction_hierarchy(self):
        from apps.core.exceptions import (
            DocumentProcessingError,
            DjangoRAGError,
            TextExtractionError,
        )
        self.assertTrue(issubclass(TextExtractionError, DocumentProcessingError))
        self.assertTrue(issubclass(TextExtractionError, DjangoRAGError))

    def test_text_extraction_attributes(self):
        from apps.core.exceptions import TextExtractionError
        original = ValueError("causa raiz")
        exc = TextExtractionError("msg", file_path="/tmp/doc.pdf", original=original)
        self.assertEqual(exc.file_path, "/tmp/doc.pdf")
        self.assertIs(exc.original, original)
        self.assertEqual(str(exc), "msg")

    def test_chunking_error_hierarchy(self):
        from apps.core.exceptions import ChunkingError, DocumentProcessingError
        self.assertTrue(issubclass(ChunkingError, DocumentProcessingError))

    def test_embedding_error_attributes(self):
        from apps.core.exceptions import DjangoRAGError, EmbeddingError
        exc = EmbeddingError("falha", model_name="all-MiniLM-L6-v2")
        self.assertIsInstance(exc, DjangoRAGError)
        self.assertEqual(exc.model_name, "all-MiniLM-L6-v2")
        self.assertIsNone(exc.original)

    def test_reranker_error_attributes(self):
        from apps.core.exceptions import RerankerError
        exc = RerankerError("falha reranker", model_name="ms-marco")
        self.assertEqual(exc.model_name, "ms-marco")

    def test_rag_error_hierarchy(self):
        from apps.core.exceptions import DjangoRAGError, LLMError, RAGError
        self.assertTrue(issubclass(RAGError, DjangoRAGError))
        self.assertTrue(issubclass(LLMError, RAGError))

    def test_llm_error_attributes(self):
        from apps.core.exceptions import LLMError
        exc = LLMError("timeout", model_name="llama3.2:3b", status_code=504)
        self.assertEqual(exc.model_name, "llama3.2:3b")
        self.assertEqual(exc.status_code, 504)

    def test_privacy_filter_error_hierarchy(self):
        from apps.core.exceptions import DjangoRAGError, PrivacyFilterError
        self.assertTrue(issubclass(PrivacyFilterError, DjangoRAGError))

    def test_catching_by_base_class(self):
        from apps.core.exceptions import DjangoRAGError, TextExtractionError
        with self.assertRaises(DjangoRAGError):
            raise TextExtractionError("erro")


# =============================================================================
# TimeStampedModel
# =============================================================================


class TestTimeStampedModel(TestCase):
    """TimeStampedModel e abstrato -- verificamos seus campos via inspecao."""

    def test_is_abstract(self):
        from apps.core.models import TimeStampedModel
        self.assertTrue(TimeStampedModel._meta.abstract)

    def test_has_created_at(self):
        from apps.core.models import TimeStampedModel
        field_names = [f.name for f in TimeStampedModel._meta.get_fields()]
        self.assertIn("created_at", field_names)

    def test_has_updated_at(self):
        from apps.core.models import TimeStampedModel
        field_names = [f.name for f in TimeStampedModel._meta.get_fields()]
        self.assertIn("updated_at", field_names)

    def test_created_at_auto_now_add(self):
        from apps.core.models import TimeStampedModel
        field = TimeStampedModel._meta.get_field("created_at")
        self.assertTrue(field.auto_now_add)

    def test_updated_at_auto_now(self):
        from apps.core.models import TimeStampedModel
        field = TimeStampedModel._meta.get_field("updated_at")
        self.assertTrue(field.auto_now)

    def test_default_ordering(self):
        from apps.core.models import TimeStampedModel
        self.assertEqual(TimeStampedModel._meta.ordering, ["-created_at"])


# =============================================================================
# utils -- extract_text
# =============================================================================


class TestExtractTextTxt(TestCase):
    """Testa extract_text para TXT e MD (sem dependencias externas)."""

    def _write_tmp(self, content: str, suffix: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def test_extract_txt_utf8(self):
        from apps.core.utils import extract_text
        path = self._write_tmp("texto simples", ".txt")
        try:
            self.assertEqual(extract_text(path, "txt"), "texto simples")
        finally:
            path.unlink(missing_ok=True)

    def test_extract_md(self):
        from apps.core.utils import extract_text
        content = "# Titulo\n\nParagrafo."
        path = self._write_tmp(content, ".md")
        try:
            self.assertEqual(extract_text(path, "md"), content)
        finally:
            path.unlink(missing_ok=True)

    def test_extension_with_dot_prefix(self):
        from apps.core.utils import extract_text
        path = self._write_tmp("conteudo", ".txt")
        try:
            self.assertEqual(extract_text(path, ".txt"), "conteudo")
        finally:
            path.unlink(missing_ok=True)

    def test_extension_case_insensitive(self):
        from apps.core.utils import extract_text
        path = self._write_tmp("texto", ".txt")
        try:
            self.assertEqual(extract_text(path, "TXT"), "texto")
        finally:
            path.unlink(missing_ok=True)

    def test_unsupported_type_raises(self):
        from apps.core.exceptions import TextExtractionError
        from apps.core.utils import extract_text
        with self.assertRaises(TextExtractionError) as ctx:
            extract_text("/fake/file.xls", "xls")
        self.assertIn("xls", str(ctx.exception))

    def test_file_not_found_raises(self):
        from apps.core.exceptions import TextExtractionError
        from apps.core.utils import extract_text
        with self.assertRaises(TextExtractionError):
            extract_text("/caminho/inexistente/arquivo.txt", "txt")

    def test_extract_empty_file(self):
        from apps.core.utils import extract_text
        path = self._write_tmp("", ".txt")
        try:
            self.assertEqual(extract_text(path, "txt"), "")
        finally:
            path.unlink(missing_ok=True)


# =============================================================================
# utils -- truncate_text, normalize_whitespace
# =============================================================================


class TestTextUtils(TestCase):

    def test_truncate_below_limit_unchanged(self):
        from apps.core.utils import truncate_text
        self.assertEqual(truncate_text("texto curto", max_chars=100), "texto curto")

    def test_truncate_adds_ellipsis(self):
        from apps.core.utils import truncate_text
        result = truncate_text("palavra1 palavra2 palavra3 palavra4", max_chars=20)
        self.assertTrue(result.endswith("...") or result.endswith("\u2026"))

    def test_truncate_preserves_whole_words(self):
        from apps.core.utils import truncate_text
        text = "um dois tres quatro cinco"
        result = truncate_text(text, max_chars=10)
        # O resultado (sem o ellipsis) deve ser uma palavra completa
        clean = result.rstrip("\u2026").strip()
        self.assertIn(clean, text)

    def test_truncate_exact_limit(self):
        from apps.core.utils import truncate_text
        self.assertEqual(truncate_text("abc", max_chars=3), "abc")

    def test_truncate_empty_string(self):
        from apps.core.utils import truncate_text
        self.assertEqual(truncate_text("", max_chars=100), "")

    def test_normalize_collapses_spaces(self):
        from apps.core.utils import normalize_whitespace
        self.assertEqual(normalize_whitespace("a   b"), "a b")

    def test_normalize_collapses_blank_lines(self):
        from apps.core.utils import normalize_whitespace
        self.assertEqual(normalize_whitespace("a\n\n\n\nb"), "a\n\nb")

    def test_normalize_strips_edges(self):
        from apps.core.utils import normalize_whitespace
        self.assertEqual(normalize_whitespace("   texto   "), "texto")

    def test_normalize_preserves_single_newline(self):
        from apps.core.utils import normalize_whitespace
        self.assertEqual(normalize_whitespace("a\nb"), "a\nb")


# =============================================================================
# privacy_filter -- mask()
# =============================================================================


@pytest.mark.slow
class TestPrivacyFilter(TestCase):
    """
    Testes de integracao do filtro PII/LGPD com Presidio real.
    Requerem: presidio-analyzer, presidio-anonymizer, spacy pt_core_news_lg.
    """

    def test_mask_empty_text(self):
        from apps.core.privacy_filter import mask
        result, occ = mask("")
        self.assertEqual(result, "")
        self.assertEqual(occ, [])

    def test_mask_whitespace_only(self):
        from apps.core.privacy_filter import mask
        _, occ = mask("   ")
        self.assertEqual(occ, [])

    def test_mask_cpf(self):
        from apps.core.privacy_filter import mask
        text = "O CPF do cliente e 123.456.789-09."
        masked, occ = mask(text)
        self.assertIn("[CPF]", masked)
        self.assertNotIn("123.456.789-09", masked)
        self.assertIn("BR_CPF", [o["type"] for o in occ])

    def test_mask_email(self):
        from apps.core.privacy_filter import mask
        text = "Contato: joao.silva@empresa.com.br"
        masked, occ = mask(text)
        self.assertIn("[EMAIL]", masked)
        self.assertNotIn("joao.silva@empresa.com.br", masked)
        self.assertIn("EMAIL_ADDRESS", [o["type"] for o in occ])

    def test_mask_credit_card(self):
        from apps.core.privacy_filter import mask
        text = "Cartao 4111 1111 1111 1111 aprovado."
        masked, occ = mask(text)
        self.assertIn("[CARTAO]", masked)
        self.assertIn("CREDIT_CARD", [o["type"] for o in occ])

    def test_occurrences_have_required_keys(self):
        from apps.core.privacy_filter import mask
        _, occ = mask("CPF: 123.456.789-09")
        for item in occ:
            for key in ("type", "score", "start", "end"):
                self.assertIn(key, item)

    def test_high_threshold_masks_nothing(self):
        """Score impossivel (>1.0) nao deve mascarar nada."""
        from apps.core.privacy_filter import _get_engines, mask
        _get_engines.cache_clear()
        _, occ = mask("CPF: 123.456.789-09, email: a@b.com", min_score=1.1)
        self.assertEqual(occ, [])
        _get_engines.cache_clear()


# =============================================================================
# reranker -- rerank(), rerank_with_scores()
# =============================================================================


class TestReranker(TestCase):
    """Testes unitarios do reranker com mock do CrossEncoder."""

    def test_empty_chunks_returns_empty(self):
        from apps.core.reranker import rerank
        self.assertEqual(rerank("query", chunks=[], top_k=4), [])

    def test_ordering_and_top_k(self):
        """O chunk com maior score deve ficar no topo."""
        import numpy as np
        chunks = ["doc A", "doc B", "doc C", "doc D", "doc E"]
        # doc C (index 2) tem maior score
        scores = [0.3, 0.1, 0.9, 0.2, 0.7]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = np.array(scores)

        with patch("apps.core.reranker._get_cross_encoder", return_value=mock_encoder):
            from apps.core.reranker import rerank
            result = rerank("query", chunks=chunks, top_k=3)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "doc C")  # score 0.9
        self.assertEqual(result[1], "doc E")  # score 0.7

    def test_top_k_larger_than_chunks(self):
        """top_k maior que chunks disponíveis retorna todos."""
        import numpy as np
        chunks = ["X", "Y"]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = np.array([0.5, 0.8])

        with patch("apps.core.reranker._get_cross_encoder", return_value=mock_encoder):
            from apps.core.reranker import rerank
            result = rerank("query", chunks=chunks, top_k=10)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "Y")  # maior score

    def test_rerank_with_scores_returns_tuples(self):
        import numpy as np
        chunks = ["doc A", "doc B", "doc C"]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = np.array([0.2, 0.8, 0.5])

        with patch("apps.core.reranker._get_cross_encoder", return_value=mock_encoder):
            from apps.core.reranker import rerank_with_scores
            result = rerank_with_scores("query", chunks=chunks, top_k=2)

        self.assertEqual(len(result), 2)
        score, text = result[0]
        self.assertIsInstance(score, float)
        self.assertEqual(text, "doc B")

    @pytest.mark.slow
    def test_real_model_picks_relevant_chunk(self):
        """Integracao: verifica que o modelo real prioriza o chunk relevante."""
        from apps.core.reranker import _get_cross_encoder, rerank
        _get_cross_encoder.cache_clear()
        chunks = [
            "A empresa oferece plano odontologico.",
            "O colaborador tem direito a 30 dias de ferias por ano.",
            "O refeitorio funciona das 11h30 as 13h30.",
        ]
        result = rerank("Quantos dias de ferias o funcionario tem?", chunks=chunks, top_k=1)
        self.assertEqual(len(result), 1)
        self.assertIn("ferias", result[0])
        _get_cross_encoder.cache_clear()


# =============================================================================
# rag_service -- get_embedding, get_embeddings_batch
# =============================================================================


class TestEmbeddings(TestCase):

    def test_get_embedding_returns_list_of_floats(self):
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)

        with patch("apps.core.rag_service._get_embedding_model", return_value=mock_model):
            from apps.core.rag_service import get_embedding
            result = get_embedding("Texto de teste")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 384)
        self.assertIsInstance(result[0], float)

    def test_get_embeddings_batch_empty(self):
        from apps.core.rag_service import get_embeddings_batch
        self.assertEqual(get_embeddings_batch([]), [])

    def test_get_embeddings_batch_count(self):
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1] * 384] * 3)

        with patch("apps.core.rag_service._get_embedding_model", return_value=mock_model):
            from apps.core.rag_service import get_embeddings_batch
            result = get_embeddings_batch(["a", "b", "c"])

        self.assertEqual(len(result), 3)
        for vec in result:
            self.assertIsInstance(vec, list)
            self.assertEqual(len(vec), 384)

    @pytest.mark.slow
    @override_settings(EMBEDDING_MODEL="all-MiniLM-L6-v2")
    def test_real_model_vector_shape(self):
        """Integracao: vetor deve ter 384 dimensoes."""
        from apps.core.rag_service import _get_embedding_model, get_embedding
        _get_embedding_model.cache_clear()
        result = get_embedding("politica de ferias")
        self.assertEqual(len(result), 384)
        for v in result:
            self.assertIsInstance(v, float)
        _get_embedding_model.cache_clear()

    @pytest.mark.slow
    def test_similar_texts_closer_than_unrelated(self):
        """Textos semanticamente proximos devem ter maior similaridade."""
        import math
        from apps.core.rag_service import _get_embedding_model, get_embedding
        _get_embedding_model.cache_clear()

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x**2 for x in a))
            nb = math.sqrt(sum(x**2 for x in b))
            return dot / (na * nb + 1e-9)

        v1 = get_embedding("politica de ferias dos funcionarios")
        v2 = get_embedding("direito a dias de descanso do empregado")
        v3 = get_embedding("receita de bolo de chocolate")

        self.assertGreater(cosine(v1, v2), cosine(v1, v3))
        _get_embedding_model.cache_clear()


# =============================================================================
# rag_service -- RAGService.build_context (sem banco)
# =============================================================================


class TestRAGServiceBuildContext(TestCase):
    """Testa o RAGService.build_context com mocks de embedding e reranker."""

    def _make_service(self, collection_ids=None, use_personal=False):
        from apps.core.rag_service import RAGService
        user = MagicMock()
        user.pk = 1
        return RAGService(
            user=user,
            collection_ids=collection_ids or [],
            use_personal_docs=use_personal,
            top_k=2,
        )

    def test_no_candidates_returns_empty_context(self):
        """Sem colecoes nem docs pessoais: chunks e sources vazios."""
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)

        with patch("apps.core.rag_service._get_embedding_model", return_value=mock_model):
            ctx = self._make_service().build_context("Qual politica de ferias?")

        self.assertEqual(ctx.chunks, [])
        self.assertEqual(ctx.sources, [])
        self.assertIn("Nenhum trecho", ctx.prompt)

    def test_candidates_flow_calls_rerank(self):
        """Com candidatos: reranker e chamado e contexto e montado."""
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)

        fake_candidates = [
            {
                "content": "Funcionarios tem 30 dias de ferias.",
                "source_title": "Politica RH",
                "source_id": "abc-123",
                "source_type": "knowledge",
            },
            {
                "content": "O aviso previo e de 30 dias.",
                "source_title": "Politica RH",
                "source_id": "abc-123",
                "source_type": "knowledge",
            },
        ]

        with patch("apps.core.rag_service._get_embedding_model", return_value=mock_model):
            with patch(
                "apps.core.rag_service.rerank",
                return_value=["Funcionarios tem 30 dias de ferias."],
            ) as mock_rerank:
                from apps.core.rag_service import RAGService
                service = self._make_service(collection_ids=["col-1"])
                with patch.object(service, "_retrieve_candidates", return_value=fake_candidates):
                    ctx = service.build_context("Quantos dias de ferias?")

        mock_rerank.assert_called_once()
        self.assertEqual(len(ctx.chunks), 1)
        self.assertIn("Funcionarios tem 30 dias de ferias.", ctx.prompt)
        self.assertIn("Quantos dias de ferias?", ctx.prompt)
        self.assertEqual(len(ctx.sources), 1)
        self.assertEqual(ctx.sources[0]["title"], "Politica RH")

    def test_prompt_contains_user_question(self):
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)

        with patch("apps.core.rag_service._get_embedding_model", return_value=mock_model):
            ctx = self._make_service().build_context("Pergunta do usuario aqui?")

        self.assertIn("Pergunta do usuario aqui?", ctx.prompt)

    def test_rag_context_dataclass_defaults(self):
        from apps.core.rag_service import RAGContext
        ctx = RAGContext()
        self.assertEqual(ctx.chunks, [])
        self.assertEqual(ctx.sources, [])
        self.assertEqual(ctx.prompt, "")
