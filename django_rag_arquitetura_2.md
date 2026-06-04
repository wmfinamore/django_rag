# Django RAG — Arquitetura do Projeto

> Documentação técnica atualizada · baseada no código-fonte

---

## Stack

| Camada | Tecnologia |
|---|---|
| Web / API | Django 6.0 + Django REST Framework 3.15 |
| WebSocket | Django Channels 4.1 + channels-redis + Daphne 4.1 |
| Task queue | Celery 5.3+ [redis] |
| Autenticação | Keycloak 24 (OIDC) + fallback ModelBackend |
| Banco de dados | PostgreSQL 16 + pgvector |
| LLM | Ollama (llama3.2:3b · CPU) |
| Embeddings | sentence-transformers · all-MiniLM-L6-v2 · CPU (384 dims) |
| Chunking | LangChain SemanticChunker (semantic split) |
| Reranking | sentence-transformers · cross-encoder/ms-marco-MiniLM-L-6-v2 · CPU |
| Filtro de privacidade | presidio-analyzer + presidio-anonymizer (PII / LGPD) |
| Avaliação RAG | Ragas + datasets |
| Gerenciador de deps | uv + pyproject.toml (Python ≥ 3.12, < 3.14) |
| Testes | pytest + pytest-django + pytest-asyncio |

---

## 01 · Estrutura de Apps e Pastas

```
django_rag/
│
├── config/                        # configuração do projeto Django
│   ├── settings/
│   │   ├── base.py                # settings compartilhados (todos os ambientes)
│   │   ├── development.py         # + debug_toolbar, logging verbose
│   │   └── production.py
│   ├── urls.py                    # todas as rotas prefixadas em /rag/
│   ├── asgi.py
│   ├── wsgi.py
│   └── celery.py
│
├── apps/                          # todos os apps do projeto
│   │
│   ├── core/                      # artefatos comuns (habilitado)
│   │   ├── models.py              # TimeStampedModel (abstrato)
│   │   ├── rag_service.py         # núcleo RAG: embeddings, retrieval, reranking, stream
│   │   ├── reranker.py            # CrossEncoder reranker (ms-marco-MiniLM)
│   │   ├── privacy_filter.py      # filtro PII/LGPD com Presidio (mascaramento)
│   │   ├── ragas_eval.py          # avaliação do pipeline com Ragas
│   │   ├── tasks.py               # tasks Celery: index_document, delete_document, reindex_document
│   │   ├── views.py
│   │   ├── utils.py
│   │   ├── mixins.py
│   │   └── exceptions.py          # EmbeddingError, LLMError, RAGError
│   │
│   ├── accounts/                  # autenticação + usuário customizado (habilitado)
│   │   ├── models.py              # CustomUser (extends AbstractUser)
│   │   ├── oidc_backend.py        # GroupSyncOIDCBackend
│   │   ├── admin.py
│   │   ├── forms.py
│   │   ├── views.py               # home, profile
│   │   └── urls.py
│   │
│   ├── knowledge/                 # base de conhecimento institucional (habilitado)
│   │   ├── models.py              # KnowledgeCollection, KnowledgeDocument, KnowledgeChunk, BulkImportJob
│   │   ├── admin.py               # admin com inline, ações de indexação e badge de status
│   │   ├── serializers.py         # serializers DRF + upload + BulkImportJob
│   │   ├── views.py               # ViewSets REST (collections, documents, bulk-import)
│   │   ├── tasks.py               # run_bulk_import (Celery)
│   │   ├── urls.py                # router DRF
│   │   ├── tests.py               # 156 testes (23 classes)
│   │   ├── management/
│   │   │   └── commands/
│   │   │       └── bulk_ingest_knowledge.py  # importação em lote via CLI
│   │   └── migrations/
│   │       ├── 0001_initial.py    # inclui RunSQL CREATE EXTENSION vector
│   │       └── 0002_bulkimportjob_knowledgedocument_bulk_import_job.py
│   │
│   ├── documents/                 # documentos pessoais do usuário (não habilitado ainda)
│   │   ├── models.py              # UserDocument, UserChunk
│   │   ├── serializers.py
│   │   ├── views.py               # upload / delete (DRF)
│   │   ├── urls.py
│   │   └── tasks.py               # index / delete / reindex
│   │
│   └── chat/                      # conversas e streaming (não habilitado ainda)
│       ├── models.py              # Conversation, Message
│       ├── consumers.py           # ChatConsumer (WebSocket)
│       ├── routing.py
│       ├── serializers.py
│       ├── views.py
│       └── urls.py
│
├── docker/
│   ├── keycloak_setup.py          # script de setup automático do Keycloak via Admin API
│   ├── fix_pkce.py
│   ├── fix_redirect_uris.py
│   ├── download_bootstrap.py
│   └── postgres/
│       └── init.sql
│
├── templates/
│   ├── base.html
│   ├── accounts/
│   │   └── profile.html
│   ├── home.html
│   ├── chat/
│   ├── documents/
│   └── knowledge/
│
├── static/
│   ├── css/   (bootstrap.min.css)
│   └── js/    (bootstrap.bundle.min.js)
│
├── conftest.py                    # verifica pré-carregamento do torch para testes slow
├── .env
├── .env.example
├── docker-compose-infra.yml       # serviços de infra (db, redis, keycloak, redis-commander)
├── pyproject.toml
└── manage.py
```

