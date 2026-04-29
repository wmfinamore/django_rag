# Django RAG — Arquitetura do Projeto

> Proposta técnica completa · versão para avaliação

---

## Stack

| Camada | Tecnologia |
|---|---|
| Web / API | Django 4.2 + Django REST Framework |
| WebSocket | Django Channels + channels-redis |
| Task queue | Celery + Redis 7 |
| Autenticação | Keycloak 24 (OIDC) + fallback ModelBackend |
| Banco de dados | PostgreSQL 16 + pgvector |
| LLM | Ollama (llama3.2:3b · CPU) |
| Embeddings | sentence-transformers · all-MiniLM-L6-v2 · CPU |
| Chunking | LangChain SemanticChunker (semantic split) |
| Reranking | sentence-transformers · ms-marco-MiniLM-L-6-v2 · CPU |
| Filtro de privacidade | presidio-analyzer + presidio-anonymizer (PII / LGPD) |
| Avaliação RAG | Ragas + datasets |
| Gerenciador de deps | uv + pyproject.toml |
| Testes | pytest + pytest-django |

---

## 01 · Estrutura de Apps e Pastas

```
django_rag/
│
├── config/                        # settings app (não é um app Django comum)
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   ├── asgi.py
│   ├── wsgi.py
│   └── celery.py
│
├── apps/                          # todos os apps do projeto
│   │
│   ├── core/                      # artefatos comuns
│   │   ├── models.py              # TimeStampedModel (abstrato)
│   │   ├── rag_service.py         # núcleo RAG: embeddings, retrieval, reranking, stream
│   │   ├── reranker.py            # CrossEncoder reranker (ms-marco-MiniLM)
│   │   ├── privacy_filter.py      # filtro PII/LGPD com Presidio (mascaramento)
│   │   ├── ragas_eval.py          # avaliação do pipeline com Ragas
│   │   ├── tasks.py               # tasks Celery compartilhadas
│   │   ├── utils.py
│   │   ├── mixins.py
│   │   └── exceptions.py
│   │
│   ├── accounts/                  # autenticação + usuário customizado
│   │   ├── models.py              # CustomUser (extends AbstractUser)
│   │   ├── oidc_backend.py        # GroupSyncOIDCBackend
│   │   ├── admin.py
│   │   ├── forms.py
│   │   ├── views.py
│   │   └── urls.py
│   │
│   ├── knowledge/                 # base de conhecimento institucional
│   │   ├── models.py              # KnowledgeCollection, KnowledgeDocument, KnowledgeChunk
│   │   ├── admin.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── management/
│   │       └── commands/
│   │           ├── ingest_knowledge.py   # CLI de ingestão em lote
│   │           └── eval_rag.py           # CLI de avaliação com Ragas
│   │
│   ├── documents/                 # documentos pessoais do usuário
│   │   ├── models.py              # UserDocument, UserChunk
│   │   ├── serializers.py
│   │   ├── views.py               # upload / delete (DRF)
│   │   ├── urls.py
│   │   └── tasks.py               # index / delete / reindex
│   │
│   └── chat/                      # conversas e streaming
│       ├── models.py              # Conversation, Message
│       ├── consumers.py           # ChatConsumer (WebSocket)
│       ├── routing.py
│       ├── serializers.py
│       ├── views.py
│       └── urls.py
│
├── templates/
│   ├── base.html
│   ├── accounts/
│   ├── chat/
│   ├── documents/
│   └── knowledge/
│
├── static/
│   ├── css/
│   ├── js/
│   └── img/
│
├── tests/
│   ├── conftest.py
│   ├── accounts/
│   ├── core/
│   ├── knowledge/
│   ├── documents/
│   └── chat/
│
├── .env
├── .env.example
├── docker-compose.yml
├── pyproject.toml
└── Makefile
```

> Cada app contém: `__init__.py`, `apps.py`, `models.py`, `migrations/`, `admin.py`, `tests/`

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
├── file_type           CharField  (pdf, docx, txt, md)
├── status              CharField  (pending · indexing · ready · error)
├── chunks_count        IntegerField
├── error_message       TextField
├── ingested_by         ForeignKey → CustomUser
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

Fluxo por query:

```
browser / WebSocket
    │
    ▼
sentence-transformers          gera embedding da query (CPU · ~50ms)
    │
    ▼
pgvector — KnowledgeChunk      busca top-k × RAG_RERANK_FACTOR por similaridade
    filtro: collection_id IN (coleções do usuário via groups)
    │
    + (se use_personal_docs=True)
    │
pgvector — UserChunk           busca top-k × RAG_RERANK_FACTOR por similaridade
    filtro: user_id = request.user.id
    │
    ▼
CrossEncoder reranker          reordena todos os chunks candidatos
    modelo: ms-marco-MiniLM-L-6-v2 (CPU · ~30ms)
    seleciona os RAG_TOP_K mais relevantes após reranking
    │
    ▼
rag_service.build_prompt()     mescla chunks rerankeados + query → prompt
    │
    ▼
Ollama — llama3.2:3b           geração com streaming (CPU · ~2-5 tok/s)
    │
    ▼
ChatConsumer                   yield tokens via WebSocket
    │
    ▼
Message.save()                 persiste resposta + sources (JSON)
```

