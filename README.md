# Django RAG

Chat com LLM e RAG sobre base de conhecimento institucional e documentos pessoais.

**Stack principal:** Django 6 · PostgreSQL 16 + pgvector · Redis 7 · Keycloak 24 · Ollama · Celery · sentence-transformers · Presidio

---

## Pré-requisitos

| Ferramenta | Versão mínima | Como instalar |
|---|---|---|
| Python | 3.12 | [python.org](https://www.python.org/downloads/) |
| uv | qualquer | `pip install uv` ou [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| Docker + Compose | Docker 24+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Ollama | qualquer | [ollama.com](https://ollama.com/download) |
| Git | qualquer | — |

---

## Configuração do ambiente de desenvolvimento

### 1. Clonar o repositório

```bash
git clone <url-do-repositorio>
cd django_rag
```

### 2. Baixar o bootstrap estático

```bash
python docker/download_bootstrap.py
```

Esse script baixa os arquivos CSS/JS do Bootstrap para `static/`.

### 3. Criar o arquivo `.env`

```bash
cp .env.example .env
```

O `.env.example` já contém os valores corretos para desenvolvimento local. Os únicos campos que precisam ser preenchidos depois são os de Keycloak (`OIDC_RP_CLIENT_SECRET`), que serão gerados no passo 6.

> **Portas não-padrão:** o docker-compose-infra.yml expõe PostgreSQL na **15432** e Redis na **6380** para evitar conflitos com serviços locais. O `.env.example` já usa essas portas.

### 4. Subir a infraestrutura (PostgreSQL, Redis, Keycloak)

```bash
docker compose -f docker-compose-infra.yml up -d
```

Aguarde o Keycloak ficar saudável (pode demorar ~60 segundos na primeira vez):

```bash
docker compose -f docker-compose-infra.yml ps
# keycloak deve aparecer como "healthy"
```

Interfaces disponíveis após o `up`:

| Serviço | URL |
|---|---|
| Keycloak Admin Console | http://localhost:8081 (admin / admin) |
| Redis Commander (UI) | http://localhost:8082 |

### 5. Instalar as dependências Python

```bash
uv sync --group dev
```

Isso cria o ambiente virtual em `.venv` e instala todas as dependências de produção e desenvolvimento.

### 6. Configurar o Keycloak automaticamente

```bash
uv run python docker/keycloak_setup.py
```

O script cria via Admin REST API:
- Realm `django-rag`
- Client `django_cli` (confidential, Authorization Code)
- Mappers de grupos e nome no token
- Grupos: `admin`, `editor`, `viewer`
- Usuário de teste: `testuser` / `Test@1234` (grupos: admin, viewer)

Ao final, o script imprime o `Client Secret` gerado. **Copie esse valor** e cole no `.env`:

```bash
# .env
OIDC_RP_CLIENT_SECRET=<valor-impresso-pelo-script>
```

> Se precisar reconfigurar (ex.: após recriar os containers), basta rodar o script novamente — ele detecta o que já existe e atualiza apenas o necessário.

### 7. Baixar o modelo Ollama

```bash
ollama pull llama3.2:3b
```

O Ollama deve estar rodando antes de puxar o modelo. No Windows, o Ollama fica em execução como serviço após a instalação.

Verifique se está ativo:

```bash
ollama list
# deve listar llama3.2:3b
```

### 8. Baixar o modelo spaCy (Presidio / filtro de privacidade)

```bash
uv run python -m spacy download pt_core_news_lg
```

Necessário para o filtro de PII/LGPD em português.

### 9. Aplicar as migrations

```bash
uv run python manage.py migrate
```

Isso cria todas as tabelas no PostgreSQL, incluindo a extensão `pgvector` e as tabelas da app `knowledge`.

### 10. Criar um superusuário local (opcional)

Para acessar o Django Admin sem passar pelo Keycloak:

```bash
uv run python manage.py createsuperuser
```

### 11. Iniciar o servidor Django

```bash
uv run python manage.py runserver
```

Acesse: http://localhost:8000

A raiz `/` redireciona para `/rag/`. O login é feito via Keycloak em `/rag/oidc/authenticate/`.

---

## Rodar o Celery (worker de tasks)

Em um terminal separado:

```bash
uv run celery -A config worker -l info
```

O Celery é necessário para indexação assíncrona de documentos (tasks `index_document`, `delete_document`, `reindex_document`).

> **Windows:** o pool `prefork` padrão não é suportado. O `development.py` já configura `CELERY_WORKER_POOL = "solo"` automaticamente — nenhuma flag adicional é necessária.

---

## Estrutura de URLs

| URL | Descrição |
|---|---|
| `/rag/` | Home |
| `/rag/admin/` | Django Admin |
| `/rag/oidc/authenticate/` | Inicia login via Keycloak |
| `/rag/oidc/callback/` | Callback OIDC pós-login |
| `/rag/oidc/logout/` | Logout federado (Keycloak + Django) |
| `/rag/accounts/profile/` | Perfil do usuário autenticado |
| `/rag/api/knowledge/collections/` | API REST — coleções de conhecimento |
| `/rag/api/knowledge/collections/<id>/documents/` | API REST — documentos de uma coleção |
| `/rag/api/knowledge/documents/<id>/` | API REST — detalhe / deleção de documento |
| `/rag/api/knowledge/documents/<id>/reindex/` | API REST — re-indexação de documento |
| `/rag/__debug__/` | Django Debug Toolbar (só com DEBUG=True) |

---

## API de Conhecimento

A app `knowledge` expõe uma API REST para gerenciar a base de conhecimento institucional.

### Coleções

```bash
# Listar coleções acessíveis ao usuário autenticado
GET /rag/api/knowledge/collections/

# Criar nova coleção (requer is_staff)
POST /rag/api/knowledge/collections/
{"name": "Políticas de RH", "description": "..."}

# Detalhe de uma coleção
GET /rag/api/knowledge/collections/<uuid>/

# Listar documentos da coleção (filtro opcional: ?status=ready)
GET /rag/api/knowledge/collections/<uuid>/documents/

# Upload de documento para a coleção (requer is_staff, multipart/form-data)
POST /rag/api/knowledge/collections/<uuid>/documents/
  title=<string>  file=<arquivo pdf|docx|txt|md>
```

### Documentos

```bash
# Detalhe do documento (status de indexação, chunks_count)
GET /rag/api/knowledge/documents/<uuid>/

# Remover documento e chunks (requer is_staff)
# A deleção é assíncrona via Celery — retorna 202 Accepted
DELETE /rag/api/knowledge/documents/<uuid>/

# Re-indexar documento (requer is_staff/admin)
# Retorna 202 Accepted com task_id
POST /rag/api/knowledge/documents/<uuid>/reindex/
```

### Controle de acesso

| Operação | Permissão necessária |
|---|---|
| Listar / ler coleções e documentos | Autenticado + pertencer ao grupo da coleção (ou coleção pública) |
| Criar coleções | `is_staff = True` |
| Upload de documentos | `is_staff = True` |
| Deletar documentos | `is_staff = True` |
| Re-indexar documentos | `is_staff = True` e `is_superuser = True` |

Coleções sem grupos em `allowed_groups` são **públicas** (qualquer autenticado acessa). Superusuários acessam tudo independentemente dos grupos.

---

## Testes

```bash
# Todos os testes
uv run pytest

# Apenas a app knowledge (84 testes)
uv run pytest apps/knowledge/tests.py -v

# Pular testes lentos (carregam modelos de ML)
uv run pytest -m "not slow"

# Com relatório de cobertura
uv run pytest --cov=apps
```

---

## Linting e formatação

```bash
# Verificar problemas
uv run ruff check .

# Corrigir automaticamente
uv run ruff check . --fix

# Verificar tipos (mypy)
uv run mypy apps/
```

---

## Variáveis de ambiente

Todas as variáveis estão documentadas no `.env.example`. Os valores de desenvolvimento já estão preenchidos, com exceção de `OIDC_RP_CLIENT_SECRET` (gerado no passo 6).

Variáveis que mais frequentemente precisam ser ajustadas:

| Variável | Default | Quando mudar |
|---|---|---|
| `OIDC_RP_CLIENT_SECRET` | — | Sempre (gerado pelo keycloak_setup.py) |
| `OLLAMA_NUM_THREAD` | 4 | Ajustar ao número de CPUs da máquina |
| `OLLAMA_LLM_MODEL` | `llama3.2:3b` | Para usar outro modelo Ollama |
| `RAG_TOP_K` | 4 | Para alterar o número de chunks no prompt |
| `PRIVACY_MIN_SCORE` | 0.7 | Para ajustar sensibilidade do filtro PII |

---

## Apps habilitados vs. planejados

| App | Status | Responsabilidade |
|---|---|---|
| `apps.core` | ✅ habilitado | RAGService, reranker, privacy_filter, ragas_eval, tasks Celery |
| `apps.accounts` | ✅ habilitado | CustomUser, OIDC backend, grupos |
| `apps.knowledge` | ✅ habilitado | Coleções, documentos institucionais, ingestão, API REST, 84 testes |
| `apps.documents` | 🔜 planejado | Documentos pessoais do usuário |
| `apps.chat` | 🔜 planejado | Conversas, WebSocket streaming |

---

## Arquitetura detalhada

Consulte `django_rag_arquitetura_2.md` para documentação técnica completa: modelos de dados, pipeline RAG, autenticação OIDC, tasks Celery, filtro de privacidade e avaliação com Ragas.