> Apps habilitados atualmente em `INSTALLED_APPS`: `apps.core`, `apps.accounts`, `apps.knowledge`.
> Apps `documents` e `chat` estão implementados mas comentados — serão habilitados progressivamente.

---

## 02 · Modelo de Dados

### Base institucional

```
KnowledgeCollection
├── id                  UUIDField PK
├── name                CharField
├── description         TextField
├── allowed_groups      ManyToManyField → Group   ← controle de acesso
├── is_active           BooleanField
└── (TimeStampedModel)  created_at, updated_at

KnowledgeDocument
├── id                  UUIDField PK
├── collection          ForeignKey → KnowledgeCollection (CASCADE)
├── title               CharField
├── file_path           CharField
├── file_type           CharField  (pdf · docx · txt · md)
├── status              CharField  (pending · indexing · ready · error)
├── chunks_count        IntegerField
├── error_message       TextField
├── ingested_by         ForeignKey → CustomUser (SET_NULL)
├── bulk_import_job     ForeignKey → BulkImportJob (SET_NULL, nullable)  ← NULL para uploads manuais
└── (TimeStampedModel)

BulkImportJob                      ← rastreia importações em lote via diretório
├── id                  UUIDField PK
├── collection          ForeignKey → KnowledgeCollection (CASCADE)
├── source_directory    CharField  ← caminho absoluto no servidor
├── recursive           BooleanField (default True)
├── file_extensions     JSONField  ← lista de extensões aceitas; vazio = todas
├── status              CharField  (pending · running · completed · completed_with_errors · failed)
├── total_files         IntegerField
├── indexed_files       IntegerField
├── failed_files        IntegerField
├── error_message       TextField
├── celery_task_id      CharField  ← ID da task run_bulk_import
├── triggered_by        ForeignKey → CustomUser (SET_NULL)
└── (TimeStampedModel)

KnowledgeChunk                     ← tabela de vetores pgvector
├── id                  UUIDField PK
├── document            ForeignKey → KnowledgeDocument (CASCADE)
├── collection_id       UUIDField  ← desnormalizado para filtro eficiente
├── chunk_index         IntegerField
├── content             TextField
└── embedding           VectorField(384)
```

### Base pessoal

```
UserDocument
├── id                  UUIDField PK
├── owner               ForeignKey → CustomUser (CASCADE)
├── title               CharField
├── file                FileField
├── file_type           CharField
├── status              CharField  (pending · indexing · ready · error)
├── chunks_count        IntegerField
└── (TimeStampedModel)

UserChunk                          ← tabela de vetores pgvector
├── id                  UUIDField PK
├── document            ForeignKey → UserDocument (CASCADE)
├── user_id             UUIDField  ← desnormalizado para filtro eficiente
├── chunk_index         IntegerField
├── content             TextField
└── embedding           VectorField(384)
```

### Chat

```
Conversation
├── id                  UUIDField PK
├── user                ForeignKey → CustomUser
├── title               CharField
├── collections         ManyToManyField → KnowledgeCollection  ← escopo institucional
├── use_personal_docs   BooleanField  ← inclui UserChunk na busca
└── (TimeStampedModel)

Message
├── id                  UUIDField PK
├── conversation        ForeignKey → Conversation (CASCADE)
├── role                CharField  (user · assistant)
├── content             TextField
├── sources             JSONField  ← arquivos usados como contexto
└── created_at          DateTimeField
```

### Usuário customizado

```
CustomUser  (extends AbstractUser)
├── sub                 CharField unique  ← Keycloak subject ID
├── avatar_url          CharField blank   ← campo futuro
└── (demais campos do AbstractUser: username, email, first_name, last_name, groups…)
```

