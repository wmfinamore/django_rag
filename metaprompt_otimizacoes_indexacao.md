# Metaprompt — Otimizações no pipeline de indexação

## Contexto

Este projeto é um Django RAG com Celery para indexação assíncrona de documentos.
O pipeline de indexação executa: extração de texto → mascaramento PII (Presidio + spaCy)
→ chunking semântico (SemanticChunker) → embeddings (SentenceTransformer) → pgvector.

Foram identificados **4 problemas** nos logs de produção que devem ser corrigidos.
Cada seção abaixo descreve o problema, o arquivo afetado, o diagnóstico e a solução esperada.

---

## Problema 1 — `HuggingFaceEmbeddings` depreciado e instância duplicada do modelo

**Arquivo:** `apps/core/tasks.py`, função `_chunk_text`

**Diagnóstico:**
A função `_chunk_text` instancia `HuggingFaceEmbeddings` da classe depreciada
`langchain_community.embeddings` a cada chamada. Isso gera o warning:

```
LangChainDeprecationWarning: The class `HuggingFaceEmbeddings` was deprecated in
LangChain 0.2.2 and will be removed in 1.0.
```

Além disso, essa instância carrega o modelo `all-MiniLM-L6-v2` do zero,
duplicando o modelo que já existe como singleton em `apps/core/rag_service.py`
via `_get_embedding_model` (que usa `sentence_transformers.SentenceTransformer`).

**Solução:**

1. Criar uma função singleton em `apps/core/rag_service.py` que retorne uma instância
   de `HuggingFaceEmbeddings` do pacote `langchain-huggingface` (não do `langchain_community`),
   reutilizando o mesmo `model_name` de `settings.EMBEDDING_MODEL`.
   Usar `@lru_cache(maxsize=1)` igual ao padrão já adotado no arquivo.
   O modelo interno deve ser o mesmo objeto já carregado por `_get_embedding_model`,
   passado via parâmetro `model` do construtor de `HuggingFaceEmbeddings`
   (evita carregar dois modelos em memória).

2. Em `apps/core/tasks.py`, substituir a importação e instanciação local:
   ```python
   # REMOVER:
   from langchain_community.embeddings import HuggingFaceEmbeddings
   hf_embeddings = HuggingFaceEmbeddings(
       model_name=embedding_model_name,
       model_kwargs={"device": "cpu"},
   )

   # SUBSTITUIR POR (chamar o singleton de rag_service):
   from apps.core.rag_service import get_langchain_embeddings
   hf_embeddings = get_langchain_embeddings()
   ```

3. Verificar se `langchain-huggingface` já está no `pyproject.toml`.
   Se não estiver, adicionar com `uv add langchain-huggingface`.

---

## Problema 2 — ~80 warnings de `Entity MISC` no Presidio

**Arquivo:** `apps/core/privacy_filter.py`, função `_get_engines`

**Diagnóstico:**
O modelo spaCy `pt_core_news_lg` produz entidades do tipo `MISC` que o Presidio
não mapeia para nenhuma entidade PII reconhecida. Isso gera dezenas de warnings
por documento indexado:

```
Entity MISC is not mapped to a Presidio entity, but keeping anyway.
Add to `NerModelConfiguration.labels_to_ignore` to remove.
```

O código atual usa `NlpEngineProvider` com um dicionário de configuração simples
que não especifica `labels_to_ignore`.

**Solução:**

Substituir a criação do `nlp_engine` via `NlpEngineProvider` por uso direto de
`SpacyNlpEngine` com `NerModelConfiguration`:

```python
from presidio_analyzer.nlp_engine import SpacyNlpEngine, NerModelConfiguration

ner_model_configuration = NerModelConfiguration(
    labels_to_ignore=["MISC", "O"],
)
nlp_engine = SpacyNlpEngine(
    models=[{"lang_code": "pt", "model_name": "pt_core_news_lg"}],
    ner_model_configuration=ner_model_configuration,
)
```

Remover a importação de `NlpEngineProvider` se não for mais usada.

---

## Problema 3 — 18 warnings de recognizers de outros idiomas no Presidio

