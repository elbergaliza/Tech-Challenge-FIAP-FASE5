# Configuracao lida do ambiente, num objeto so.

import os

import bootstrap

BACKEND_DIR = bootstrap.BACKEND_DIR
REPO_ROOT = bootstrap.REPO_ROOT

DATA_DIR = os.path.join(BACKEND_DIR, "data")


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on", "sim")


def _int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _database_url():
    # URL do banco, com caminho de SQLite sempre absoluto.
    #
    # `sqlite:///./data/app.db` e relativo ao diretorio de onde o processo subiu.
    # Rodar `uvicorn` da raiz do repo criaria um banco diferente do que o
    # `seed_imoveis.py` rodado de dentro de backend/ criou, e o bug apareceria
    # so como "cade meus imoveis". Resolver contra BACKEND_DIR mata isso.
    url = (os.getenv("DATABASE_URL") or "").strip()

    if not url:
        url = "sqlite:///" + os.path.join(DATA_DIR, "app.db")
    elif url.startswith("sqlite:///./") or url.startswith("sqlite:///.\\"):
        relative = url[len("sqlite:///"):]
        url = "sqlite:///" + os.path.abspath(os.path.join(BACKEND_DIR, relative))

    return url


DATABASE_URL = _database_url()

CORS_ORIGINS = [
    origin.strip()
    for origin in (os.getenv("CORS_ORIGINS") or "http://localhost:5173,http://localhost:3000").split(",")
    if origin.strip()
]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
HAS_LLM = bool(GEMINI_API_KEY)

SEED_ON_STARTUP = _bool("SEED_ON_STARTUP", True)
SEED_LIMIT = _int("SEED_LIMIT", 0)  # 0 = base inteira

FOLLOWUP_ENABLED = _bool("FOLLOWUP_ENABLED", True)
FOLLOWUP_INTERVAL_MINUTES = _int("FOLLOWUP_INTERVAL_MINUTES", 30)

# O job de follow-up escreve o texto com a HEURISTICA, nao com o LLM.
#
# Ligado por padrao e acordando a cada 30 minutos, ele gerava texto com IA para
# cada lead elegivel, em segundo plano, sem ninguem ver. A cota gratuita do
# Gemini e de 20 requisicoes por DIA e POR MODELO, e o job usa o MESMO modelo
# do chat: bastava o testador seguir o proprio roteiro e envelhecer alguns
# leads para o chat comecar a cair no mock sem nenhuma explicacao aparente na
# tela, porque a cota tinha ido embora num job invisivel.
#
# O texto heuristico ja existe, ja usa o perfil do lead e ja funciona. O texto
# com IA continua disponivel sob demanda, na rota que o corretor chama
# explicitamente (`/dashboard/followups?com_texto=true`), onde ele sabe que
# esta gastando.
FOLLOWUP_USA_LLM = _bool("FOLLOWUP_USA_LLM", False)

# Quantos turnos de conversa vao para o agente. Espelha o corte que a Pessoa 1
# ja faz em `historico[-10:]`.
HISTORY_WINDOW = _int("HISTORY_WINDOW", 10)

PROPERTIES_JSON = os.path.join(REPO_ROOT, "shared", "data", "imoveis.json")

os.makedirs(DATA_DIR, exist_ok=True)