> `AUTH_USER_MODEL = "accounts.CustomUser"` definido desde o início — sem migrações problemáticas no futuro.

---

## 03 · Pipeline RAG

Fluxo por query (implementado em `apps/core/rag_service.py`):

```
browser / WebSocket
    │
    ▼
RAGService.stream(query)
    │
    ▼
get_embedding(query)               sentence-transformers (CPU · ~50ms)
    │                              singleton por processo via @lru_cache
    ▼
RAGService._retrieve_candidates()
    │
    ├── (se collection_ids não vazio)
    │   pgvector — KnowledgeChunk  busca top_k × rerank_factor por L2 distance
    │   filtro: collection_id IN collection_ids
    │
    └── (se use_personal_docs=True)
        pgvector — UserChunk       busca top_k × rerank_factor por L2 distance
        filtro: user_id = user.pk
    │
    ▼
rerank(query, chunks, top_k)       CrossEncoder ms-marco-MiniLM-L-6-v2 (CPU · ~30ms)
    │                              (apps/core/reranker.py)
    ▼
RAGService.build_context()         mescla chunks rerankeados + query → prompt
    │
    ▼
OllamaLLM.stream(full_prompt)     geração com streaming token a token
    │
    ▼
ChatConsumer                       yield tokens via WebSocket
    │
    ▼
Message.save()                     persiste resposta + sources (JSON)
```

**Parâmetros RAG relevantes (settings):**

| Parâmetro | Valor padrão | Descrição |
|---|---|---|
| `RAG_CHUNK_SIZE` | 500 | tamanho máximo do chunk em tokens (fallback) |
| `RAG_CHUNK_OVERLAP` | 50 | sobreposição no chunking de fallback |
| `RAG_TOP_K` | 4 | chunks finais enviados ao prompt (pós-reranking) |
| `RAG_RERANK_FACTOR` | 3 | multiplicador de candidatos pré-reranking (busca top_k × 3 = 12) |
| `RAG_SEMANTIC_BREAKPOINT` | `percentile` | estratégia do SemanticChunker (`percentile` · `std_deviation` · `interquartile`) |
| `RAG_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | modelo CrossEncoder para reranking |
| `OLLAMA_NUM_CTX` | 2048 | janela de contexto (CPU) |
| `OLLAMA_NUM_THREAD` | 4 | threads (i7-7500U) |
| `OLLAMA_TEMPERATURE` | 0.3 | temperatura do LLM (respostas mais determinísticas) |

---

## 04 · Autenticação OIDC + Fallback

Implementado em `apps/accounts/oidc_backend.py` — `GroupSyncOIDCBackend`.

```
browser
  │  GET /rag/oidc/authenticate/
  ▼
Django  →  redirect para Keycloak :8081
              │
              │  usuário faz login
              ▼
           Keycloak emite auth code
              │
              ▼  /rag/oidc/callback/
Django troca code por JWT
              │
              ▼
        GroupSyncOIDCBackend          (accounts/oidc_backend.py)
        ├── filter_users_by_claims()  →  busca por sub (não por email)
        ├── create_user() / update_user()
        └── _sync_groups()
            ├── lê claim "groups" do JWT (prefixo "/" removido)
            ├── Group.objects.get_or_create() para cada grupo
            ├── user.groups.set(grupos_do_token)
            ├── is_staff = True se in STAFF_GROUPS {"admin"}
            └── is_superuser = True se in SUPERUSER_GROUPS (vazio por padrão)
              │
              ▼
        sessão Django criada (cookie · backend: Redis)
