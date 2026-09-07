# Agendamento de visita ou reuniao.

import enum
from datetime import datetime, timezone

from database.db import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship


class TipoAgendamento(str, enum.Enum):
    VISITA = "VISITA"
    REUNIAO = "REUNIAO"
    # O fluxo de INVESTIMENTO da Pessoa 1 termina em "conversa com especialista",
    # que nao e visita a imovel nenhum.
    CONSULTORIA = "CONSULTORIA"


class StatusAgendamento(str, enum.Enum):
    AGENDADO = "AGENDADO"
    REALIZADO = "REALIZADO"
    CANCELADO = "CANCELADO"
    NAO_COMPARECEU = "NAO_COMPARECEU"


class Agendamento(Base):
    __tablename__ = "agendamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True,
    )
    # Nulo de proposito: reuniao com especialista de investimento nao tem imovel,
    # e o lead pode agendar antes de escolher qual visitar. SET NULL para que
    # apagar um imovel do catalogo nao apague a visita da agenda do corretor.
    imovel_id: Mapped[str | None] = mapped_column(
        ForeignKey("imoveis.id", ondelete="SET NULL"), index=True,
    )

    tipo: Mapped[str] = mapped_column(String(20), default=TipoAgendamento.VISITA.value)
    status: Mapped[str] = mapped_column(String(20),
                                        default=StatusAgendamento.AGENDADO.value,
                                        index=True)

    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    corretor: Mapped[str | None] = mapped_column(String(120))
    observacoes: Mapped[str | None] = mapped_column(Text)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )

    lead: Mapped["Lead"] = relationship(back_populates="agendamentos")  # noqa: F821
    imovel: Mapped["Imovel | None"] = relationship()  # noqa: F821

    def __repr__(self):
        return "<Agendamento %s %s %s>" % (self.lead_id, self.data_hora, self.status)
