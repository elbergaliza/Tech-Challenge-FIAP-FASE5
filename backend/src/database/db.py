# Engine, sessao e Base do SQLAlchemy.

import bootstrap  # noqa: F401  (precisa vir antes de qualquer import nosso)
import config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_is_sqlite = config.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    config.DATABASE_URL,
    # SQLite recusa uma conexao usada por outra thread. O FastAPI roda rota
    # `def` (sincrona) num threadpool, entao isso acontece o tempo todo.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    echo=False,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        # WAL e foreign_keys, que o SQLite nao liga sozinho.
        #
        # Sem `foreign_keys=ON` o banco aceita agendamento apontando para lead
        # inexistente e o ON DELETE CASCADE nao roda: o SQLite valida FK so
        # quando mandado, por compatibilidade historica.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    # Dependencia do FastAPI: uma sessao por request, sempre fechada.
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def criar_tabelas():
    # `create_all` em vez de Alembic: e um POC de hackathon.
    #
    # Se o modelo mudar depois de o banco existir, apague backend/data/app.db e
    # suba de novo. Migracao de verdade nao paga o tempo aqui.
    import models  # noqa: F401  (registra todo mundo no metadata)

    Base.metadata.create_all(bind=engine)
