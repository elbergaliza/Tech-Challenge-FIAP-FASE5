# Popula a tabela de imoveis a partir de `shared/data/imoveis.json`.
#
#     python backend/src/database/seed_imoveis.py            # base inteira (140)
#     python backend/src/database/seed_imoveis.py --limit 25 # so os 25 primeiros
#     python backend/src/database/seed_imoveis.py --reset    # apaga e reimporta
#
# Por que importar o JSON em vez de gerar imovel fake aqui
# -------------------------------------------------------
# O RAG da Pessoa 2 indexa AQUELE arquivo. Se este script inventasse os proprios
# imoveis, o agente recomendaria "IMV-0007, 3 quartos em Ipanema" e o
# `GET /imoveis/IMV-0007` do dashboard devolveria outro imovel, ou 404. Numa demo
# ao vivo isso e o tipo de furo que ninguem consegue explicar no ar.
#
# A base ja vem com 140 imoveis sinteticos e validados contra
# `shared/schemas/imovel_schema.json`. Se precisar de outra, regenere na fonte:
#
#     python ai-memory-rag/scripts/generate_properties.py

import argparse
import json
import os
import sys
from datetime import date

# Prelude de caminho: este arquivo tambem roda como script solto, e ai o Python
# poe `backend/src/database` no sys.path, nao `backend/src`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402,F401
import config  # noqa: E402
from database.db import SessionLocal, criar_tabelas  # noqa: E402
from models.imovel import Imovel  # noqa: E402


def carregar_json(path=None):
    path = path or config.PROPERTIES_JSON

    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Base de imoveis nao encontrada em %s.\n"
            "Rode: python ai-memory-rag/scripts/generate_properties.py" % path
        )

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    return data["properties"] if isinstance(data, dict) else data


def _data(valor):
    # `updated_at` vem como "2026-08-22"; o SQLite quer um date.
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def para_modelo(prop):
    return Imovel(
        id=prop["id"],
        title=prop["title"],
        description=prop["description"],
        deal_type=prop["deal_type"],
        property_type=prop["property_type"],
        price=prop["price"],
        condo_fee=prop.get("condo_fee"),
        property_tax=prop.get("property_tax"),
        bedrooms=prop["bedrooms"],
        suites=prop.get("suites"),
        bathrooms=prop["bathrooms"],
        parking=prop["parking"],
        area_m2=prop["area_m2"],
        neighborhood=prop["neighborhood"],
        zone=prop["zone"],
        city=prop["city"],
        lat=prop["lat"],
        lon=prop["lon"],
        features_csv="|".join(prop.get("features") or []),
        accepts_financing=prop.get("accepts_financing"),
        annual_yield_pct=prop.get("annual_yield_pct"),
        status=prop.get("status", "AVAILABLE"),
        updated_at=_data(prop.get("updated_at")),
    )


def seed(limit=0, reset=False, silencioso=False):
    # Idempotente: roda quantas vezes quiser, so insere o que falta.
    #
    # Chamado tambem no startup do app, entao NAO pode apagar nada sem `reset` e
    # nao pode explodir por imovel ja existente.
    criar_tabelas()
    propriedades = carregar_json()

    if limit and limit > 0:
        propriedades = propriedades[:limit]

    with SessionLocal() as db:
        if reset:
            apagados = db.query(Imovel).delete()
            db.commit()
            if not silencioso:
                print("[seed] %d imoveis removidos." % apagados)

        existentes = {linha[0] for linha in db.query(Imovel.id).all()}
        novos = [para_modelo(p) for p in propriedades if p["id"] not in existentes]

        if novos:
            db.add_all(novos)
            db.commit()

        total = db.query(Imovel).count()

    if not silencioso:
        print("[seed] %d inseridos, %d ja existiam. Total na base: %d."
              % (len(novos), len(propriedades) - len(novos), total))

    return len(novos), total


def main():
    parser = argparse.ArgumentParser(description="Popula a base de imoveis.")
    parser.add_argument("--limit", type=int, default=0,
                        help="importa so os N primeiros (0 = todos)")
    parser.add_argument("--reset", action="store_true",
                        help="apaga a tabela antes de importar")
    args = parser.parse_args()

    seed(limit=args.limit, reset=args.reset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