```

**Fallback local:**

```
/rag/admin/login/  →  ModelBackend  →  somente is_staff=True
```

**settings/base.py:**

```python
AUTHENTICATION_BACKENDS = [
    "apps.accounts.oidc_backend.GroupSyncOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_REDIRECT_URL  = "/rag/"
LOGOUT_REDIRECT_URL = "/rag/"
LOGIN_URL           = "/rag/oidc/authenticate/"
```

**Keycloak — configuração de desenvolvimento:**

| Item | Valor |
|---|---|
| Realm | `django-rag` |
| Client ID | `django_cli` |
| Client tipo | confidential, Authorization Code (sem PKCE) |
| Redirect URI | `http://localhost:8000/rag/oidc/callback/` |
| Grupos criados | `admin`, `editor`, `viewer` |
| Mapper | `groups` claim → incluso em id_token, access_token e userinfo |
| Usuário de teste | `testuser` / `Test@1234` (grupos: admin, viewer) |

> O script `docker/keycloak_setup.py` automatiza toda essa configuração via Admin REST API.

---

## 05 · Tasks Assíncronas (Celery + Redis)

As tasks estão divididas em dois módulos:

- `apps/core/tasks.py` — `index_document`, `delete_document`, `reindex_document`
- `apps/knowledge/tasks.py` — `run_bulk_import` (importação em lote via BulkImportJob)

### `index_document(doc_id, doc_type)`
```
1. busca KnowledgeDocument ou UserDocument pelo doc_id
2. status → "indexing"
3. extrai texto (pdf / docx / txt / md)
4. privacy_filter.mask(texto)       ← filtro PII/LGPD antes de qualquer processamento
     ├── detecta: CPF, CNPJ, RG, e-mail, telefone, endereço,
     │           cartão de crédito, dados bancários, nomes próprios
     ├── substitui por placeholders: [CPF], [CNPJ], [EMAIL], [TELEFONE],
     │           [ENDERECO], [CARTAO], [CONTA_BANCARIA], [PESSOA]
     └── registra ocorrências mascaradas no log de auditoria
5. SemanticChunker → divide nos pontos de mudança semântica do texto mascarado
     └── fallback: RecursiveCharacterTextSplitter se o texto for muito curto
6. sentence-transformers → embeddings dos chunks em lote (CPU)
     └── get_embeddings_batch() — mais eficiente que chamar get_embedding em loop
7. bulk insert → KnowledgeChunk ou UserChunk
8. status → "ready" / "error"
```

### `delete_document(doc_id, doc_type)`
```
1. DELETE FROM knowledge_chunk WHERE document_id = doc_id
   (ou user_chunk para docs pessoais)
2. deleta KnowledgeDocument / UserDocument
3. remove arquivo físico do disco
```

### `reindex_document(doc_id, doc_type)`
```
1. chama delete_document.si(doc_id, doc_type)
2. encadeia index_document.si(doc_id, doc_type)
   via Celery chain
```

### `run_bulk_import(job_id)` — `apps/knowledge/tasks.py`
```
1. carrega BulkImportJob pelo job_id → status: "running"
2. valida source_directory (existe e é diretório acessível)
3. varre o diretório (glob recursivo ou não, conforme job.recursive)
   filtra por extensões aceitas (pdf, docx, txt, md)
4. para cada arquivo:
   ├── KnowledgeDocument.objects.create(bulk_import_job=job, ...)
   └── index_document.delay(doc.id, "knowledge")
5. atualiza contadores (indexed_files, failed_files) a cada 10 arquivos
6. status final: "completed" ou "completed_with_errors"
   (em caso de erro fatal antes da varredura: "failed")
```

O job pode ser disparado por:
- **API**: `POST /api/knowledge/collections/<id>/bulk-import/`
- **CLI**: `python manage.py bulk_ingest_knowledge <collection_id> <directory> [--options]`

Opções do management command:

| Flag | Descrição |
|---|---|
| `--extensions pdf docx` | Filtra extensões aceitas |
| `--no-recursive` | Não percorre subdiretórios |
| `--skip-existing` | Ignora arquivos já presentes na coleção |
| `--dry-run` | Lista o que seria importado sem criar nada |
| `--sync` | Indexação síncrona (sem Celery — útil para debug) |
| `--batch-size N` | Documentos por transação (padrão: 100) |
| `--user USERNAME` | Registra `triggered_by` no job |

---

## 06 · Filtro de Privacidade — PII / LGPD (Presidio)

O filtro é executado **antes do chunking**, sobre o texto bruto extraído do documento. Nenhum dado sensível chega ao pgvector.

### Biblioteca: Microsoft Presidio

Presidio roda 100% local (sem chamadas externas), com suporte nativo a português via modelos spaCy. É composto por dois pacotes:

- `presidio-analyzer` — detecta entidades PII usando NLP + regex + checksum
- `presidio-anonymizer` — substitui as entidades detectadas por placeholders configuráveis

### Entidades detectadas e placeholders

| Tipo de dado | Entidade Presidio | Placeholder |
|---|---|---|
| CPF | `BR_CPF` | `[CPF]` |
| CNPJ | `BR_CNPJ` | `[CNPJ]` |
| RG | `BR_RG` | `[RG]` |
| E-mail | `EMAIL_ADDRESS` | `[EMAIL]` |
| Telefone / celular | `PHONE_NUMBER` | `[TELEFONE]` |
| Endereço físico | `LOCATION` | `[ENDERECO]` |
| Cartão de crédito | `CREDIT_CARD` | `[CARTAO]` |
| Dados bancários (agência/conta) | `IBAN_CODE` | `[CONTA_BANCARIA]` |
| Nomes próprios (PII) | `PERSON` | `[PESSOA]` |

### Localização no projeto

```
apps/core/privacy_filter.py
```

### Comportamento

```python
# apps/core/privacy_filter.py

from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

analyzer = AnalyzerEngine()   # singleton — carregado uma vez por processo
anonymizer = AnonymizerEngine()

def mask(text: str, language: str = "pt") -> tuple[str, list[dict]]:
    """
    Mascara dados sensíveis no texto.
    Retorna (texto_mascarado, lista_de_ocorrencias).
    """
    results = analyzer.analyze(text=text, language=language, entities=ENTITIES)
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results, operators=OPERATORS)
    occurrences = [{"type": r.entity_type, "score": r.score} for r in results]
    return anonymized.text, occurrences
```

### Posição no pipeline de indexação

```
texto bruto extraído
        │
        ▼
privacy_filter.mask()      ← mascaramento PII/LGPD
        │
        ├── texto mascarado → SemanticChunker → get_embeddings_batch() → pgvector
        │
        └── ocorrências     → log de auditoria
```

### Aplicação

O filtro é aplicado em **ambas as bases**:

- `apps/core/tasks.py` — task `index_document` para `KnowledgeDocument`
- `apps/documents/tasks.py` — task `index_document` para `UserDocument`

### Modelo spaCy necessário

```bash
# instalação obrigatória antes de usar o Presidio em português
uv run python -m spacy download pt_core_news_lg
```

### Observações

- O texto **original** do arquivo nunca é armazenado no pgvector — apenas o texto mascarado entra nos chunks
- O arquivo físico original permanece no disco (`media/uploads/`) sem modificação
- O score de confiança de cada detecção fica registrado nas ocorrências, permitindo revisar falsos positivos
- Nomes próprios (`PERSON`) tendem a ter maior taxa de falsos positivos — o threshold de confiança mínimo é configurável via `PRIVACY_MIN_SCORE` (padrão: 0.7)

---

## 07 · Avaliação do Pipeline RAG (Ragas)

O Ragas mede a qualidade do pipeline RAG de forma automática, sem depender de respostas humanas anotadas. Funciona localmente com Ollama como LLM avaliador.

### Métricas coletadas

| Métrica | O que mede | Valor ideal |
|---|---|---|
| **Faithfulness** | Resposta está fundamentada nos chunks recuperados? | → 1.0 |
| **Answer Relevancy** | Resposta é relevante para a pergunta feita? | → 1.0 |
| **Context Recall** | Os chunks recuperados cobrem a resposta correta? | → 1.0 |
| **Context Precision** | Os chunks recuperados são precisos (sem ruído)? | → 1.0 |

### Configuração com Ollama local

```python
# apps/core/ragas_eval.py
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_ollama import OllamaLLM
from langchain_community.embeddings import HuggingFaceEmbeddings

ragas_llm = LangchainLLMWrapper(OllamaLLM(
    base_url=settings.OLLAMA_BASE_URL,
    model=settings.OLLAMA_LLM_MODEL,
))

ragas_embeddings = LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(
    model_name=settings.EMBEDDING_MODEL,
    model_kwargs={"device": "cpu"},
))

METRICS = [faithfulness, answer_relevancy, context_recall, context_precision]
```

### Management command de avaliação

```bash
# apps habilitados: knowledge
python manage.py eval_rag --collection politicas-rh --samples 20
# Saída:
# faithfulness        0.87
# answer_relevancy    0.91
# context_recall      0.83
# context_precision   0.79
```

### Quando executar

| Momento | Frequência |
|---|---|
| Após mudança no chunking ou embedding | A cada alteração |
| Após reindexação de coleção | Pontual |
| Monitoramento contínuo | Semanal / mensal |

---

## 08 · Infraestrutura de Desenvolvimento

### Arquivo: `docker-compose-infra.yml`

Sobe os serviços de infraestrutura. O Django e o Celery rodam diretamente no host via `uv`.

| Serviço | Imagem | Porta host → container |
|---|---|---|
| `db` | pgvector/pgvector:pg16 | **15432**:5432 |
| `redis` | redis:7-alpine | **6380**:6379 |
| `keycloak` | quay.io/keycloak/keycloak:24.0 | **8081**:8080 |
| `redis-commander` | rediscommander/redis-commander | **8082**:8081 |

> **Atenção às portas:** PostgreSQL expõe na **15432** e Redis na **6380** (não nas padrões 5432 / 6379). O Keycloak fica em **localhost:8081**.

```yaml
# Trecho relevante do docker-compose-infra.yml
services:
  db:
    image: pgvector/pgvector:pg16
    ports: ["15432:5432"]
    environment:
      POSTGRES_DB: django_rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres

  redis:
    image: redis:7-alpine
    ports: ["6380:6379"]
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru

  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    ports: ["8081:8080"]
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://db:5432/keycloak
      KC_HOSTNAME_URL: http://localhost:8081
    depends_on:
      db:
        condition: service_healthy

  redis-commander:
    image: rediscommander/redis-commander
    ports: ["8082:8081"]     # UI Redis: http://localhost:8082
```

### Configuração de endereços no `.env` (desenvolvimento)

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:15432/django_rag
REDIS_URL=redis://localhost:6380/0
CELERY_BROKER_URL=redis://localhost:6380/0
CELERY_RESULT_BACKEND=redis://localhost:6380/1
OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8081/realms/django-rag/...
```

### Fase prod (futura)

| Componente | Onde roda | Obs |
|---|---|---|
| Django + Celery | container (Rancher) | — |
| Ollama | Windows (nativo) | `OLLAMA_BASE_URL=http://host-gateway:11434` |
| PostgreSQL + pgvector | container (Rancher) | portas padrão internas |
| Redis | container (Rancher) | portas padrão internas |
| Keycloak | container (Rancher) | portas padrão internas |

### Estimativa de RAM (Rancher · 4GB)

| Serviço | RAM estimada |
|---|---|
| PostgreSQL 16 | ~300 MB |
| Redis 7 Alpine | ~30 MB |
| Keycloak 24 | ~512 MB |
| Overhead SO/runtime | ~200 MB |
| **Total containers** | **~1.04 GB** |
| **Folga disponível** | **~3 GB** (para Django + Celery em prod) |

---

## 09 · URLs do Projeto

Todas as rotas são prefixadas em `/rag/`. A raiz `/` redireciona para `/rag/`.

```
/                          →  redirect 302 para /rag/
/rag/                      →  home
/rag/admin/                →  Django Admin
/rag/oidc/authenticate/    →  inicia fluxo OIDC (redirect para Keycloak)
/rag/oidc/callback/        →  callback pós-login Keycloak
/rag/oidc/logout/          →  logout federado (Keycloak + sessão Django)
/rag/accounts/profile/     →  perfil do usuário
/rag/__debug__/            →  Django Debug Toolbar (apenas DEBUG=True)

# API de Conhecimento
/rag/api/knowledge/collections/                              →  GET lista coleções acessíveis · POST cria coleção (staff)
/rag/api/knowledge/collections/<id>/                         →  GET detalhe da coleção
/rag/api/knowledge/collections/<id>/documents/               →  GET lista docs · POST upload + indexação (staff)
/rag/api/knowledge/collections/<id>/bulk-import/             →  GET lista BulkImportJobs · POST dispara job (admin)
/rag/api/knowledge/collections/<id>/bulk-import/<job_id>/    →  GET detalhe de um BulkImportJob (admin)
/rag/api/knowledge/documents/<id>/                           →  GET detalhe do documento
/rag/api/knowledge/documents/<id>/                           →  DELETE remove doc e chunks (staff)
/rag/api/knowledge/documents/<id>/reindex/                   →  POST re-indexa o documento (admin)
```

### Controle de acesso na API de Conhecimento

| Operação | Requisito |
|---|---|
| Listar / ver coleções e documentos | autenticado + pertencer ao grupo da coleção (ou coleção pública) |
| Criar coleção | `is_staff = True` |
| Upload de documento | `is_staff = True` |
| Remover documento | `is_staff = True` |
| Re-indexar documento | `is_superuser` (`IsAdminUser`) |
| Bulk import (listar / criar / ver job) | `is_superuser` (`IsAdminUser`) |

> Coleções sem `allowed_groups` são públicas — qualquer usuário autenticado tem acesso. Superusuários acessam tudo.

---

## 10 · Dependências (pyproject.toml)

### Produção

```toml
[project]
requires-python = ">=3.12,<3.14"

dependencies = [
    # Web / API
    "django>=6.0,<7.0",
    "djangorestframework>=3.15",
    # WebSocket
    "channels>=4.1",
    "channels-redis>=4.1",
    "daphne>=4.1",
    # Autenticação OIDC
    "mozilla-django-oidc>=4.0",
    # Configuração / ambiente
    "django-environ>=0.11",
    # Cache / sessão
    "django-redis>=5.4",
    # Task queue
    "celery[redis]>=5.3",
    # Banco de dados
    "psycopg[binary]>=3.1",
    "pgvector>=0.3",
    # LangChain
    "langchain>=0.2",
    "langchain-community>=0.2",
    "langchain-postgres>=0.0.9",
    "langchain-ollama>=0.1",
    "langchain-experimental>=0.0.60",   # SemanticChunker
    # Embeddings + reranker
    "sentence-transformers>=3.0",
    # Filtro de privacidade PII/LGPD
    "presidio-analyzer>=2.2",
    "presidio-anonymizer>=2.2",
    "spacy>=3.7",
    # Extração de texto
    "pypdf>=4.0",
    "python-docx>=1.1",
    # Avaliação do pipeline RAG
    "ragas>=0.1",
    "datasets>=2.0",
    # Tipos Celery + Arrow
    "celery-types>=0.26.0",
    "pyarrow>=18.0,<19.0",
]
```

### Desenvolvimento

```toml
[dependency-groups]
dev = [
    "pytest",
    "pytest-django",
    "pytest-asyncio",
    "factory-boy",
    "ruff",
    "mypy",
    "django-stubs",
    "djangorestframework-stubs",
    "django-debug-toolbar",
    "ipython",
]
```

---

## 11 · Variáveis de Ambiente (.env.example)

```bash
# Django
SECRET_KEY=troque-em-producao-use-uma-chave-longa-e-aleatoria
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_SETTINGS_MODULE=config.settings.development

# PostgreSQL (porta 15432 — mapeada pelo docker-compose-infra.yml)
POSTGRES_DB=django_rag
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:15432/django_rag

# Redis / Celery (porta 6380 — mapeada pelo docker-compose-infra.yml)
REDIS_URL=redis://localhost:6380/0
CELERY_BROKER_URL=redis://localhost:6380/0
CELERY_RESULT_BACKEND=redis://localhost:6380/1

# Ollama
# dev: localhost | prod (container): http://host-gateway:11434
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=llama3.2:3b
OLLAMA_NUM_CTX=2048
OLLAMA_NUM_THREAD=4
OLLAMA_TEMPERATURE=0.3

# Embeddings
EMBEDDING_MODEL=all-MiniLM-L6-v2

# RAG pipeline
RAG_TOP_K=4
RAG_RERANK_FACTOR=3
RAG_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RAG_SEMANTIC_BREAKPOINT=percentile
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50

# Filtro de privacidade PII/LGPD
# PRIVACY_MIN_SCORE não está no .env.example mas é lido em settings/base.py (default 0.7)

# Keycloak OIDC (porta 8081 — mapeada pelo docker-compose-infra.yml)
OIDC_RP_CLIENT_ID=django_cli
OIDC_RP_CLIENT_SECRET=troque-pelo-secret-do-keycloak
OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/auth
OIDC_OP_TOKEN_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/token
OIDC_OP_USER_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/userinfo
OIDC_OP_JWKS_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/certs
OIDC_OP_LOGOUT_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/logout
OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS=60

# Keycloak Admin
KEYCLOAK_ADMIN_PASSWORD=admin
```

---

## 12 · Parâmetros RAG

| Parâmetro | Valor | Justificativa |
|---|---|---|
| Modelo LLM | `llama3.2:3b` | ~4GB RAM, bom equilíbrio qualidade/velocidade em CPU |
| Modelo embedding | `all-MiniLM-L6-v2` | 90MB, 384 dims, roda em CPU sem GPU |
| Modelo reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ~85MB, roda em CPU, estado da arte em reranking |
| Modelo NLP privacidade | `pt_core_news_lg` (spaCy) | NER em português para detecção de nomes e locais |
| `PRIVACY_MIN_SCORE` | 0.7 | threshold de confiança do Presidio — abaixo disso não mascara |
| `RAG_CHUNK_SIZE` | 500 tokens | tamanho máximo no chunking de fallback |
| `RAG_CHUNK_OVERLAP` | 50 tokens | sobreposição no chunking de fallback |
| `RAG_TOP_K` | 4 chunks | chunks finais no prompt (pós-reranking) |
| `RAG_RERANK_FACTOR` | 3 | busca top_k × 3 candidatos antes do reranker |
| `RAG_SEMANTIC_BREAKPOINT` | `percentile` | estratégia do SemanticChunker |
| `OLLAMA_NUM_CTX` | 2048 | janela menor = mais rápido em CPU |
| `OLLAMA_NUM_THREAD` | 4 | todos os threads do i7-7500U |
| `OLLAMA_TEMPERATURE` | 0.3 | respostas mais determinísticas para RAG |

---

## 13 · Testes

```bash
# Roda todos os testes
uv run pytest

# Pula testes lentos (carregam modelos ML)
uv run pytest -m "not slow"

# Testes com cobertura
uv run pytest --cov=apps

# Testes de um app específico
uv run pytest apps/core/tests.py
```

Cobertura por app:

| App | Arquivo | Testes |
|---|---|---|
| `apps.core` | `apps/core/tests.py` | **54** |
| `apps.accounts` | `apps/accounts/tests.py` | **13** |
| `apps.knowledge` | `apps/knowledge/tests.py` | **156** |

### Testes da app core (54 testes · 8 classes)

| Classe | O que testa |
|---|---|
| `TestExceptionHierarchy` | hierarquia EmbeddingError / LLMError / RAGError |
| `TestTimeStampedModel` | campos created_at / updated_at |
| `TestExtractTextTxt` | extração de texto de arquivos .txt |
| `TestTextUtils` | utilitários de texto |
| `TestPrivacyFilter` | mascaramento PII/LGPD com Presidio |
| `TestReranker` | CrossEncoder reranker |
| `TestEmbeddings` | geração de embeddings com sentence-transformers |
| `TestRAGServiceBuildContext` | montagem do contexto RAG |

### Testes da app accounts (13 testes · 4 classes)

| Classe | O que testa |
|---|---|
| `TestCustomUserModel` | campo `sub`, herança AbstractUser |
| `TestCustomUserCreationForm` | formulário de criação de usuário |
| `TestCustomUserChangeForm` | formulário de edição de usuário |
| `TestCustomUserAdmin` | interface admin do CustomUser |

### Testes da app knowledge (156 testes · 23 classes)

| Classe | O que testa |
|---|---|
| `TestKnowledgeCollectionModel` | `is_accessible_by()` — público, restrito por grupo, inativo |
| `TestKnowledgeDocumentModel` | `trigger_indexing()`, `trigger_reindex()` via mock Celery |
| `TestKnowledgeChunkModel` | criação de chunk com embedding de dimensão correta |
| `TestBulkImportJobModel` | propriedades `is_done`, `progress_pct` |
| `TestKnowledgeCollectionSerializer` | serialização de coleção |
| `TestKnowledgeDocumentSerializer` | serialização de documento |
| `TestKnowledgeDocumentUploadSerializer` | validação de upload (tipo, tamanho) |
| `TestBulkImportJobSerializer` | serialização de job |
| `TestBulkImportJobCreateSerializer` | validação do payload de criação de job |
| `TestIsStaffOrReadOnly` | permissão customizada — GET vs POST para staff/não-staff |
| `TestCollectionListAPI` | GET lista, filtragem por grupo, 401 anônimo |
| `TestCollectionRetrieveAPI` | detalhe de coleção — acesso/restrição por grupo |
| `TestCollectionCreateAPI` | POST cria coleção (staff) |
| `TestCollectionDocumentsListAPI` | GET lista docs da coleção, filtro por status |
| `TestCollectionDocumentsUploadAPI` | POST upload — tipos válidos/inválidos, 400/201 |
| `TestBulkImportListAPI` | GET lista BulkImportJobs (admin) |
| `TestBulkImportCreateAPI` | POST cria job — body válido/inválido, 202 |
| `TestBulkImportDetailAPI` | GET detalhe de job específico |
| `TestDocumentRetrieveAPI` | GET detalhe do documento — acesso/restrição |
| `TestDocumentDestroyAPI` | DELETE — staff vs não-staff, fallback sem Celery |
| `TestDocumentReindexAPI` | POST reindex — admin vs não-admin, doc em indexação (409) |
| `TestRunBulkImportTask` | task `run_bulk_import` — varredura, contadores, status final |
| `TestBulkIngestCommand` | management command `bulk_ingest_knowledge` — flags, dry-run, skip-existing |

> Celery é mockado via `@patch("apps.core.tasks.index_document.delay")` — testes não precisam de Redis.
> Uploads usam `django.test.SimpleUploadedFile` — não requerem disco real.

Marcadores disponíveis:

| Marker | Descrição |
|---|---|
| `slow` | Testes que carregam sentence-transformers, Presidio ou CrossEncoder |

Configuração em `pyproject.toml`:
```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.development"
asyncio_mode = "auto"
python_files = ["test_*.py", "*_test.py", "tests.py"]
```
