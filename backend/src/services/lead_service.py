# Regras de lead: criacao, sincronizacao com o perfil da IA e montagem do dto.

from datetime import datetime, timezone

import services.ai_service as ai_service
from dto.schemas import (
    FIELD_LABELS, INTENT_LABELS, STATUS_LABELS, TEMPERATURE_LABELS,
    URGENCY_LABELS,
    LeadResumo,
)
from models.conversa import Mensagem
from models.lead import Lead, StatusLead, Temperatura
import uuid

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.exc import IntegrityError

# Campo do perfil da Parte 2 -> coluna nossa. Os valores ja vem no dialeto
# canonico (BUY/RENT/INVEST, high/medium/low) porque o `lead_profile.from_agent`
# traduz na borda; aqui e copia, nao conversao.
PERFIL_PARA_COLUNA = {
    "name": "nome",
    "email": "email",
    "phone": "telefone",
    "intent": "intencao",
    "region": "regiao",
    "price_range": "faixa_preco",
    "bedrooms": "quartos",
    "urgency": "urgencia",
}


def novo_id(db=None) -> str:
    """Id de lead imprevisivel, no formato "lead-a3f1c8d902b45e17".

    ERA sequencial ("lead-0001", "lead-0002"), legivel de proposito, e isso
    transformava as rotas de LGPD num oraculo sobre a base inteira: nao ha
    login, o id vem do proprio cliente, e trocar um digito na URL dava acesso a
    EXPORTAR (`GET /leads/{id}/exportar`, que devolve nome, telefone, e-mail e
    a conversa inteira) e a APAGAR (`DELETE /leads/{id}`) os dados de qualquer
    outra pessoa. Adivinhar "lead-0003" nao e ataque: e digitacao.

    Com 16 digitos hexadecimais o espaco e grande demais para chute, e o
    formato continua respeitando o `^[A-Za-z0-9_-]{1,64}$` que a memoria da
    Parte 2 exige. O prefixo "lead-" fica: e ele que torna o id reconhecivel
    num log e numa tela de suporte.

    Os ids antigos continuam validos: nada aqui invalida "lead-0001", e a demo
    nao precisa de migracao de banco.

    `db` deixou de ser usado, e o parametro fica por compatibilidade com quem
    ja chama `novo_id(db)`. Sem consulta tambem nao ha mais a corrida entre
    dois chats simultaneos que o retry do `criar` cobria.
    """
    return "lead-%s" % uuid.uuid4().hex[:16]


def criar(db, dados=None, lead_id=None, origem="chat") -> Lead:
    # Cria o lead no banco e registra o consentimento na memoria da IA.
    dados = dados or {}

    for tentativa in range(5):
        lead = Lead(
            id=lead_id or novo_id(db),
            nome=dados.get("nome"),
            email=dados.get("email"),
            telefone=dados.get("telefone"),
            intencao=_valor(dados.get("intencao")),
            regiao=dados.get("regiao"),
            faixa_preco=dados.get("faixa_preco"),
            quartos=dados.get("quartos"),
            urgencia=_valor(dados.get("urgencia")),
            status=StatusLead.NOVO.value,
            temperatura=Temperatura.COLD.value,
            origem=dados.get("origem", origem),
            consentimento=bool(dados.get("consentimento", False)),
        )
        db.add(lead)

        try:
            db.commit()
            break
        except IntegrityError:
            # Outro request pegou este id entre o count e o insert. Tenta o
            # proximo; nao insiste para sempre para nao virar loop infinito se
            # o erro for outro.
            db.rollback()
            if lead_id or tentativa == 4:
                raise
    else:  # pragma: no cover
        raise RuntimeError("nao consegui gerar um lead_id livre")

    if lead.consentimento:
        ai_service.registrar_consentimento(lead.id, True)

    return lead


def obter(db, lead_id: str) -> Lead | None:
    return db.get(Lead, lead_id)


def obter_ou_criar(db, lead_id: str | None) -> Lead:
    if lead_id:
        lead = db.get(Lead, lead_id)
        if lead:
            return lead
        # lead_id vindo do front que nao existe mais no banco (banco apagado,
        # localStorage antigo): recria com o MESMO id em vez de 404. A conversa
        # do usuario nao deveria morrer por causa disso.
        return criar(db, lead_id=lead_id)

    return criar(db)


def atualizar(db, lead: Lead, alteracoes: dict) -> Lead:
    # PATCH. So mexe no que veio no corpo.
    for campo, valor in alteracoes.items():
        if hasattr(lead, campo):
            setattr(lead, campo, _valor(valor))

    db.commit()
    return lead


def sincronizar_do_perfil(db, lead: Lead, perfil: dict, card: dict | None) -> Lead:
    # Copia o perfil da memoria da IA para as colunas do lead.
    #
    # A memoria e a fonte de verdade do PERFIL: ela e monotonica, sabe distinguir
    # correcao de novidade e nao esquece o que saiu da janela de contexto. As
    # colunas aqui existem para o dashboard filtrar e ordenar em SQL, o que seria
    # impossivel com o perfil dentro de um blob JSON.
    #
    # Campo que o corretor editou a mao NAO e sobrescrito por valor vazio: a
    # condicao e "a memoria sabe algo", nunca "a memoria nao sabe, entao apague".
    for campo_perfil, coluna in PERFIL_PARA_COLUNA.items():
        valor = perfil.get(campo_perfil)
        if valor not in (None, "", "undefined"):
            setattr(lead, coluna, str(valor))

    if card:
        lead.score = int(card.get("score") or 0)
        lead.temperatura = card.get("temperature") or Temperatura.COLD.value
        lead.proxima_acao = card.get("next_action")

    # Status so avanca automaticamente ate QUALIFICADO. AGENDADO e DESCARTADO
    # sao decisao humana (ou da rota de agendamento) e a IA nao os desfaz: um
    # lead que agendou visita nao pode voltar a "em andamento" porque mandou
    # mais uma mensagem.
    if lead.status not in (StatusLead.AGENDADO.value, StatusLead.DESCARTADO.value):
        if lead.temperatura == Temperatura.HOT.value or _tem_contato_e_intencao(lead):
            lead.status = StatusLead.QUALIFICADO.value
        else:
            lead.status = StatusLead.EM_ANDAMENTO.value

    db.commit()
    return lead


