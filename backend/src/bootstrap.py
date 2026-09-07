# Coloca as outras partes do projeto no caminho de import, e carrega o .env.
#
# Tem que ser o PRIMEIRO import de qualquer entrypoint do backend, antes de
# `config`, de `services` ou de qualquer coisa que toque no ai-core.
#
# Por que existe
# --------------
# O ai-core e o ai-memory-rag nao sao pacotes instalaveis: sao pastas `src/` com
# imports planos (`import lead_profile`, `from llm import get_client`). O
# `run_chat.py` da Pessoa 2 resolve isso do mesmo jeito, com `sys.path.insert`.
# Importar como modulo em vez de falar HTTP com dois microsservicos e a razao de
# o backend ser Python: nao ha serializacao, nao ha porta, nao ha um terceiro
# processo para subir na hora da demo.
#
# Ordem do sys.path importa
# -------------------------
# `backend/src` entra na frente. O `ai-core/src` tem um `schemas.py` e um
# `prompts/`, nomes genericos o suficiente para colidir com um pacote nosso no
# futuro; os nossos ganham. E por isso tambem que o pacote de contratos da API
# aqui se chama `dto` e nao `schemas`.
#
# O .env e lido da RAIZ DO REPOSITORIO, nao do diretorio de onde o processo foi
# chamado. O `agent.py` da Pessoa 1 le `os.getenv("GEMINI_API_KEY")` no momento
# do import e levanta ValueError se nao achar, entao a variavel precisa ja estar
# no ambiente antes de qualquer import dele.

import os
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SRC_DIR)
REPO_ROOT = os.path.dirname(BACKEND_DIR)

AI_CORE_SRC = os.path.join(REPO_ROOT, "ai-core", "src")
AI_MEMORY_SRC = os.path.join(REPO_ROOT, "ai-memory-rag", "src")

_ready = False


def setup():
    # Idempotente: pode ser chamado por todo entrypoint sem medo.
    global _ready
    if _ready:
        return

    for path in (AI_MEMORY_SRC, AI_CORE_SRC, SRC_DIR):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    _load_env(os.path.join(REPO_ROOT, ".env"))
    _ready = True


def _load_env(path):
    # Le um .env simples sem depender do python-dotenv estar instalado.
    #
    # Nao sobrescreve o que ja esta no ambiente, que e o comportamento do dotenv
    # e o que faz sentido em CI e no Docker.
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value


setup()
