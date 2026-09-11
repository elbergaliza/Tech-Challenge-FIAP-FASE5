// Tela 2: o painel do corretor.
//
// A pergunta que ele faz ao abrir a tela é "quem eu ligo agora", não "quem
// chegou por último". Por isso a lista vem ordenada por score desc (o padrão do
// backend) e os leads quentes e os follow-ups pendentes ficam acima da dobra.

import { useState } from "react";
import { Link } from "react-router-dom";

import { BadgeSimples, BadgeStatus } from "../components/Badges";
import { Barras, Botao, Card, Carregando, Erro, Kpi, Vazio, classesInput } from "../components/Ui";
import { useApi } from "../hooks/useApi";
import { useDebounce } from "../hooks/useDebounce";
import { numero, silencio } from "../lib/formatar";
import { ApiError, api } from "../services/api";
import type { FollowUpPendente, LeadResumo } from "../types";

type Ordenacao = "score" | "criado_em" | "ultima_mensagem_em";

export default function Dashboard() {
  const resumo = useApi(() => api.resumoDashboard(), []);

  const [temperatura, setTemperatura] = useState("");
  const [status, setStatus] = useState("");
  const [intencao, setIntencao] = useState("");
  const [busca, setBusca] = useState("");
  const [ordenarPor, setOrdenarPor] = useState<Ordenacao>("score");

  // `busca` entra na dependência já com debounce simples: sem isso, cada tecla
  // digitada viraria uma requisição.
  const buscaAtrasada = useDebounce(busca, 350);

  const leads = useApi(
    () =>
      api.listarLeads({
        temperatura,
        status,
        intencao,
        busca: buscaAtrasada,
        ordenar_por: ordenarPor,
        limite: 100,
      }),
    [temperatura, status, intencao, buscaAtrasada, ordenarPor],
  );

  return (
    <div className="space-y-6">
      {resumo.erro && <Erro mensagem={resumo.erro} aoTentarNovamente={resumo.recarregar} />}

      {/* Os KPIs dependem de /dashboard/summary; a lista de leads e os
          follow-ups são outras rotas. Com tudo aninhado dentro de
          `resumo.dados &&`, uma falha SÓ no resumo apagava a tela inteira,
          inclusive as partes que estavam respondendo bem. Agora cada bloco cai
          sozinho. */}
      {resumo.carregando && !resumo.dados ? (
        <Carregando texto="Carregando o painel..." />
      ) : (
        resumo.dados && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <Kpi rotulo="Leads" valor={resumo.dados.total_leads} />
              <Kpi
                rotulo="Quentes"
                valor={resumo.dados.leads_quentes}
                cor="text-quente"
                detalhe="ligar hoje"
              />
              <Kpi rotulo="Mornos" valor={resumo.dados.leads_mornos} cor="text-morno" />
              <Kpi rotulo="Frios" valor={resumo.dados.leads_frios} cor="text-frio" />
              <Kpi
                rotulo="Qualificação"
                valor={`${numero(resumo.dados.taxa_qualificacao)}%`}
                detalhe={`${resumo.dados.qualificados} qualificados`}
              />
              <Kpi
                rotulo="Agenda"
                valor={resumo.dados.agendamentos_proximos}
                detalhe={`${resumo.dados.agendamentos_hoje} hoje`}
              />
          </div>
        )
      )}

      {/* Os gráficos vêm do mesmo /dashboard/summary dos KPIs, então caem
          junto com eles, e só com eles. */}
      {resumo.dados && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card titulo="Por status">
            <Barras itens={resumo.dados.por_status} cor="bg-suave" />
          </Card>
          <Card titulo="Por intenção">
            <Barras itens={resumo.dados.por_intencao} cor="bg-frio" />
          </Card>
          <Card titulo="Regiões mais pedidas">
            <Barras itens={resumo.dados.por_regiao} cor="bg-sucesso" />
          </Card>
        </div>
      )}

      {/* A lista de leads e os follow-ups têm rotas próprias e estado de erro
          próprio: eles aparecem mesmo que o resumo acima tenha falhado, que é
          o caso em que o corretor MAIS precisa da lista. */}
      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
              <Card
                titulo={
                  <span className="flex items-center gap-2">
                    Leads
                    {leads.dados && (
                      <BadgeSimples>{leads.dados.length} na lista</BadgeSimples>
                    )}
                  </span>
                }
                acao={
                  <select
                    value={ordenarPor}
                    onChange={(e) => setOrdenarPor(e.target.value as Ordenacao)}
                    aria-label="Ordenar os leads"
                    className="rounded-suave border border-linha bg-superficie px-2 py-1 text-xs"
                  >
                    <option value="score">Maior score</option>
                    <option value="ultima_mensagem_em">Última mensagem</option>
                    <option value="criado_em">Mais recentes</option>
                  </select>
                }
              >
                <div className="mb-3 grid gap-2 sm:grid-cols-4">
                  <input
                    value={busca}
                    onChange={(e) => setBusca(e.target.value)}
                    aria-label="Buscar lead por nome, e-mail ou telefone"
                    placeholder="Nome, e-mail ou telefone"
                    className={`${classesInput} sm:col-span-2`}
                  />
                  <select
                    value={temperatura}
                    onChange={(e) => setTemperatura(e.target.value)}
                    aria-label="Filtrar por temperatura"
                    className={classesInput}
                  >
                    <option value="">Toda temperatura</option>
                    <option value="HOT">Quentes</option>
                    <option value="WARM">Mornos</option>
                    <option value="COLD">Frios</option>
                  </select>
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                    aria-label="Filtrar por status"
                    className={classesInput}
                  >
                    <option value="">Todo status</option>
                    <option value="NOVO">Novo</option>
                    <option value="EM_ANDAMENTO">Em andamento</option>
                    <option value="QUALIFICADO">Qualificado</option>
                    <option value="AGENDADO">Agendado</option>
                    <option value="DESCARTADO">Descartado</option>
                  </select>
                </div>

                <div className="mb-3 flex flex-wrap gap-1.5">
                  {[
                    { valor: "", texto: "Todas as intenções" },
                    { valor: "BUY", texto: "Compra" },
                    { valor: "RENT", texto: "Aluguel" },
                    { valor: "INVEST", texto: "Investimento" },
                  ].map((opcao) => (
                    <button
                      key={opcao.valor}
                      type="button"
                      onClick={() => setIntencao(opcao.valor)}
                      className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                        intencao === opcao.valor
                          ? "bg-acento text-acento-tinta"
                          : "bg-superficie-2 text-suave hover:text-texto"
                      }`}
                    >
                      {opcao.texto}
                    </button>
                  ))}
                </div>

                {leads.erro ? (
                  <Erro mensagem={leads.erro} aoTentarNovamente={leads.recarregar} />
                ) : leads.carregando && !leads.dados ? (
                  <Carregando />
                ) : leads.dados && leads.dados.length > 0 ? (
                  <TabelaLeads leads={leads.dados} />
                ) : (
                  <Vazio
                    texto={
                      busca || temperatura || status || intencao
                        ? "Nenhum lead com esses filtros"
                        : "Nenhum lead ainda"
                    }
                    detalhe={
                      busca || temperatura || status || intencao
                        ? "Limpe a busca ou tire um filtro para ver a lista inteira."
                        : "Assim que alguém mandar a primeira mensagem no chat, o lead aparece aqui, ordenado por score."
                    }
                  />
                )}
              </Card>

        <PainelFollowups />
      </div>
    </div>
  );
}

// Linha de 44px com a faixa de temperatura na borda esquerda, em vez de uma
// tabela de seis colunas com badge em cada célula. O corretor varre trinta
// leads pela cor da borda e só lê a linha em que vai parar; a temperatura,
// que é a informação mais forte da lista, não gasta uma coluna por isso.
const COLUNAS = "grid-cols-[minmax(10rem,1.5fr)_minmax(8rem,1.2fr)_5rem_6.5rem_3.5rem]";

function TabelaLeads({ leads }: { leads: LeadResumo[] }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <div className="min-w-[40rem]">
        <div
          className={`grid ${COLUNAS} items-center gap-3 border-b border-linha pb-2 pl-3 text-xs font-semibold uppercase tracking-wide text-suave`}
        >
          <span>Lead</span>
          <span>Interesse</span>
          <span>Urgência</span>
          <span>Status</span>
          <span className="text-right">Silêncio</span>
        </div>

        <ul>
          {leads.map((lead) => (
            <li key={lead.id} className="faixa" data-t={lead.temperatura}>
              <Link
                to={`/painel/leads/${lead.id}`}
                title={`${lead.temperatura_label ?? lead.temperatura} · score ${lead.score}`}
                className={`grid ${COLUNAS} h-11 items-center gap-3 border-b border-linha pl-3 pr-1 text-sm hover:bg-superficie-2`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-semibold">
                    {lead.nome ?? "(sem nome)"}
                  </span>
                  <span className="block truncate text-xs text-suave">
                    {lead.telefone ?? lead.email ?? lead.id} · {lead.total_mensagens} msg
                  </span>
                </span>

                <span className="truncate text-xs text-suave">
                  {[
                    lead.intencao_label,
                    lead.regiao,
                    lead.quartos ? `${lead.quartos}q` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </span>

                <span className="truncate text-xs text-suave">
                  {lead.urgencia_label ?? "—"}
                </span>

                <span className="flex items-center gap-1.5">
                  {/* O score fica ao lado do status porque é ele que ordena a
                      lista: esconder o número deixaria a ordem inexplicável. */}
                  <span className="w-6 text-right text-xs font-semibold tabular-nums text-texto">
                    {lead.score}
                  </span>
                  <BadgeStatus status={lead.status} label={lead.status_label} />
                </span>

                <span className="text-right text-xs tabular-nums text-suave">
                  {silencio(lead.horas_sem_resposta)}
                </span>
              </Link>
            </li>
          ))}
        </ul>

        <p className="pt-2 pl-3 text-xs text-suave">
          A cor na borda é a temperatura: quente, morno, frio.
        </p>
      </div>
    </div>
  );
}

// "Precisam de atenção": quem parou de responder, do mais silencioso ao menos.
function PainelFollowups() {
  const { dados, carregando, erro, recarregar } = useApi(() => api.followups(), []);
  const [abertos, setAbertos] = useState<Record<string, string>>({});
  const [gerando, setGerando] = useState<string | null>(null);

  // `?com_texto=true` gasta uma chamada de LLM POR LEAD. Chamado só quando o
  // corretor abre um item, e o resultado fica em cache local para ele não
  // pagar duas vezes pelo mesmo texto.
  async function gerarTexto(item: FollowUpPendente) {
    if (abertos[item.lead_id]) {
      setAbertos(({ [item.lead_id]: _removido, ...resto }) => resto);
      return;
    }

    setGerando(item.lead_id);
    try {
      const comTexto = await api.followups(true);
      const encontrado = comTexto.find((f) => f.lead_id === item.lead_id);
      setAbertos((atuais) => ({
        ...atuais,
        [item.lead_id]: encontrado?.texto_sugerido ?? "Sem sugestão para este lead.",
      }));
    } catch (falha) {
      // Este é o caminho que gasta LLM por lead, ou seja, o mais provável de
      // falhar dos três: cota estourada e 503 caem exatamente aqui. Sem
      // `catch`, o botão piscava e nada aparecia.
      setAbertos((atuais) => ({
        ...atuais,
        [item.lead_id]:
          falha instanceof ApiError
            ? `Não foi possível gerar o texto: ${falha.message}`
            : "Não foi possível gerar o texto agora.",
      }));
    } finally {
      setGerando(null);
    }
  }

  return (
    <Card
      titulo="Precisam de atenção"
      acao={
        <Botao variante="secundario" onClick={recarregar} className="text-xs">
          Atualizar
        </Botao>
      }
    >
      {erro ? (
        <Erro mensagem={erro} aoTentarNovamente={recarregar} />
      ) : carregando && !dados ? (
        <Carregando />
      ) : dados && dados.length > 0 ? (
        <ul className="space-y-2">
          {dados.map((item) => (
            <li
              key={item.lead_id}
              className="rounded-suave border border-linha p-3 hover:border-acento/50"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <Link
                    to={`/painel/leads/${item.lead_id}`}
                    className="alvo-de-toque text-sm font-medium text-texto underline-offset-2 hover:underline"
                  >
                    {item.lead_nome ?? item.lead_id}
                  </Link>
                  <p className="mt-0.5 text-xs text-suave">{item.motivo}</p>
                </div>
                <BadgeSimples cor="bg-morno/10 text-morno">
                  {silencio(item.horas_de_silencio)} calado
                </BadgeSimples>
              </div>

              <div className="mt-2 flex items-center gap-2">
                <span className="text-xs text-suave/70">
                  tentativa {item.tentativa}
                  {item.tom ? ` · tom ${item.tom}` : ""}
                </span>
                <button
                  type="button"
                  onClick={() => gerarTexto(item)}
                  disabled={gerando === item.lead_id}
                  className="ml-auto text-xs font-medium text-suave underline-offset-2 hover:underline disabled:text-suave/70"
                >
                  {gerando === item.lead_id
                    ? "gerando..."
                    : abertos[item.lead_id]
                      ? "esconder sugestão"
                      : "ver texto sugerido"}
                </button>
              </div>

              {abertos[item.lead_id] && (
                <p className="mt-2 rounded-suave bg-superficie-2 px-3 py-2 text-xs leading-relaxed text-texto">
                  {abertos[item.lead_id]}
                </p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Vazio
          texto="Ninguém esperando resposta"
          detalhe="Todo lead com conversa aberta já foi respondido. O job de retomada roda a cada 30 minutos e avisa aqui."
        />
      )}
    </Card>
  );
}