**Parâmetros RAG relevantes (settings):**

| Parâmetro | Valor padrão | Descrição |
|---|---|---|
| `RAG_CHUNK_SIZE` | 500 | tamanho máximo do chunk em tokens (fallback) |
| `RAG_CHUNK_OVERLAP` | 50 | sobreposição no chunking de fallback |
| `RAG_TOP_K` | 4 | chunks finais enviados ao prompt (pós-reranking) |
| `RAG_RERANK_FACTOR` | 3 | multiplicador de candidatos pré-reranking (busca top-k × 3 = 12) |
| `RAG_SEMANTIC_BREAKPOINT` | `percentile` | estratégia do SemanticChunker (`percentile` · `std_deviation` · `interquartile`) |
| `RAG_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | modelo CrossEncoder para reranking |
| `OLLAMA_NUM_CTX` | 2048 | janela de contexto (CPU) |
| `OLLAMA_NUM_THREAD` | 4 | threads (i7-7500U) |

---

## 04 · Autenticação OIDC + Fallback

```
browser
  │  GET /login/
  ▼
Django  →  redirect para Keycloak :8080
              │
              │  usuário faz login
              ▼
           Keycloak emite auth code
              │
              ▼  /oidc/callback/
Django troca code por JWT
              │
              ▼
        GroupSyncOIDCBackend          (accounts/oidc_backend.py)
        ├── lê claim "groups" do JWT
        ├── Group.objects.get_or_create() para cada grupo
        ├── user.groups.set(grupos_do_token)
        └── CustomUser.objects.get_or_create(sub=payload["sub"])
              │
              ▼
        sessão Django criada (cookie)
