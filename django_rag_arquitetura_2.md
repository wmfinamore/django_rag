# Django RAG — Arquitetura do Projeto

> Proposta técnica completa · versão para avaliação

---

## Stack

| Camada | Tecnologia |
|---|---|
| Web / API | Django 6.0.4 + Django REST Framework |
| WebSocket | Django Channels + channels-redis |
| Task queue | Celery + Redis 7 |
| Autenticação | Keycloak 24 (OIDC) + fallback ModelBackend |
| Banco de dados | PostgreSQL 16 + pgvector |
| LLM | Ollama (llama3.2:3b · CPU) |
| Embeddings | sentence-transformers · all-MiniLM-L6-v2 · CPU |
| Chunking | LangChain SemanticChunker (semantic split) |
| Reranking | sentence-transformers · ms-marco-MiniLM-L-6-v2 · CPU |
| Avaliação RAG | Ragas + datasets |
| Python | 3.14 |
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
├── docker-compose-infra.yml
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

## 03 · Convenção de Comentários no Banco

Todo model do projeto deve documentar a si mesmo e aos seus campos customizados diretamente no schema do PostgreSQL, usando os recursos do Django 4.2+ que materializam essas descrições como `COMMENT ON TABLE` e `COMMENT ON COLUMN` no banco. Isso permite que ferramentas externas (DBeaver, pgAdmin, dbt docs, geradores de ER) leiam a documentação sem precisar do código-fonte.

### Regras

1. **Todo model declara `Meta.db_table_comment`** descrevendo o propósito da tabela em uma ou duas frases.
2. **Todo campo customizado (definido pelo projeto, não herdado) declara `db_comment`** descrevendo o significado do dado, unidade quando aplicável, e regras de NULL/vazio.
3. **Campos herdados** (de `AbstractUser`, `TimeStampedModel`, etc.) não precisam de `db_comment` na subclasse — o comentário deve estar na superclasse.
4. **`db_comment` ≠ `help_text`**: `help_text` é para o admin/forms, `db_comment` é para o DBA. Eles podem repetir conteúdo, mas têm públicos diferentes — o `db_comment` deve ser autoexplicativo sem contexto da UI.
5. **Idioma**: comentários em português, alinhado com `verbose_name` e `help_text` do projeto.

### Exemplo canônico (CustomUser)

```python
class CustomUser(AbstractUser):
    sub = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_comment=(
            "Keycloak subject ID (claim 'sub' do JWT OIDC). Identificador "
            "canônico do usuário quando autenticado via Keycloak. NULL para "
            "contas locais criadas via admin (fallback ModelBackend)."
        ),
    )

    avatar_url = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="URL pública do avatar do usuário. Vazio quando não definido.",
    )

    class Meta:
        db_table_comment = (
            "Usuários da aplicação django_rag. Estende auth.AbstractUser "
            "adicionando o 'sub' do Keycloak (OIDC) e avatar_url. "
            "Registrado como AUTH_USER_MODEL desde a primeira migration."
        )
```

### Aplicação por model

Esta convenção se aplica a **todos** os models listados na seção 02:

| Model | `db_table_comment` esperado | Campos com `db_comment` |
|---|---|---|
| `CustomUser` | Usuários do sistema (OIDC + local) | `sub`, `avatar_url` |
| `KnowledgeCollection` | Coleções de conhecimento institucional, com ACL por grupo | `name`, `description`, `allowed_groups`, `is_active` |
| `KnowledgeDocument` | Documentos institucionais ingeridos numa coleção | `collection`, `title`, `file_path`, `file_type`, `status`, `chunks_count`, `error_message`, `ingested_by` |
| `KnowledgeChunk` | Chunks vetoriais (pgvector) de docs institucionais | `document`, `collection_id`, `chunk_index`, `content`, `embedding` |
| `UserDocument` | Documentos pessoais do usuário | `owner`, `title`, `file`, `file_type`, `status`, `chunks_count` |
| `UserChunk` | Chunks vetoriais (pgvector) de docs pessoais | `document`, `user_id`, `chunk_index`, `content`, `embedding` |
| `Conversation` | Conversas de chat com escopo de coleções e docs pessoais | `user`, `title`, `collections`, `use_personal_docs` |
| `Message` | Mensagens individuais de uma conversa | `conversation`, `role`, `content`, `sources` |

