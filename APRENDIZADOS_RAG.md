# Aprendizados — django_rag

Guia de erros já cometidos e decisões que funcionaram, extraído da implementação
de um RAG em Django (Postgres/pgvector + Celery + Ollama + Presidio + Channels).
Objetivo: **não repetir os mesmos erros no próximo projeto RAG.**

Stack de referência: Django 6, DRF, Channels/Daphne, Celery+Redis, PostgreSQL 16 + pgvector,
sentence-transformers (all-MiniLM-L6-v2, 384d), CrossEncoder ms-marco-MiniLM-L-6-v2,
LangChain 1.x, Ollama (llama3.2:3b), Presidio + spaCy pt_core_news_lg, Keycloak (OIDC).

---

## 1. Erros que custaram caro (leia primeiro)

### 1.1 API errada do pgvector — retrieval falhava em silêncio
`embedding.l2_distance(...)` **não existe** no ORM do Django. O correto é a expressão
`L2Distance`. O erro era engolido por um `try/except` genérico e o RAG simplesmente
respondia "não encontrei informação" — sem stack trace.

```python
from pgvector.django import L2Distance

qs = (KnowledgeChunk.objects
      .filter(collection_id__in=ids)
      .select_related("document")               # evita N+1 ao ler document.title
      .annotate(distance=L2Distance("embedding", query_embedding))
      .order_by("distance"))
```

**Lição geral:** nunca envolver a etapa de retrieval em `except Exception: log.warning`
sem um teste de integração que prove que a busca retorna linhas. Falha silenciosa em RAG
é indistinguível de "não há contexto relevante".

### 1.2 Cold start: modelos de ML carregando na primeira requisição
Primeira query levava dezenas de segundos (SentenceTransformer + CrossEncoder + spaCy +
Ollama carregando do zero). Solução: **pré-carregar em todos os processos**.

- Django: `AppConfig.ready()` chama os singletons de embedding e reranker.
- Celery: sinal `worker_process_init` faz o mesmo (~22 s eliminados na 1ª task).
  `worker_init` não basta — com prefork cada processo filho precisa carregar.
- Ollama: thread daemon no `ready()` faz `POST /api/generate` sem prompt com `keep_alive`.

```python
# config/celery.py
@worker_process_init.connect
def preload_ml_models(sender=None, **kwargs):
    _get_embedding_model(settings.EMBEDDING_MODEL)
    get_langchain_embeddings()
    _get_engines()          # Presidio + spaCy
```

Todo carregamento de modelo deve ser `@lru_cache(maxsize=1)` — singleton por processo.

### 1.3 `keep_alive="-1"` → HTTP 400 no Ollama
Ollama aceita `keep_alive` como **número** (segundos, `-1` = indefinido) ou **string com
unidade** (`"30m"`, `"1h"`). A string `"-1"` falha no parse de duração. Como vem de `.env`
sempre chega como string:

```python
if isinstance(keep_alive, str) and keep_alive.lstrip("-").isdigit():
    keep_alive = int(keep_alive)
```

E não basta corrigir no warm-up: **o cliente LLM também precisa receber `keep_alive`**,
senão a primeira query real reseta o timer e anula o aquecimento.

### 1.4 Dois modelos de embedding na memória
O `SemanticChunker` do LangChain exige um wrapper `HuggingFaceEmbeddings`, que instancia
o **seu próprio** SentenceTransformer — dobrando o uso de RAM. Solução: apontar o client
interno do wrapper para o singleton já carregado.

```python
hf = HuggingFaceEmbeddings(model_name=model_name, model_kwargs={"device": "cpu"})
try:
    hf.client = singleton          # langchain < 1.x
except (ValueError, AttributeError):
    hf._client = singleton         # langchain >= 1.x (pydantic extra="forbid")
```

### 1.5 Presidio: ~100 warnings por documento e erro de idioma
Três problemas distintos:

| Sintoma | Causa | Correção |
|---|---|---|
| `Entity MISC is not mapped to a Presidio entity` (~80x) | `NlpEngineProvider` sem config de NER | `SpacyNlpEngine(..., ner_model_configuration=NerModelConfiguration(labels_to_ignore=["MISC","O"]))` |
| ~18 warnings de recognizers en/es/it/pl | registry padrão carrega todos os idiomas | `RecognizerRegistry(supported_languages=["pt"])` + `load_predefined_recognizers(languages=["pt"])` |
| `ValueError` de idiomas inconsistentes | `AnalyzerEngine` com registry multi-idioma | passar `supported_languages=["pt"]` no registry **e** no engine |

Recognizers nativos do Presidio (ex.: `CREDIT_CARD`) só existem para `en`. Para pt-BR é
preciso registrar `PatternRecognizer` próprios: **CPF, CNPJ, RG e cartão** — com
`supported_language="pt"` e `context=[...]` para elevar o score.

