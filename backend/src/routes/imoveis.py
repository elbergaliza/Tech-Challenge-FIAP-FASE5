# Catalogo de imoveis.
#
# CRUD de leitura mais uma busca por filtros em SQL. Nao confundir com o RAG: aqui
# e "filtro exato que o corretor escolheu na tela"; la e "o que combina com o que
# este lead disse", com relaxamento de criterio e ranking semantico. As duas
# respondem sobre a mesma base e servem a perguntas diferentes.

from database.db import get_db
from dto.schemas import ImoveisPagina, ImovelOut
from fastapi import APIRouter, Depends, HTTPException, Query
from models.imovel import Imovel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/imoveis", tags=["imoveis"])


def _para_dto(imovel: Imovel) -> ImovelOut:
    return ImovelOut.model_validate(imovel).model_copy(
        update={"features": imovel.features},
    )


@router.get("", response_model=ImoveisPagina, summary="Listar/filtrar imoveis")
def listar(
    db: Session = Depends(get_db),
    deal_type: str | None = Query(None, description="SALE ou RENTAL"),
    property_type: str | None = None,
    neighborhood: str | None = None,
    zone: str | None = None,
    bedrooms_min: int | None = Query(None, ge=0),
    preco_min: float | None = Query(None, ge=0),
    preco_max: float | None = Query(None, ge=0),
    status: str = Query("AVAILABLE", description="AVAILABLE, RESERVED, SOLD ou 'todos'"),
    limite: int = Query(24, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = db.query(Imovel)

    if status and status != "todos":
        query = query.filter(Imovel.status == status)
    if deal_type:
        query = query.filter(Imovel.deal_type == deal_type)
    if property_type:
        query = query.filter(Imovel.property_type == property_type)
    if neighborhood:
        query = query.filter(Imovel.neighborhood == neighborhood)
    if zone:
        query = query.filter(Imovel.zone == zone)
    if bedrooms_min is not None:
        query = query.filter(Imovel.bedrooms >= bedrooms_min)
    if preco_min is not None:
        query = query.filter(Imovel.price >= preco_min)
    if preco_max is not None:
        query = query.filter(Imovel.price <= preco_max)

    # Total ANTES de paginar: o front precisa dele para desenhar a paginacao.
    total = query.with_entities(func.count()).scalar() or 0
    itens = query.order_by(Imovel.price.asc()).offset(offset).limit(limite).all()

    return ImoveisPagina(total=total, limit=limite, offset=offset,
                         items=[_para_dto(i) for i in itens])


@router.get("/filtros", summary="Valores disponiveis para os filtros")
def filtros(db: Session = Depends(get_db)):
    # Evita o front chumbar a lista de bairros num array no codigo.
    def distintos(coluna):
        return [v for (v,) in db.query(coluna).distinct().order_by(coluna).all() if v]

    faixa = db.execute(select(func.min(Imovel.price), func.max(Imovel.price))).first()

    return {
        "deal_type": distintos(Imovel.deal_type),
        "property_type": distintos(Imovel.property_type),
        "neighborhood": distintos(Imovel.neighborhood),
        "zone": distintos(Imovel.zone),
        "bedrooms": distintos(Imovel.bedrooms),
        "preco_min": faixa[0] if faixa else 0,
        "preco_max": faixa[1] if faixa else 0,
    }


@router.get("/{imovel_id}", response_model=ImovelOut, summary="Detalhe do imovel")
def detalhe(imovel_id: str, db: Session = Depends(get_db)):
    imovel = db.get(Imovel, imovel_id)
    if not imovel:
        raise HTTPException(404, "Imovel nao encontrado.")

    return _para_dto(imovel)