### TimeStampedModel (campos herdados)

O mixin `apps.core.models.TimeStampedModel` deve declarar `db_comment` em `created_at` e `updated_at` uma única vez — todos os models que herdarem dele recebem o comentário automaticamente, sem precisar redeclarar.

### Verificação

Após `makemigrations`, conferir os SQL gerados contém `COMMENT ON TABLE` e `COMMENT ON COLUMN`:

```bash
python manage.py sqlmigrate accounts 0001 | grep -i COMMENT
```

E no banco, após `migrate`:

```sql
SELECT obj_description('accounts_customuser'::regclass);
SELECT col_description('accounts_customuser'::regclass, attnum)
  FROM pg_attribute WHERE attrelid = 'accounts_customuser'::regclass AND attnum > 0;
```

---

## 04 · Pipeline RAG

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

## 05 · Autenticação OIDC + Fallback

### Fluxo de login

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
              ▼  GET /rag/oidc/callback/
Django troca code por JWT (id_token + access_token)
              │
              ▼
        GroupSyncOIDCBackend          (accounts/oidc_backend.py)
        ├── filter_users_by_claims()  → busca por sub (não por e-mail)
        ├── create_user() / update_user()
        │     ├── persiste username, email, first_name, last_name, sub
        │     └── chama _sync_groups()
        └── _sync_groups()
              ├── lê claim "groups" do JWT (prefixo "/" removido)
              ├── Group.objects.get_or_create() para cada grupo
              ├── user.groups.set(grupos_do_token)
              ├── is_staff = True se grupo ∈ STAFF_GROUPS {"admin"}
              └── is_superuser = True se grupo ∈ SUPERUSER_GROUPS {}
              │
              ▼
        id_token salvo na sessão (OIDC_STORE_ID_TOKEN = True)
              │
              ▼
        sessão Django criada → redirect para /rag/
```

### Fluxo de logout

```
browser
  │  GET /rag/accounts/logout/
  ▼
keycloak_logout view              (accounts/views.py)
  ├── lê id_token da sessão
  ├── destroi sessão Django (django_logout)
  └── redirect para Keycloak end_session_endpoint
        ?id_token_hint=<token>
        &post_logout_redirect_uri=http://localhost:8000/rag/
              │
              ▼
        Keycloak invalida sessão SSO
              │
              ▼
        redirect de volta para /rag/
```

### Sincronização de permissões por grupo

| Grupo Keycloak | `is_staff` | `is_superuser` |
|---|---|---|
| `admin` | ✅ True | ❌ False |
| `editor` | ❌ False | ❌ False |
| `viewer` | ❌ False | ❌ False |

Para alterar os grupos que concedem staff/superuser, edite as constantes em `apps/accounts/oidc_backend.py`:

```python
class GroupSyncOIDCBackend(OIDCAuthenticationBackend):
    STAFF_GROUPS: frozenset[str] = frozenset({"admin"})
    SUPERUSER_GROUPS: frozenset[str] = frozenset()
