# Entrypoint do backend.
#
#     python backend/run.py                 # http://localhost:8000
#     python backend/run.py --port 8080 --no-reload
#
# Existe para nao depender de onde o terminal esta. Os modulos do backend usam
# import plano (`from database.db import ...`) para conviver com o ai-core e o
# ai-memory-rag, que fazem o mesmo; isso exige `backend/src` no sys.path, e este
# arquivo garante isso antes de qualquer import.

import argparse
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC)

import bootstrap  # noqa: E402,F401


def main():
    parser = argparse.ArgumentParser(description="Sobe a API.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true",
                        help="desliga o autoreload (use em producao)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        # O reloader sobe um processo filho que NAO herda o nosso sys.path.
        # Sem `app_dir` ele nao acharia `main` e morreria com ImportError so
        # depois da primeira edicao de arquivo, que e o pior momento possivel.
        app_dir=SRC,
        reload_dirs=[SRC] if not args.no_reload else None,
    )


if __name__ == "__main__":
    main()