def _tem_contato_e_intencao(lead: Lead) -> bool:
    # Qualificado = da para o corretor ligar E ele sabe do que falar.
    #
    # Contato pesa tanto quanto intencao porque, para um SDR, contato e o produto
    # final: um lead perfeitamente qualificado sem telefone e um lead que ninguem
    # consegue atender. E o mesmo criterio do `SCORE_WEIGHTS` da Parte 2.
    return bool(lead.intencao) and bool(lead.telefone or lead.email)


def _preco_legivel(valor: str) -> str:
    # "8.55k" -> "R$ 8.550". A memoria normaliza faixa de preco nesse formato
    # compacto, que serve para comparar e nao para ler: ninguem diz que o
    # orcamento e "8.55k".
    texto = str(valor).strip()
    if not texto.lower().endswith("k"):
        return texto

    try:
        numero = float(texto[:-1].replace(",", ".")) * 1000
    except ValueError:
        return texto

    # `format(..., ",d")` separa milhar com virgula (padrao ingles); a troca
    # por ponto e o que faz virar 8.550 e nao 8,550.
    return "R$ " + format(int(round(numero)), ",d").replace(",", ".")


def valor_legivel(campo: str, valor) -> str:
    # Um valor do perfil como ele deve aparecer na tela.
    #
    # Nao concatena o nome do campo ("3 quartos"): quem escreve o rotulo e a
    # tela, e o painel ja tem a coluna "Quartos" do lado. Repetir ali sairia
    # "Quartos: 3 quartos".
    texto = str(valor)

    if campo == "intent":
        return INTENT_LABELS.get(texto, texto)
    if campo == "urgency":
        return URGENCY_LABELS.get(texto, texto)
    if campo == "price_range":
        return _preco_legivel(texto)

    return texto


# Como cada campo do perfil da IA aparece na tela. O valor cru continua no
# `perfil`, para quem precisa comparar; isto e so a versao de LER.
#
# Mora aqui, e nao no front, pela mesma razao dos outros rotulos: as tabelas de
# traducao ja sao daqui, e uma segunda copia do outro lado sai de sincronia na
# primeira mudanca. Isso ja tinha acontecido: o front mantinha a propria lista
# de nomes de campo, e ela ja divergia da FIELD_LABELS ("orcamento" contra
# "Faixa de preco").
def rotulos_do_perfil(perfil: dict) -> dict:
    if not perfil:
        return {}

    return {
        campo: valor_legivel(campo, valor)
        for campo, valor in perfil.items()
        if valor not in (None, "", "undefined")
    }


def nomes_dos_campos(perfil: dict) -> dict:
    # O nome de exibicao de cada campo presente no perfil, da FIELD_LABELS da
    # Parte 2, que e a dona dessa tabela.
    if not perfil:
        return {}

    return {
        campo: FIELD_LABELS.get(campo, campo.replace("_", " "))
        for campo, valor in perfil.items()
        if valor not in (None, "", "undefined")
    }


def rotular_novidades(novidades: list) -> list:
    # Cada novidade ganha nome de campo e valores prontos para ler, sem perder
    # os campos crus: o chip mostra "anotei: Quartos = 3", e quem precisa
    # comparar continua com `field`, `from` e `to`.
    rotuladas = []

    for item in novidades or []:
        item = dict(item)
        campo = item.get("field", "")

        item["field_label"] = FIELD_LABELS.get(campo, campo.replace("_", " "))
        for lado in ("from", "to"):
            valor = item.get(lado)
            item["%s_label" % lado] = (
                valor_legivel(campo, valor) if valor not in (None, "", "undefined") else None
            )

        rotuladas.append(item)

    return rotuladas


def para_dto(db, lead: Lead, com_contagens=True) -> LeadResumo:
    # Modelo -> dto, preenchendo rotulos e contagens.
    dto = LeadResumo.model_validate(lead)

    dto.intencao_label = INTENT_LABELS.get(lead.intencao or "")
    dto.urgencia_label = URGENCY_LABELS.get(lead.urgencia or "")
    dto.faixa_preco_label = (
        _preco_legivel(lead.faixa_preco) if lead.faixa_preco else None
    )
    dto.temperatura_label = TEMPERATURE_LABELS.get(lead.temperatura, lead.temperatura)
    dto.status_label = STATUS_LABELS.get(lead.status, lead.status)

    if com_contagens:
        dto.total_mensagens = db.scalar(
            select(func.count()).select_from(Mensagem).where(Mensagem.lead_id == lead.id)
        ) or 0

    dto.horas_sem_resposta = _horas_desde(lead.ultima_mensagem_em)
    return dto


def _horas_desde(momento: datetime | None) -> float | None:
    if momento is None:
        return None

    # SQLite devolve datetime ingenuo mesmo com `DateTime(timezone=True)`: ele
    # nao tem tipo de data nativo e nao guarda offset. Assumir UTC e correto
    # porque e sempre em UTC que gravamos.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - momento
    return round(delta.total_seconds() / 3600.0, 1)


def _valor(v):
    # Enum do pydantic -> str da coluna.
    return v.value if hasattr(v, "value") else v