### 1.6 `docker-compose`: dois erros de infra
- `driver: "loca"` (typo) em `volumes:` — o compose aceita e falha depois.
- `init.sql` montado como **volume nomeado** nunca é entregue ao container. Tem que ser
  **bind mount**: `./docker/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro`.
- `init.sql` só roda na **primeira** criação do volume. Alterou o script? `docker compose down -v`.
- pgvector: `CREATE EXTENSION IF NOT EXISTS vector;` no `init.sql` **ou** `CreateExtension`
  na primeira migration — e as demais apps precisam depender dela.

### 1.7 PyTorch no Windows — `[WinError 1114]`
`LoadLibraryExW` falha ao inicializar `c10.dll` quando psycopg/LangChain registram DLLs
primeiro no mesmo processo. Correção: forçar torch a carregar antes de tudo.

```python
# .venv/Lib/site-packages/sitecustomize.py
try:
    import torch  # noqa: F401
except Exception:
    pass
```

Complementos: `conftest.py` avisa se `"torch" not in sys.modules`; no Windows use
`.venv\Scripts\python.exe -m pytest` (o `uv run` altera o PATH e reintroduz o erro).

### 1.8 uv + índice do PyTorch contaminando a resolução
Sem `explicit = true`, o uv tentava resolver `requests`, `langchain` etc. contra o índice
do PyTorch.

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true                       # só é usado por quem pedir explicitamente

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
pt-core-news-lg = { url = "https://github.com/explosion/spacy-models/releases/download/pt_core_news_lg-3.8.0/pt_core_news_lg-3.8.0-py3-none-any.whl" }
```

Registrar o modelo spaCy como dependência (wheel via URL) elimina o passo manual
`python -m spacy download` e garante reprodutibilidade.

### 1.9 Compatibilidade LangChain 1.x
Imports mudaram de lugar. Sempre com fallback:

```python
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter   # >= 1.x
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter    # legado
```

`langchain_community.embeddings.HuggingFaceEmbeddings` está depreciado → use
`langchain-huggingface`.

### 1.10 Celery no Windows
O pool `prefork` não funciona. Em `development.py`: `CELERY_WORKER_POOL = "solo"`.
(Consequência: single-threaded; não serve para medir throughput.)

### 1.11 Diversos Django que travaram o boot
- Nome de índice > 30 caracteres → `models.E034`. Sempre nomear índices explicitamente e curto.
- Django 5+: `CustomUserChangeForm` precisa incluir `password` **e** `usable_password`.
- Daphne não serve estáticos como o `runserver`: adicionar `ASGIStaticFilesHandler` no
  `asgi.py` quando `DEBUG`.
- Tag `<script>` truncada no `base.html` quebrou o Bootstrap JS inteiro (modais mortos) —
  sintoma parecia bug de aplicação.

---

## 2. Decisões de arquitetura que se provaram certas

### 2.1 App `core` com o pipeline; apps de domínio só re-exportam
`apps/core` concentra `rag_service`, `reranker`, `privacy_filter`, `tasks`, `utils`,
`exceptions`. As apps `knowledge` (institucional) e `documents` (pessoal) apenas passam
`doc_type="knowledge"|"personal"` para as mesmas tasks. Zero duplicação de pipeline.

### 2.2 Uma task de indexação parametrizada por tipo
```
index_document(doc_id, doc_type)
  1. status → "indexing"
  2. extract_text (PDF/DOCX/TXT/MD)
  3. privacy_mask (Presidio)      ← PII removido ANTES de virar embedding
  4. _chunk_text (SemanticChunker → fallback RecursiveCharacterTextSplitter)
  5. get_embeddings_batch (batch_size=32)
  6. delete chunks antigos + bulk_create(batch_size=100)
  7. status → "ready" | "error" (+ error_message)