```

**Fallback local:**

```
/admin/login/  →  ModelBackend  →  somente is_staff=True
```

**settings/base.py:**

```python
AUTHENTICATION_BACKENDS = [
    "accounts.oidc_backend.GroupSyncOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]
```

**Keycloak — configurações necessárias:**

- Realm: `django-rag`
- Client: `django` (confidential, redirect URI: `http://localhost:8000/oidc/callback/`)
- Mapper: `Group Membership` → claim name `groups` → incluído no access token

---

## 05 · Tasks Assíncronas (Celery + Redis)

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
     └── registra ocorrências mascaradas no log de auditoria (pghistory context)
5. SemanticChunker → divide nos pontos de mudança semântica do texto mascarado
     └── fallback: RecursiveCharacterTextSplitter se o texto for muito curto
6. sentence-transformers → embeddings dos chunks (CPU)
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

ENTITIES = [
    "BR_CPF", "BR_CNPJ", "BR_RG",
    "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION",
    "CREDIT_CARD", "IBAN_CODE", "PERSON",
]

OPERATORS = {
    "BR_CPF":         OperatorConfig("replace", {"new_value": "[CPF]"}),
    "BR_CNPJ":        OperatorConfig("replace", {"new_value": "[CNPJ]"}),
    "BR_RG":          OperatorConfig("replace", {"new_value": "[RG]"}),
    "EMAIL_ADDRESS":  OperatorConfig("replace", {"new_value": "[EMAIL]"}),
    "PHONE_NUMBER":   OperatorConfig("replace", {"new_value": "[TELEFONE]"}),
    "LOCATION":       OperatorConfig("replace", {"new_value": "[ENDERECO]"}),
    "CREDIT_CARD":    OperatorConfig("replace", {"new_value": "[CARTAO]"}),
    "IBAN_CODE":      OperatorConfig("replace", {"new_value": "[CONTA_BANCARIA]"}),
    "PERSON":         OperatorConfig("replace", {"new_value": "[PESSOA]"}),
}

def mask(text: str, language: str = "pt") -> tuple[str, list[dict]]:
    """
    Mascara dados sensíveis no texto.
    Retorna (texto_mascarado, lista_de_ocorrencias).
    A lista de ocorrências é gravada no log de auditoria via pghistory context.
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
        ├── texto mascarado → SemanticChunker → embeddings → pgvector
        │
        └── ocorrências     → pghistory context → log de auditoria
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
- O arquivo físico original permanece no disco (`media/uploads/`) sem modificação — o mascaramento é aplicado apenas ao texto extraído em memória durante a indexação
- O score de confiança de cada detecção fica registrado nas ocorrências, permitindo revisar falsos positivos
- Nomes próprios (`PERSON`) tendem a ter maior taxa de falsos positivos — o threshold de confiança mínimo é configurável via `PRIVACY_MIN_SCORE`

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

def evaluate_pipeline(dataset):
    """
    dataset: lista de dicts com keys:
      - question       (str)
      - answer         (str — resposta gerada pelo LLM)
      - contexts       (list[str] — chunks usados no prompt)
      - ground_truth   (str — resposta esperada, para context_recall)
    """
    return evaluate(
        dataset=dataset,
        metrics=METRICS,
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )
```

### Management command de avaliação

```
apps/knowledge/management/commands/
└── eval_rag.py          # python manage.py eval_rag --collection <slug> --samples 20
```

Execução:
```bash
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

## 08 · Infraestrutura de Deployment

### Fase dev (atual)

| Componente | Onde roda | Endereço |
|---|---|---|
| Django + Celery | Windows (uv run) | localhost:8000 |
| Ollama | Windows (nativo) | 0.0.0.0:11434 |
| PostgreSQL + pgvector | Rancher (container) | localhost:5432 |
| Redis | Rancher (container) | localhost:6379 |
| Keycloak | Rancher (container) | localhost:8080 |

### Fase prod (futura)

| Componente | Onde roda | Endereço |
|---|---|---|
| Django + Celery | Rancher (container) | — |
| Ollama | Windows (nativo) | host-gateway:11434 |
| PostgreSQL + pgvector | Rancher (container) | postgres:5432 |
| Redis | Rancher (container) | redis:6379 |
| Keycloak | Rancher (container) | keycloak:8080 |

### docker-compose.yml — serviços

```yaml
services:

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: django_rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]
    healthcheck: ...

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes:
      - redis_data:/data

  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: postgres
      KC_DB_PASSWORD: ${POSTGRES_PASSWORD}
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
    command: start-dev
    ports: ["8080:8080"]
    depends_on: [postgres]

# fase prod — Django + Celery entram aqui
# extra_hosts: ["host-gateway:host-gateway"]
# OLLAMA_BASE_URL: http://host-gateway:11434
```

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

## 09 · Dependências (pyproject.toml)

### Produção

```toml
[project.dependencies]
django = ">=4.2,<5.0"
djangorestframework = ">=3.14"
django-channels = ">=4.0"
channels-redis = ">=4.0"
mozilla-django-oidc = ">=4.0"
django-environ = ">=0.11"
django-redis = ">=5.4"
celery = {extras = ["redis"], version = ">=5.3"}
psycopg = {extras = ["binary"], version = ">=3.1"}
pgvector = ">=0.3"
langchain = ">=0.2"
langchain-community = ">=0.2"
langchain-postgres = ">=0.0.9"
langchain-ollama = ">=0.1"
langchain-experimental = ">=0.0.60"   # SemanticChunker
sentence-transformers = ">=3.0"       # embeddings + CrossEncoder reranker
presidio-analyzer = ">=2.2"           # detecção de PII/LGPD
presidio-anonymizer = ">=2.2"         # mascaramento de PII/LGPD
spacy = ">=3.7"                       # NLP para Presidio (pt_core_news_lg)
pypdf = ">=4.0"
python-docx = ">=1.1"
ragas = ">=0.1"                       # avaliação do pipeline RAG
datasets = ">=2.0"                    # dependência do ragas
```

### Desenvolvimento

```toml
[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-django",
  "pytest-asyncio",
  "factory-boy",
  "ruff",
  "mypy",
  "django-debug-toolbar",
  "ipython",
]
```

---

## 10 · Variáveis de Ambiente (.env.example)

```bash
# Django
SECRET_KEY=troque-em-producao
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_SETTINGS_MODULE=config.settings.development

# PostgreSQL
POSTGRES_DB=django_rag
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/django_rag

# Redis / Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Ollama
# dev: localhost | prod (container): http://host-gateway:11434
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=llama3.2:3b
EMBEDDING_MODEL=all-MiniLM-L6-v2

# RAG pipeline
RAG_TOP_K=4
RAG_RERANK_FACTOR=3
RAG_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RAG_SEMANTIC_BREAKPOINT=percentile
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50

# Filtro de privacidade PII/LGPD
PRIVACY_MIN_SCORE=0.7          # score mínimo de confiança para mascarar (0.0–1.0)
PRIVACY_LANGUAGE=pt            # idioma do analisador Presidio

# Keycloak OIDC
OIDC_RP_CLIENT_ID=django
OIDC_RP_CLIENT_SECRET=troque-pelo-secret-do-keycloak
OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8080/realms/django-rag/protocol/openid-connect/auth
OIDC_OP_TOKEN_ENDPOINT=http://localhost:8080/realms/django-rag/protocol/openid-connect/token
OIDC_OP_USER_ENDPOINT=http://localhost:8080/realms/django-rag/protocol/openid-connect/userinfo
OIDC_OP_JWKS_ENDPOINT=http://localhost:8080/realms/django-rag/protocol/openid-connect/certs

# Keycloak Admin (docker-compose)
KEYCLOAK_ADMIN_PASSWORD=admin
```

---

## 11 · Parâmetros RAG

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
| `RAG_RERANK_FACTOR` | 3 | busca top-k × 3 candidatos antes do reranker |
| `RAG_SEMANTIC_BREAKPOINT` | `percentile` | estratégia do SemanticChunker |
| `OLLAMA_NUM_CTX` | 2048 | janela menor = mais rápido em CPU |
| `OLLAMA_NUM_THREAD` | 4 | todos os threads do i7-7500U |
| Temperatura | 0.3 | respostas mais determinísticas para RAG |
