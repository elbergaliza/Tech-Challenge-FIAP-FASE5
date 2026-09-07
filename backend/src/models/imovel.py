# A base de imoveis.
#
# As colunas espelham `shared/schemas/imovel_schema.json`, que e o contrato que
# o `generate_properties.py` produz e o RAG indexa. Nomes em ingles aqui, e nao
# traduzidos, porque o `retriever.py` filtra por `deal_type`, `bedrooms`,
# `neighborhood` etc.: traduzir criaria um mapa de-para so para o backend
# desfazer na hora de casar com o RAG.
#
# Quem manda no catalogo continua sendo o JSON: o RAG indexa o arquivo, nao esta
# tabela. O seed importa o arquivo para ca para que a API tenha CRUD e o
# dashboard tenha o que mostrar. Se editar imovel pelo banco, rode o reindex.

from datetime import datetime, timezone

from database.db import Base
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class Imovel(Base):
    __tablename__ = "imoveis"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # "IMV-0001"

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)

    deal_type: Mapped[str] = mapped_column(String(10), index=True)      # SALE | RENTAL
    property_type: Mapped[str] = mapped_column(String(20), index=True)

    price: Mapped[float] = mapped_column(Float, index=True)
    condo_fee: Mapped[float | None] = mapped_column(Float)
    property_tax: Mapped[float | None] = mapped_column(Float)

    bedrooms: Mapped[int] = mapped_column(Integer, index=True)
    suites: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int] = mapped_column(Integer)
    parking: Mapped[int] = mapped_column(Integer)
    area_m2: Mapped[float] = mapped_column(Float)

    neighborhood: Mapped[str] = mapped_column(String(60), index=True)
    zone: Mapped[str] = mapped_column(String(40), index=True)
    city: Mapped[str] = mapped_column(String(60))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)

    # Lista de strings virou CSV em vez de JSON: no SQLite os dois sao TEXT, e
    # CSV deixa `LIKE '%piscina%'` funcionar sem extensao nenhuma.
    features_csv: Mapped[str | None] = mapped_column(Text)

    accepts_financing: Mapped[bool | None] = mapped_column(Boolean)
    annual_yield_pct: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE", index=True)
    updated_at: Mapped[datetime | None] = mapped_column(Date)

    importado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )

    @property
    def features(self) -> list[str]:
        return [f for f in (self.features_csv or "").split("|") if f]

    def __repr__(self):
        return "<Imovel %s %s R$%.0f>" % (self.id, self.neighborhood, self.price)
