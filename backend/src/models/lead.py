# O lead: a entidade central do sistema.
#
# Sobre os valores dos enums
# --------------------------
# Sao os mesmos do `lead_profile.py` da Pessoa 2 (BUY/RENT/INVEST,
# high/medium/low), e nao os da Pessoa 1 (COMPRA/ALUGUEL/INVESTIMENTO,
# alta/media/baixa), de proposito. O `lead_profile` e declaradamente "o unico
# lugar onde chaves em portugues podem existir": ele traduz o dialeto da Pessoa 1
# uma vez, na borda. Guardar o dialeto ja traduzido significa que o backend nunca
# precisa de uma segunda tabela de traducao, e que sincronizar perfil -> banco e
# copia direta.
#
# Quem quiser portugues na tela usa `INTENT_LABELS` / `URGENCY_LABELS`, que o dto
# ja expoe junto com o valor cru.
#
# Sobre a temperatura e o score
# -----------------------------
# Nao sao calculados aqui. Quem calcula e o `summarizer.compute_score()` da
# Pessoa 2, que enxerga coisas que a tabela nao tem: horas de silencio, numero de
# mensagens, campos esquivados. Estas colunas sao um CACHE do ultimo calculo,
# atualizado a cada turno de chat, para o dashboard listar e ordenar 200 leads
# sem rodar o summarizer 200 vezes.

import enum
from datetime import datetime, timezone

from database.db import Base
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Intencao(str, enum.Enum):
    BUY = "BUY"
    RENT = "RENT"
    INVEST = "INVEST"


class Urgencia(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StatusLead(str, enum.Enum):
    NOVO = "NOVO"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    QUALIFICADO = "QUALIFICADO"
    AGENDADO = "AGENDADO"
    DESCARTADO = "DESCARTADO"


class Temperatura(str, enum.Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


def agora():
    return datetime.now(timezone.utc)


class Lead(Base):
    __tablename__ = "leads"

    # String, e nao inteiro autoincremental, porque este mesmo id e a chave da
    # memoria da Pessoa 2, que o valida contra ^[A-Za-z0-9_-]{1,64}$ (ele viraria
    # nome de arquivo no JsonFileStore). Ids sao gerados como "lead-0001" pelo
    # lead_service.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    nome: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(180), index=True)
    telefone: Mapped[str | None] = mapped_column(String(40), index=True)

    intencao: Mapped[str | None] = mapped_column(String(16), index=True)
    regiao: Mapped[str | None] = mapped_column(String(80), index=True)
    faixa_preco: Mapped[str | None] = mapped_column(String(60))
    quartos: Mapped[str | None] = mapped_column(String(10))
    urgencia: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[str] = mapped_column(String(20), default=StatusLead.NOVO.value,
                                        index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    temperatura: Mapped[str] = mapped_column(String(10), default=Temperatura.COLD.value,
                                             index=True)

    # Ultimo texto de "proxima acao" sugerido pelo summarizer. Cache tambem:
    # o corretor le isso na lista sem abrir o lead.
    proxima_acao: Mapped[str | None] = mapped_column(Text)

    origem: Mapped[str] = mapped_column(String(40), default="chat")
    consentimento: Mapped[bool] = mapped_column(default=False)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    default=agora, onupdate=agora)
    # So avanca quando o LEAD escreve. E a data que mede silencio: se o
    # follow-up do agente a atualizasse, mandar follow-up zeraria o proprio
    # contador que decide se cabe follow-up. A memoria da Pessoa 2 separa as
    # duas datas pelo mesmo motivo.
    ultima_mensagem_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                index=True)
    followups_enviados: Mapped[int] = mapped_column(Integer, default=0)

    mensagens: Mapped[list["Mensagem"]] = relationship(  # noqa: F821
        back_populates="lead", cascade="all, delete-orphan",
        order_by="Mensagem.criado_em",
    )
    agendamentos: Mapped[list["Agendamento"]] = relationship(  # noqa: F821
        back_populates="lead", cascade="all, delete-orphan",
        order_by="Agendamento.data_hora",
    )

    def __repr__(self):
        return "<Lead %s %s %s>" % (self.id, self.nome or "?", self.status)
