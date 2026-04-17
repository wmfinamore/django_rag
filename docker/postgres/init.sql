-- =============================================================================
-- Inicialização do PostgreSQL para o projeto Django RAG
-- Executado automaticamente na primeira vez que o container sobe
-- =============================================================================

-- Banco do Keycloak (separado do banco da aplicação)
CREATE DATABASE keycloak;

-- Habilita pgvector no banco da aplicação (django_rag)
\c django_rag
CREATE EXTENSION IF NOT EXISTS vector;

-- Habilita pgvector no banco do Keycloak (necessário para algumas versões)
\c keycloak
-- (sem extensões necessárias por padrão)
