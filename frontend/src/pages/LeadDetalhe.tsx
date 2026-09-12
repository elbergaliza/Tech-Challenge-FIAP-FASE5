// Detalhe do lead: a tela em que o corretor decide o que fazer com ele.
//
// O card de resumo da IA vem primeiro, e dentro dele `next_action`, `alerts` e
// `buying_signals` ganham destaque: são as três coisas que mudam a próxima
// ligação. O histórico completo fica abaixo, para quem quiser conferir.

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { BadgeAgendamento, BadgeSimples, BadgeStatus, BadgeTemperatura } from "../components/Badges";
import { Botao, Campo, Card, Carregando, Erro, Vazio, classesInput } from "../components/Ui";
import { useApi } from "../hooks/useApi";
import { dataHora, hora, paraIso, silencio } from "../lib/formatar";
import { ApiError, api } from "../services/api";
import type { LeadDetalhe as Lead, ResumoIA, StatusLead, TipoAgendamento } from "../types";

const STATUS: { valor: StatusLead; texto: string }[] = [
  { valor: "NOVO", texto: "Novo" },
  { valor: "EM_ANDAMENTO", texto: "Em andamento" },
  { valor: "QUALIFICADO", texto: "Qualificado" },
  { valor: "AGENDADO", texto: "Agendado" },
  { valor: "DESCARTADO", texto: "Descartado" },
];