```
Pontos que valeram:
- **Mascarar PII antes do chunking/embedding** — nada sensível chega ao pgvector.
- Texto < 50 palavras pula o SemanticChunker (custo alto, ganho nulo).
- SemanticChunker sempre com fallback: ele falha em textos degenerados.
- `error_message` persistido no modelo — o usuário vê por que falhou.
- `reindex` = `chain(delete.si(...), index.si(...))`, não uma terceira implementação.
- Exceções tipadas (`TextExtractionError`, `ChunkingError`, `EmbeddingError`, …) separam
  "erro de conteúdo" (marca error, não retenta) de "erro inesperado" (`self.retry`).

### 2.3 Retrieval adaptativo, não top-k fixo
Sempre enviar `top_k` chunks polui o prompt com ruído quando só 1 é relevante. Dois cortes:

| Setting | Onde atua | Efeito |
|---|---|---|
| `RAG_MAX_DISTANCE` (1.3) | filtro L2 no SQL | descarta candidato irrelevante antes de sair do banco |
| `RAG_RERANK_FACTOR` (3) | `top_k × factor` candidatos | recall antes do reranking |
| `RAG_MIN_RERANK_SCORE` | pós-CrossEncoder | nº final de chunks vira 1..top_k |

Com fallback: se nada passa no corte, mantém o melhor candidato (o LLM ainda pode dizer
que não há informação suficiente).

### 2.4 Dedup + score/distance nas `sources`
Chunks idênticos vindos de bases diferentes desperdiçam contexto — dedup por conteúdo
mantendo o de menor distância. Expor `score` e `distance` em cada source torna o
retrieval **depurável** (é a única forma prática de saber se o problema é retrieval ou LLM).

### 2.5 Streaming: thread + `asyncio.Queue`
`RAGService.stream()` é síncrono (LangChain). No `AsyncWebsocketConsumer`, roda em thread
separada e os tokens vão para o consumer via `loop.call_soon_threadsafe()` — o event loop
nunca bloqueia. Protocolo simples: `{"type": "token"|"done"|"error"|"info"}`.
Auth via `AuthMiddlewareStack`; recusa com códigos próprios (4001 não-autenticado,
4003 sem permissão) em vez de fechar genérico.

### 2.6 pgvector: desnormalizar a chave de filtro
`KnowledgeChunk.collection_id` é `UUIDField` desnormalizado (além da FK do documento) para
filtrar por coleção **sem JOIN** na busca vetorial. `UserChunk.user_id` idem, garantindo
isolamento por dono direto no filtro.

### 2.7 Keycloak: sync de senha só faz sentido em um sentido
Keycloak nunca expõe senhas nem hashes → Django→Keycloak via Admin API
(`PUT /admin/realms/{realm}/users/{id}/reset-password`), com hooks em
`set_password`/`save`. **Falha de sync não bloqueia a troca de senha local** (só warning),
senão o Keycloak fora do ar derruba o admin do Django.

### 2.8 Testes: separar rápidos de lentos
Marker `slow` para tudo que carrega modelo de ML; `pytest -m "not slow"` roda a suíte de
lógica em segundos. Mock de Celery e `SimpleUploadedFile` para os fluxos de upload.
~223 testes no total.

---

## 3. Checklist para o próximo projeto RAG

**Infra**

- [ ] `CREATE EXTENSION vector` garantido (init.sql via bind mount **ou** migration)
- [ ] `init.sql` só roda no volume novo — documentar `down -v`
- [ ] Healthchecks + `depends_on: condition: service_healthy`
- [ ] Portas não-padrão para não colidir com serviços locais (15432, 6380…)
- [ ] Conferir colisão de portas entre serviços do próprio compose

**Dependências**

- [ ] Índice do PyTorch com `explicit = true` e restrito ao `torch`
- [ ] Modelo spaCy/NLP como dependência versionada, não passo manual
- [ ] Imports de LangChain com fallback entre versões
- [ ] Windows: `sitecustomize.py` + guarda no `conftest.py`

**Pipeline**

- [ ] Todo modelo em `@lru_cache(maxsize=1)`
- [ ] Warm-up no `AppConfig.ready()` **e** no `worker_process_init`
- [ ] Warm-up do LLM (keep-alive) em thread daemon, sem bloquear o boot
- [ ] PII mascarado antes do embedding
- [ ] Chunking com fallback; pular semantic chunking em textos curtos
- [ ] `bulk_create` com `batch_size`; embeddings em lote
- [ ] Status + `error_message` persistidos e visíveis na UI

**Retrieval**

- [ ] Teste de integração que **prova** que a busca vetorial retorna linhas
- [ ] Nunca `except Exception` silencioso no retrieval
- [ ] `select_related` nos campos usados para montar `sources`
- [ ] Corte por distância no SQL + corte por score pós-rerank
- [ ] Dedup por conteúdo
- [ ] `score`/`distance` expostos para debug

**Dívida conhecida deste projeto (fazer melhor da próxima)**

- [ ] **Não há índice ANN (HNSW/IVFFlat) na coluna `embedding`** — a busca é sequencial.
      Funciona com poucos milhares de chunks, degrada linearmente. Criar na migration:
      `CREATE INDEX ... USING hnsw (embedding vector_l2_ops)`.
- [ ] Sem busca híbrida (BM25/full-text + vetorial) — recall sofre com termos raros,
      siglas e nomes próprios, que embeddings capturam mal.
- [ ] `RAG_MAX_DISTANCE=1.3` e `RAG_MIN_RERANK_SCORE` foram calibrados por observação,
      não por avaliação. Ligar o `ragas_eval` a um dataset dourado desde o início.
- [ ] Máscara de PII é irreversível: o chunk armazenado tem `[PESSOA]` no lugar do nome,
      o que pode inviabilizar respostas legítimas. Considerar pseudonimização reversível
      por documento.
