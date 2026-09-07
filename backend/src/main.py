# API do Agente SDR Imobiliario — Parte 3.
#
#     python backend/run.py               # com reload
#     uvicorn main:app --app-dir backend/src
#
# Docs interativas em http://localhost:8000/docs

import bootstrap  # noqa: F401  isto TEM que ser o primeiro import

import config
from contextlib import asynccontextmanager

from database.db import criar_tabelas
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routes import chat, dashboard, imoveis, leads, scheduling


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Boot: tabelas, seed, job. Nada aqui pode demorar nem falhar.
    #
    # O indice do RAG NAO e construido aqui de proposito: com o embedder do
    # Gemini ele leva dezenas de segundos, e um healthcheck que expira antes
    # disso mataria o container antes de a API existir. Quem paga esse custo e a
    # primeira mensagem de chat (ver `ai_service._get_index`).
    criar_tabelas()

    if config.SEED_ON_STARTUP:
        try:
            from database.seed_imoveis import seed
            seed(limit=config.SEED_LIMIT, silencioso=True)
        except Exception as erro:
            # Base de imoveis ausente nao pode impedir a API de subir: o chat e
            # os leads funcionam sem catalogo, so o RAG fica mudo.
            print("[seed] Ignorado: %s" % erro)

    from jobs import followup_scheduler
    followup_scheduler.iniciar()

    # Sem a porta na mensagem: quem a escolhe e o uvicorn, e chutar 8000 aqui
    # produz um link errado justamente para quem subiu em outra porta.
    print("[api] Pronta. Docs em /docs")
    yield

    followup_scheduler.parar()


app = FastAPI(
    title="Agente SDR Imobiliario — API",
    description=(
        "Backend do Tech Challenge FASE 5. Orquestra o agente de conversa "
        "(ai-core) e a memoria/RAG (ai-memory-rag), que sao importados como "
        "modulo, nao chamados por HTTP."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(leads.router)
app.include_router(imoveis.router)
app.include_router(scheduling.router)
app.include_router(dashboard.router)


@app.get("/", tags=["meta"], summary="Ping")
def raiz():
    return {"api": "agente-sdr-imobiliario", "versao": "1.0.0", "docs": "/docs"}


@app.get("/health", tags=["meta"], summary="Estado do backend e da IA")
def health():
    # Diz o que esta ligado DE VERDADE, nao o que deveria estar.
    #
    # Numa demo com chave de API no meio, "o agente esta em mock" e a primeira
    # coisa que alguem precisa conseguir descobrir sem ler log.
    import services.ai_service as ai_service
    from database.db import engine
    from sqlalchemy import text

    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
        banco = "ok"
    except Exception as erro:
        banco = "erro: %s" % erro

    return JSONResponse({
        "status": "ok" if banco == "ok" else "degradado",
        "banco": banco,
        "database_url": config.DATABASE_URL.split("///")[-1],
        "ia": ai_service.status(),
    })