export default function LeadDetalhe() {
  const { leadId = "" } = useParams();
  const navegar = useNavigate();

  // `comIa` liga o `?resumo_ia=true`, que regera o resumo com LLM e gasta
  // cota. Começa desligado: o resumo heurístico já vem pronto e não custa.
  const [comIa, setComIa] = useState(false);
  const { dados, carregando, erro, recarregar } = useApi(
    () => api.detalheLead(leadId, comIa),
    [leadId, comIa],
  );

  const [salvando, setSalvando] = useState(false);
  const [erroAcao, setErroAcao] = useState<string | null>(null);

  async function mudarStatus(status: StatusLead) {
    setSalvando(true);
    setErroAcao(null);
    try {
      // PATCH parcial: mandar só o status não apaga telefone nem e-mail.
      await api.atualizarLead(leadId, { status });
      recarregar();
    } catch (falha: unknown) {
      setErroAcao(falha instanceof ApiError ? falha.message : "Não foi possível salvar.");
    } finally {
      setSalvando(false);
    }
  }

  async function apagar() {
    const ok = window.confirm(
      "Apagar o lead e a memória da IA sobre ele? A ação não tem volta.",
    );
    if (!ok) return;

    setSalvando(true);
    try {
      await api.apagarLead(leadId);
      navegar("/painel");
    } catch (falha: unknown) {
      setErroAcao(falha instanceof ApiError ? falha.message : "Não foi possível apagar.");
      setSalvando(false);
    }
  }

  async function exportar() {
    // Sem `try`, uma falha aqui virava unhandled rejection: o botao piscava, o
    // arquivo nao baixava, e a unica pista ficava no console do navegador.
    setErroAcao(null);
    try {
      const conteudo = await api.exportarLead(leadId);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(conteudo, null, 2)], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `${leadId}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (falha) {
      setErroAcao(
        falha instanceof ApiError
          ? falha.message
          : "Nao foi possivel exportar os dados deste lead.",
      );
    }
  }

  if (erro) return <Erro mensagem={erro} aoTentarNovamente={recarregar} />;
  if (carregando && !dados) return <Carregando texto="Carregando o lead..." />;
  if (!dados) return null;

  return (
    <div className="space-y-4">
      <Link
        to="/painel"
        className="alvo-de-toque inline-block text-sm text-suave underline-offset-2 hover:underline"
      >
        ← Voltar ao painel
      </Link>

      <Cabecalho lead={dados} />

      {erroAcao && <Erro mensagem={erroAcao} />}

      <div className="flex flex-wrap items-center gap-2 rounded-cartao border border-linha bg-superficie px-4 py-3 shadow-cartao">
        <span className="text-xs font-medium text-suave">Mover para</span>
        {STATUS.map((opcao) => (
          <button
            key={opcao.valor}
            type="button"
            disabled={salvando || dados.status === opcao.valor}
            onClick={() => mudarStatus(opcao.valor)}
            className={`rounded-full px-2.5 py-1 text-xs font-medium transition disabled:opacity-40 ${
              dados.status === opcao.valor
                ? "bg-acento text-acento-tinta"
                : "bg-superficie-2 text-suave hover:text-texto"
            }`}
          >
            {opcao.texto}
          </button>
        ))}

        <div className="ml-auto flex gap-2">
          <Botao variante="secundario" onClick={exportar} className="text-xs">
            Exportar JSON
          </Botao>
          <Botao variante="perigo" onClick={apagar} disabled={salvando} className="text-xs">
            Apagar (LGPD)
          </Botao>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <CardResumoIA
            resumo={dados.resumo_ia}
            comIa={comIa}
            carregando={carregando}
            aoPedirIa={() => setComIa(true)}
          />
          <Historico lead={dados} />
        </div>

        <div className="space-y-4">
          <CardPerfil lead={dados} />
          <CardAgendamentos lead={dados} aoMudar={recarregar} />
        </div>
      </div>
    </div>
  );
}

function Cabecalho({ lead }: { lead: Lead }) {
  const contatos = [lead.telefone, lead.email].filter(Boolean);

  return (
    <div className="rounded-cartao border border-linha bg-superficie px-4 py-4 shadow-cartao">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold">
            {lead.nome ?? "Lead sem nome"}
          </h1>
          <p className="mt-0.5 text-sm text-suave">
            {contatos.length > 0 ? contatos.join(" · ") : "sem contato informado"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <BadgeTemperatura
            temperatura={lead.temperatura}
            label={lead.temperatura_label}
            score={lead.score}
          />
          <BadgeStatus status={lead.status} label={lead.status_label} />
          {!lead.consentimento && (
            <BadgeSimples cor="bg-morno/10 text-morno">sem consentimento</BadgeSimples>
          )}
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-linha pt-3 text-xs text-suave sm:grid-cols-4">
        <Info rotulo="Origem" valor={lead.origem} />
        <Info rotulo="Primeiro contato" valor={dataHora(lead.criado_em)} />
        <Info rotulo="Última mensagem" valor={dataHora(lead.ultima_mensagem_em)} />
        <Info
          rotulo="Silêncio"
          valor={`${silencio(lead.horas_sem_resposta)}${
            lead.followups_enviados > 0 ? ` · ${lead.followups_enviados} follow-up(s)` : ""
          }`}
        />
      </dl>
    </div>
  );
}

function Info({ rotulo, valor }: { rotulo: string; valor: string }) {
  return (
    <div>
      <dt className="text-suave/70">{rotulo}</dt>
      <dd className="font-medium text-texto">{valor}</dd>
    </div>
  );
}

function CardResumoIA({
  resumo,
  comIa,
  carregando,
  aoPedirIa,
}: {
  resumo: ResumoIA | null;
  comIa: boolean;
  carregando: boolean;
  aoPedirIa: () => void;
}) {
  return (
    <Card
      titulo="Resumo para o corretor"
      acao={
        comIa ? (
          <BadgeSimples cor="bg-acento/15 text-acento">
            {/* O badge lê `source` em vez do clique: se a cota do LLM estourar,
                o backend devolve o heurístico, e dizer "gerado com IA" aí
                seria mentira na tela. */}
            {carregando
              ? "gerando com IA..."
              : resumo?.source && resumo.source !== "heuristic"
                ? "gerado com IA"
                : "heurístico (IA indisponível)"}
          </BadgeSimples>
        ) : (
          <Botao
            variante="secundario"
            onClick={aoPedirIa}
            className="text-xs"
            title="Regera o texto com LLM. Gasta cota."
          >
            Melhorar com IA
          </Botao>
        )
      }
    >
      {!resumo ? (
        <Vazio
          texto="Sem resumo ainda"
          detalhe="O resumo é gerado a partir da conversa: o lead precisa ter trocado pelo menos uma mensagem com o agente."
        />
      ) : (
        <div className="space-y-3">
          {resumo.next_action && (
            <p className="rounded-suave border border-sucesso/40 bg-sucesso/10 px-3 py-2 text-sm font-medium text-sucesso">
              <span className="mr-1" aria-hidden>
                →
              </span>
              {resumo.next_action}
            </p>
          )}

          {resumo.summary && (
            <p className="max-w-[70ch] text-sm leading-relaxed">{resumo.summary}</p>
          )}

          {resumo.alerts && resumo.alerts.length > 0 && (
            <ListaMarcada
              titulo="Alertas"
              itens={resumo.alerts}
              cor="border-morno/40 bg-morno/10 text-morno"
              marcador="!"
            />
          )}

          {resumo.buying_signals && resumo.buying_signals.length > 0 && (
            <ListaMarcada
              titulo="Sinais de compra"
              itens={resumo.buying_signals}
              cor="border-frio/40 bg-frio/10 text-frio"
              marcador="✓"
            />
          )}

          {resumo.objections && resumo.objections.length > 0 && (
            <ListaMarcada
              titulo="Objeções"
              itens={resumo.objections}
              cor="border-linha bg-superficie-2 text-texto"
              marcador="•"
            />
          )}

          {resumo.shown_properties && resumo.shown_properties.length > 0 && (
            <p className="text-xs text-suave">
              Imóveis já mostrados: {resumo.shown_properties.join(", ")}
            </p>
          )}

          {/* De onde saiu o score. Sem isso o número é mágico, e um corretor
              não confia num número mágico para decidir quem ligar. */}
          {resumo.factors && resumo.factors.length > 0 && (
            <details className="text-xs text-suave">
              <summary className="cursor-pointer font-medium text-suave">
                Como o score {resumo.score ?? ""} foi montado
              </summary>
              <ul className="mt-1 space-y-0.5 pl-4">
                {resumo.factors.map((fator) => (
                  <li key={fator} className="list-disc">
                    {fator}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}

function ListaMarcada({
  titulo,
  itens,
  cor,
  marcador,
}: {
  titulo: string;
  itens: string[];
  cor: string;
  marcador: string;
}) {
  return (
    <div className={`rounded-suave border px-3 py-2 ${cor}`}>
      <p className="text-xs font-semibold uppercase tracking-wide opacity-70">{titulo}</p>
      <ul className="mt-1 space-y-0.5 text-sm">
        {itens.map((item) => (
          <li key={item} className="flex gap-2">
            <span aria-hidden className="opacity-60">
              {marcador}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CardPerfil({ lead }: { lead: Lead }) {
  // Ticket e retorno esperado vêm do perfil da memória, não das colunas do
  // lead: são campos que só existem para quem investe, e criar coluna no
  // banco para eles exigiria migração que um POC não precisa pagar.
  const perfil = lead.resumo_ia?.profile ?? {};
  const investidor = lead.intencao === "INVEST";

  const linhas = [
    ["Intenção", lead.intencao_label],
    ...(investidor
      ? ([
          ["Ticket", perfil.investor_ticket ?? null],
          ["Retorno esperado", perfil.expected_return ?? null],
        ] as const)
      : ([["Quartos", lead.quartos]] as const)),
    ["Região", lead.regiao],
    ["Orçamento", lead.faixa_preco_label ?? lead.faixa_preco],
    ["Urgência", lead.urgencia_label],
    ["Próxima ação", lead.proxima_acao],
  ] as const;

  return (
    <Card titulo="Perfil qualificado">
      <dl className="divide-y divide-linha text-sm">
        {linhas.map(([rotulo, valor]) => (
          <div key={rotulo} className="flex justify-between gap-3 py-1.5">
            <dt className="shrink-0 text-suave">{rotulo}</dt>
            <dd className="text-right font-medium text-texto">{valor ?? "—"}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function Historico({ lead }: { lead: Lead }) {
  return (
    <Card titulo={`Conversa (${lead.mensagens.length} mensagens)`}>
      {lead.mensagens.length === 0 ? (
        <Vazio texto="Nenhuma mensagem trocada" detalhe="Este lead foi criado sem passar pelo chat." />
      ) : (
        <ul className="max-h-[32rem] space-y-2 overflow-y-auto">
          {lead.mensagens.map((m) => {
            const doLead = m.papel === "user";
            const followup = m.papel === "followup";
            return (
              <li key={m.id} className={`flex ${doLead ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[62ch] rounded-cartao px-3 py-2 text-sm whitespace-pre-wrap ${
                    doLead
                      ? "bg-acento text-acento-tinta"
                      : followup
                        ? "border border-dashed border-morno/40 bg-morno/10 text-morno"
                        : "border border-linha bg-superficie text-texto"
                  }`}
                >
                  {followup && (
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-morno">
                      Retomada automática
                    </p>
                  )}
                  {m.conteudo}
                  <p
                    className={`mt-1 text-xs ${doLead ? "text-acento-tinta/70" : "text-suave/70"}`}
                  >
                    {hora(m.criado_em)}
                    {m.imoveis.length > 0 && ` · ${m.imoveis.length} imóvel(is) enviados`}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function CardAgendamentos({ lead, aoMudar }: { lead: Lead; aoMudar: () => void }) {
  const [aberto, setAberto] = useState(false);
  const [quando, setQuando] = useState("");
  // Investimento termina em conversa com especialista, não em visita a imóvel.
  const [tipo, setTipo] = useState<TipoAgendamento>(
    lead.intencao === "INVEST" ? "CONSULTORIA" : "VISITA",
  );
  const [imovelId, setImovelId] = useState("");
  const [corretor, setCorretor] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function salvar(evento: React.FormEvent) {
    evento.preventDefault();
    setSalvando(true);
    setErro(null);
    try {
      await api.agendar(lead.id, {
        data_hora: paraIso(quando),
        tipo,
        imovel_id: imovelId.trim() || null,
        corretor: corretor.trim() || null,
      });
      setAberto(false);
      setQuando("");
      setImovelId("");
      aoMudar();
    } catch (falha: unknown) {
      setErro(falha instanceof ApiError ? falha.message : "Não foi possível agendar.");
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card
      titulo="Agendamentos"
      acao={
        <Botao variante="secundario" onClick={() => setAberto((a) => !a)} className="text-xs">
          {aberto ? "Fechar" : "Novo agendamento"}
        </Botao>
      }
    >
      {aberto && (
        <form onSubmit={salvar} className="mb-3 space-y-2 rounded-suave bg-superficie-2 p-3">
          <Campo rotulo="Data e hora">
            <input
              type="datetime-local"
              value={quando}
              onChange={(e) => setQuando(e.target.value)}
              className={classesInput}
              required
            />
          </Campo>

          <Campo rotulo="Tipo">
            <select
              value={tipo}
              onChange={(e) => setTipo(e.target.value as TipoAgendamento)}
              className={classesInput}
            >
              <option value="VISITA">Visita</option>
              <option value="REUNIAO">Reunião</option>
              <option value="CONSULTORIA">Consultoria</option>
            </select>
          </Campo>

          <Campo rotulo="Imóvel (opcional)">
            <input
              value={imovelId}
              onChange={(e) => setImovelId(e.target.value)}
              placeholder="IMV-0086"
              className={classesInput}
            />
          </Campo>

          <Campo rotulo="Corretor">
            <input
              value={corretor}
              onChange={(e) => setCorretor(e.target.value)}
              placeholder="Seu nome"
              className={classesInput}
            />
          </Campo>

          {erro && <Erro mensagem={erro} />}

          <Botao type="submit" disabled={salvando || !quando} className="w-full text-xs">
            {salvando ? "Salvando..." : "Agendar"}
          </Botao>
        </form>
      )}

      {lead.agendamentos.length === 0 ? (
        <Vazio texto="Nada marcado" detalhe="Use o botão Novo para marcar uma visita, reunião ou consultoria." />
      ) : (
        <ul className="space-y-2">
          {lead.agendamentos.map((a) => (
            <li key={a.id} className="rounded-suave border border-linha p-2.5">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-texto">{dataHora(a.data_hora)}</p>
                <BadgeAgendamento status={a.status} />
              </div>
              <p className="mt-0.5 text-xs text-suave">
                {a.tipo}
                {a.imovel_titulo ? ` · ${a.imovel_titulo}` : ""}
                {a.corretor ? ` · ${a.corretor}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