```

A sincronização ocorre em **todo login** — se o usuário for removido do grupo `admin` no Keycloak, `is_staff` volta a `False` no próximo login.

### Fallback local

```
/rag/admin/login/  →  ModelBackend  →  usuários criados manualmente com is_staff=True
```

### Settings relevantes

```python
# settings/base.py
AUTHENTICATION_BACKENDS = [
    "apps.accounts.oidc_backend.GroupSyncOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]

OIDC_RP_SIGN_ALGO = "RS256"
OIDC_STORE_ID_TOKEN = True          # necessário para logout federado
LOGIN_URL = "/rag/oidc/authenticate/"
LOGIN_REDIRECT_URL = "/rag/"
LOGOUT_REDIRECT_URL = "/rag/"
```

### Keycloak — configurações necessárias

- Realm: `django-rag`
- Client: `django_cli` (confidential, PKCE **desabilitado**)
- Redirect URIs: `http://localhost:8000/rag/oidc/callback/`, `http://127.0.0.1:8000/rag/oidc/callback/`
- Console admin: `http://localhost:8081`
- Mappers: `groups` (Group Membership), `given_name`, `family_name`
- Script de setup automático: `python docker/keycloak_setup.py`

---

## 06 · Tasks Assíncronas (Celery + Redis)

### `index_document(doc_id, doc_type)`
```
1. busca KnowledgeDocument ou UserDocument pelo doc_id
2. status → "indexing"
3. extrai texto (pdf / docx / txt / md)
4. SemanticChunker → divide nos pontos de mudança semântica do texto
     └── fallback: RecursiveCharacterTextSplitter se o texto for muito curto
5. sentence-transformers → embeddings dos chunks (CPU)
6. bulk insert → KnowledgeChunk ou UserChunk
7. status → "ready" / "error"
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
| Ollama | Windows (nativo) | localhost:11434 |
| PostgreSQL + pgvector | Portainer (container) | localhost:15432 |
| Redis | Portainer (container) | localhost:6380 |
| Keycloak | Portainer (container) | localhost:8081 |
| Redis Commander | Portainer (container) | localhost:8082 |

> Em desenvolvimento, Django e Celery rodam direto no host — não são containerizados. O Ollama também roda nativamente no Windows. Os containers (PostgreSQL, Redis, Keycloak) expõem suas portas para o host, e o Django acessa tudo via `localhost`.

### Fase prod (futura)

| Componente | Onde roda | Endereço |
|---|---|---|
| Django + Celery | Portainer (container) | — |
| Ollama | Windows (nativo) | host-gateway:11434 |
| PostgreSQL + pgvector | Portainer (container) | postgres:5432 |
| Redis | Portainer (container) | redis:6379 |
| Keycloak | Portainer (container) | keycloak:8080 |

> Em produção, quando Django entrar em container, o Ollama (ainda no host Windows) será acessado via `host-gateway`. Adicionar ao serviço Django: `extra_hosts: ["host-gateway:host-gateway"]` e `OLLAMA_BASE_URL=http://host-gateway:11434`.

### docker-compose-infra.yml — serviços

O arquivo `docker-compose-infra.yml` na raiz do projeto define apenas a infra de suporte (banco, cache, identity provider). Django, Celery e Ollama **não estão** no compose durante o desenvolvimento.

```yaml
services:

  db:                               # PostgreSQL 16 + pgvector
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]

  redis:                            # Redis 7 — cache, Celery broker, channel layer
    image: redis:7-alpine
    ports: ["6379:6379"]

  keycloak:                         # Keycloak 24 — OIDC Identity Provider
    image: quay.io/keycloak/keycloak:24.0
    ports: ["8080:8080"]
    depends_on: [db]

  redis-commander:                  # UI web para inspecionar o Redis
    image: rediscommander/redis-commander:latest
    ports: ["8081:8081"]
```

O script `docker/postgres/init.sql` (montado no container do banco) cria o banco `keycloak` e habilita a extensão `vector` no banco `django_rag` automaticamente na primeira inicialização.

### Estimativa de RAM (Portainer · 4GB)

| Serviço | RAM estimada |
|---|---|
| PostgreSQL 16 | ~300 MB |
| Redis 7 Alpine | ~30 MB |
| Keycloak 24 | ~512 MB |
| Redis Commander | ~50 MB |
| Overhead SO/runtime | ~200 MB |
| **Total containers** | **~1.09 GB** |
| **Folga disponível** | **~2.9 GB** (para Django + Celery em prod) |

---

## 09 · Dependências (pyproject.toml)

### Produção

```toml
[project.dependencies]
django = ">=6.0,<7.0"
djangorestframework = ">=3.15"
django-channels = ">=4.1"
channels-redis = ">=4.1"
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
DATABASE_URL=postgresql://postgres:postgres@localhost:15432/django_rag

# Redis / Celery
REDIS_URL=redis://localhost:6380/0
CELERY_BROKER_URL=redis://localhost:6380/0
CELERY_RESULT_BACKEND=redis://localhost:6380/1

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

# Keycloak OIDC
# Client ID e secret gerados pelo keycloak_setup.py
OIDC_RP_CLIENT_ID=django_cli
OIDC_RP_CLIENT_SECRET=troque-pelo-secret-do-keycloak
OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/auth
OIDC_OP_TOKEN_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/token
OIDC_OP_USER_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/userinfo
OIDC_OP_JWKS_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/certs
OIDC_OP_LOGOUT_ENDPOINT=http://localhost:8081/realms/django-rag/protocol/openid-connect/logout
OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS=60

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
| `RAG_CHUNK_SIZE` | 500 tokens | tamanho máximo no chunking de fallback |
| `RAG_CHUNK_OVERLAP` | 50 tokens | sobreposição no chunking de fallback |
| `RAG_TOP_K` | 4 chunks | chunks finais no prompt (pós-reranking) |
| `RAG_RERANK_FACTOR` | 3 | busca top-k × 3 candidatos antes do reranker |
| `RAG_SEMANTIC_BREAKPOINT` | `percentile` | estratégia do SemanticChunker |
| `OLLAMA_NUM_CTX` | 2048 | janela menor = mais rápido em CPU |
| `OLLAMA_NUM_THREAD` | 4 | todos os threads do i7-7500U |
| Temperatura | 0.3 | respostas mais determinísticas para RAG |

---

## 12 · Roteamento de URLs

### Prefixo global `/rag/`

Todas as URLs do projeto são servidas sob o prefixo `/rag/`. A raiz `/` redireciona permanentemente para `/rag/`. O arquivo `config/urls.py` agrupa as rotas internas em `base_urlpatterns` e as envolve com `path('rag/', include(...))`:

```python
# config/urls.py
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from apps.accounts import views as accounts_views

base_urlpatterns = [
    path('admin/', admin.site.urls),
    path('oidc/', include('mozilla_django_oidc.urls')),   # login, callback
    path('accounts/', include('apps.accounts.urls', namespace='accounts')),
    path('', accounts_views.home, name='home'),
]

if settings.DEBUG:
    import debug_toolbar
    base_urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + base_urlpatterns

urlpatterns = [
    path('', RedirectView.as_view(url='/rag/', permanent=False)),
    path('rag/', include(base_urlpatterns)),
]
```

### Tabela de endereços

| Recurso | URL |
|---|---|
| Home | `http://localhost:8000/rag/` |
| Login (inicia fluxo OIDC) | `http://localhost:8000/rag/oidc/authenticate/` |
| Callback OIDC | `http://localhost:8000/rag/oidc/callback/` |
| Logout federado | `http://localhost:8000/rag/accounts/logout/` |
| Perfil do usuário | `http://localhost:8000/rag/accounts/profile/` |
| Django Admin | `http://localhost:8000/rag/admin/` |
| Debug Toolbar *(dev)* | `http://localhost:8000/rag/__debug__/` |

> A porta padrão em desenvolvimento é `8000`. Ao rodar via proxy reverso (ex.: Nginx) a porta pode mudar, mas o prefixo `/rag/` permanece fixo.

### Configuração OIDC — redirect URIs

O client `django_cli` no Keycloak deve ter as seguintes redirect URIs cadastradas (o script `keycloak_setup.py` já configura isso automaticamente):

```
http://localhost:8000/rag/oidc/callback/
http://127.0.0.1:8000/rag/oidc/callback/
```

> `localhost` e `127.0.0.1` são tratados como URIs distintas pelo Keycloak — ambas precisam estar cadastradas.

### Debug Toolbar

O middleware `DebugToolbarMiddleware` e a rota `__debug__/` são registrados **apenas quando `DEBUG=True`** (ambiente de desenvolvimento). Em produção o bloco `if settings.DEBUG` não é executado e a toolbar não fica exposta.

---

## 13 · Convenção de Commits

O projeto adota o padrão **Conventional Commits** com mensagens inteiramente em **português do Brasil**.

### Formato

```
<tipo>(<escopo>): <descrição curta no imperativo>

[corpo opcional — explica o "porquê", não o "o quê"]

[rodapé opcional — breaking changes, closes #issue]
```

- **Tipo e escopo** sempre em minúsculas.
- **Descrição** em letras minúsculas, sem ponto final, no imperativo presente ("adicionar", "corrigir", "remover").
- **Limite de 72 caracteres** na linha de assunto.
- Corpo e rodapé separados da linha de assunto por uma linha em branco.

### Tipos permitidos

| Tipo | Quando usar |
|---|---|
| `feat` | Nova funcionalidade para o usuário |
| `fix` | Correção de bug |
| `refactor` | Refatoração sem mudança de comportamento |
| `chore` | Tarefas de manutenção, configuração, dependências |
| `docs` | Alterações exclusivamente em documentação |
| `test` | Adição ou correção de testes |
| `perf` | Melhoria de performance |
| `style` | Formatação, espaços, ponto-e-vírgula (sem lógica) |
| `ci` | Configuração de pipelines e automações |
| `revert` | Reversão de commit anterior |

### Escopos sugeridos

Correspondem aos apps e módulos do projeto:

`accounts` · `knowledge` · `documents` · `chat` · `core` · `settings` · `urls` · `celery` · `oidc` · `rag` · `docker` · `deps`

### Exemplos

```
feat(chat): implementar streaming de tokens via WebSocket

fix(accounts): corrigir sincronização de grupos no callback OIDC

refactor(core): extrair lógica de reranking para classe dedicada

chore(deps): atualizar django para 6.0.4

docs(arquitetura): adicionar seção de roteamento de URLs

test(knowledge): adicionar testes de integração para ingestão de PDF

feat(urls): adicionar prefixo /rag/ em todas as rotas do projeto

BREAKING CHANGE: todas as URLs agora exigem o prefixo /rag/
```

### Breaking changes

Quando um commit introduz incompatibilidade, inclua `BREAKING CHANGE:` no rodapé com descrição do impacto e, se possível, instruções de migração.

---

## 14 · Atualização para Django 6.0.4  <!-- era seção 12 -->

### Mudanças principais

**Django 6.0.4** traz melhorias significativas em relação à versão 4.2:

| Aspecto | Mudança |
|---|---|
| **Async views** | Suporte nativo a async/await em views e middleware |
| **Database layer** | Melhorias em performance de queries e ORM |
| **Security** | Proteção aprimorada contra ataques CSRF e XSS |
| **Serializers** | DRF integrado com melhor validação de dados |
| **Channels** | Compatibilidade melhorada com WebSockets e consumers assíncronos |
| **Performance** | Otimizações gerais no ORM e template rendering |

### Compatibilidade de dependências

Com Django 6.0.4, as versões mínimas foram ajustadas:

```
django-channels >= 4.1          (suporta async melhorado)
djangorestframework >= 3.15     (compatibilidade com Django 6.0)
mozilla-django-oidc >= 4.0      (mantém compatibilidade)
```

### Checklist de migração

1. ✅ Atualizar `pyproject.toml` com `django = ">=6.0,<7.0"`
2. ✅ Executar `uv sync` para sincronizar dependências
3. ⚠️ Revisar views assíncronas em `chat/consumers.py` (ChatConsumer)
4. ⚠️ Testar autenticação OIDC com novo middleware de segurança
5. ⚠️ Executar `pytest` para validar suite de testes
6. ✅ Atualizar settings se houver deprecações (verificar DEPRECATION_WARNINGS)

### Recomendações

- **Async views**: Considerar migrar views pesadas para async para melhor throughput
- **Signals**: Django 6.0 otimizou o sistema de signals; revisar se há oportunidades
- **Database**: Aproveitar novas query optimizations no ORM
- **Testing**: DRF 3.15+ traz melhorias em factories de teste