**Arquivo:** `apps/core/privacy_filter.py`, função `_get_engines`

**Diagnóstico:**
O `AnalyzerEngine` carrega por padrão todos os recognizers predefinidos do Presidio
(`en`, `es`, `it`, `pl`) e depois rejeita os que não são `pt`, poluindo o log:

```
Recognizer not added to registry because language is not supported by registry
- CreditCardRecognizer supported languages: en, registry supported languages: pt
... (18 linhas similares)
```

**Solução:**

Criar um `RecognizerRegistry` explícito que carregue apenas recognizers para `pt`
antes de instanciar o `AnalyzerEngine`:

```python
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry

registry = RecognizerRegistry()
registry.load_predefined_recognizers(languages=["pt"])

# Adicionar os recognizers brasileiros customizados (já existentes em _build_br_recognizers)
for recognizer in _build_br_recognizers():
    registry.add_recognizer(recognizer)
    logger.debug("Reconhecedor registrado: %s (pt)", recognizer.supported_entities)

analyzer = AnalyzerEngine(
    nlp_engine=nlp_engine,
    registry=registry,
    supported_languages=["pt"],
)
```

Remover o loop de `_build_br_recognizers` que estava sendo feito após a criação
do `AnalyzerEngine` via `analyzer.registry.add_recognizer(...)`, pois agora
o registro é feito antes da instanciação.

---

## Problema 4 — Modelo de embedding e Presidio não pré-aquecidos no worker Celery

**Arquivo:** `config/celery.py`

**Diagnóstico:**
O `CoreConfig.ready()` em `apps/core/apps.py` já pré-carrega o modelo de embedding
e o CrossEncoder no processo Django (servidor web). Porém o worker Celery é um
processo separado e não passa pelo `AppConfig.ready()`. Por isso, na primeira task
`index_document` executada pelo worker, ocorre:

- ~20 requisições HEAD ao HuggingFace para verificar o modelo
- Carregamento do spaCy `pt_core_news_lg` + Presidio (~8s)
- Carregamento do `SentenceTransformer` para o SemanticChunker (~14s)

**Solução:**

Adicionar o sinal `worker_process_init` do Celery em `config/celery.py`
para pré-carregar os mesmos recursos que o Django server carrega no startup:

```python
from celery.signals import worker_process_init

@worker_process_init.connect
def preload_ml_models(sender=None, **kwargs):
    """
    Pré-carrega modelos de ML no worker Celery para eliminar cold start
    na primeira task de indexação. Espelha o comportamento de CoreConfig.ready().
    """
    import logging
    import django
    from django.conf import settings

    logger = logging.getLogger(__name__)

    # Embedding model (SentenceTransformer singleton)
    try:
        from apps.core.rag_service import _get_embedding_model
        _get_embedding_model(settings.EMBEDDING_MODEL)
        logger.info("Worker: modelo de embedding pré-carregado.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar modelo de embedding.")

    # LangChain wrapper para o SemanticChunker (novo singleton do Problema 1)
    try:
        from apps.core.rag_service import get_langchain_embeddings
        get_langchain_embeddings()
        logger.info("Worker: LangChain embeddings pré-carregados.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar LangChain embeddings.")

    # Presidio + spaCy
    try:
        from apps.core.privacy_filter import _get_engines
        _get_engines()
        logger.info("Worker: Presidio pré-carregado.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar Presidio.")
```

---

## Restrições e notas

- **Não alterar** a assinatura pública de nenhuma função existente (ex.: `mask`,
  `get_embeddings_batch`, `index_document`).
- **Não alterar** nenhum arquivo de migration nem de template HTML.
- Todos os `@lru_cache` existentes devem ser preservados — os novos singletons
  devem seguir o mesmo padrão.
- Após as alterações, rodar `uv run python -m pytest apps/core/tests.py -v`
  para verificar regressões.
- O `TRANSFORMERS_OFFLINE` / `HF_DATASETS_OFFLINE` **não** deve ser adicionado
  neste momento — esse é um ajuste separado de infraestrutura.
