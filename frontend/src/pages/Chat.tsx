// Tela 1: o lead conversando com o agente.
//
// Uma rota só sustenta esta tela: POST /chat. A resposta traz tudo num payload
// (bolha, score, perfil, novidades, imóveis), então não há uma segunda chamada
// para "buscar o resto" depois de cada turno.

import { useEffect, useRef, useState } from "react";

import { BadgeSimples, BadgeTemperatura } from "../components/Badges";
import { ImovelSugeridoCard } from "../components/ImovelSugeridoCard";
import { Botao, Campo, Card, Erro, classesInput } from "../components/Ui";
import { minimoDoSeletor, paraIso } from "../lib/formatar";
import { ApiError, api } from "../services/api";
import type { ChatSaida, ImovelSugerido, Novidade } from "../types";

const CHAVE_LEAD = "lead_id";

interface Turno {
  chave: string;
  papel: "user" | "assistant" | "followup";
  conteudo: string;
  origem?: string | null;
  novidades?: Novidade[];
  imoveis?: ImovelSugerido[];
}

// Estado que o payload do chat vai atualizando a cada turno; alimenta o painel
// lateral do perfil.
interface EstadoLead {
  status: string;
  score: number;
  temperatura: ChatSaida["temperatura"];
  temperatura_label: string;
  perfil: Record<string, string | null>;
  perfil_label: Record<string, string>;
  perfil_campos: Record<string, string>;
  proxima_acao: string | null;
  sugerir_agendamento: boolean;
}

const SAUDACAO =
  "Oi! Sou o assistente virtual da imobiliária. Me conta o que você procura " +
  "que eu já vou separando algumas opções.";

export default function Chat() {
  const [leadId, setLeadId] = useState<string | null>(() =>
    localStorage.getItem(CHAVE_LEAD),
  );
  const [turnos, setTurnos] = useState<Turno[]>([]);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [consentimento, setConsentimento] = useState(false);
  const [estado, setEstado] = useState<EstadoLead | null>(null);
  const [abrirAgenda, setAbrirAgenda] = useState(false);

  // Quem disse "depois" não pode receber o formulário de volta a cada turno.
  const dispensouAgenda = useRef(false);
  // Contador de geração da conversa. Só serve para descartar a resposta de um
  // envio que ficou para trás quando o usuário clicou em "Nova conversa".
  const geracao = useRef(0);

  const fim = useRef<HTMLDivElement>(null);
  const primeiraMensagem = leadId === null;

  // O fluxo de quem investe termina em conversa com especialista, não em
  // visita a imóvel. Lido do valor CRU de propósito: é regra de negócio, não
  // texto de tela.
  const investidor = estado?.perfil?.intent === "INVEST";

  // Qual lead já tem a conversa desenhada na tela. Quem escreve aqui é o envio
  // de mensagem, e só ele: o efeito abaixo apenas LÊ.
  //
  // A distinção não é estilo. Marcar aqui dentro do efeito, antes do fetch,
  // quebrava o histórico em desenvolvimento: o `StrictMode` roda todo efeito
  // duas vezes (executa, limpa, executa), e a sequência virava "marca, busca,
  // a limpeza descarta a resposta, a segunda execução vê a marca e desiste".
  // O histórico nunca aparecia depois do F5.
  const conversaNaTela = useRef<string | null>(null);

  // Reabrir a conversa: o lead_id vive no localStorage, então recarregar a
  // página não perde o histórico nem cria um lead duplicado no funil.
  //
  // O histórico é para REABRIR, nunca para ecoar o que acabou de ser enviado.
  // Sem a guarda, o primeiro turno caía numa corrida: ele cria o lead, o
  // `leadId` muda, o efeito dispara e a resposta do servidor substituía os
  // turnos locais. Como `GET /chat/{id}/historico` devolve `MensagemOut`, que
  // não carrega `novidades` nem `imoveis`, o chip "anotei" e os cards do RAG
  // apareciam e sumiam um instante depois.
  useEffect(() => {
    if (!leadId || conversaNaTela.current === leadId) return;

    let vivo = true;
    api
      .historico(leadId)
      .then((mensagens) => {
        if (!vivo) return;
        setTurnos(
          mensagens.map((m) => ({
            chave: `hist-${m.id}`,
            papel: m.papel,
            conteudo: m.conteudo,
            origem: m.origem,
          })),
        );
      })
      .catch((falha: unknown) => {
        if (!vivo) return;
        // 404 aqui significa lead apagado (LGPD) ou banco recriado: o id
        // guardado no navegador não vale mais, e insistir nele quebraria todo
        // turno seguinte.
        if (falha instanceof ApiError && falha.status === 404) {
          localStorage.removeItem(CHAVE_LEAD);
          conversaNaTela.current = null;
          setLeadId(null);
          return;
        }
        setErro(
          falha instanceof ApiError ? falha.message : "Erro ao carregar a conversa.",
        );
      });

    return () => {
      vivo = false;
    };
  }, [leadId]);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turnos, enviando]);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();

    const mensagem = texto.trim();
    if (!mensagem || enviando) return;

    setTexto("");
    setErro(null);
    setEnviando(true);
    setTurnos((atuais) => [
      ...atuais,
      { chave: `eu-${Date.now()}`, papel: "user", conteudo: mensagem },
    ]);

    const minhaGeracao = geracao.current;

    try {
      const turno = await api.enviarMensagem({
        lead_id: leadId,
        mensagem,
        // LGPD: o aceite vai só na primeira mensagem. Sem consentimento a
        // conversa acontece, mas o follow-up automático não dispara.
        consentimento: primeiraMensagem ? consentimento : undefined,
      });

      // A conversa mudou embaixo desta resposta: gravar o `lead_id` que voltou
      // ressuscitaria o lead que o usuário acabou de descartar, e a bolha
      // apareceria numa tela que já foi limpa.
      if (minhaGeracao !== geracao.current) return;

      if (turno.lead_id !== leadId) {
        localStorage.setItem(CHAVE_LEAD, turno.lead_id);
        // Marca antes de trocar o estado: o efeito do histórico lê esta ref
        // e precisa saber que esta conversa já está desenhada.
        conversaNaTela.current = turno.lead_id;
        setLeadId(turno.lead_id);
      }

      setTurnos((atuais) => [
        ...atuais,
        {
          chave: `ia-${turno.lead_id}-${atuais.length}`,
          papel: "assistant",
          conteudo: turno.resposta,
          origem: turno.origem,
          novidades: turno.novidades,
          imoveis: turno.imoveis,
        },
      ]);

      // O seletor abre sozinho quando o perfil fica completo, porque é para
      // ele que o agente aponta: "é só escolher o dia e a hora aí embaixo".
      // Esconder atrás de mais um clique fazia o cliente procurar o que a
      // mensagem dizia estar na tela.
      if (turno.sugerir_agendamento && !dispensouAgenda.current) {
        setAbrirAgenda(true);
      }

      setEstado({
        status: turno.status,
        score: turno.score,
        temperatura: turno.temperatura,
        temperatura_label: turno.temperatura_label,
        perfil: turno.perfil,
        perfil_label: turno.perfil_label ?? {},
        perfil_campos: turno.perfil_campos ?? {},
        proxima_acao: turno.proxima_acao,
        sugerir_agendamento: turno.sugerir_agendamento,
      });
    } catch (falha: unknown) {
      if (minhaGeracao !== geracao.current) return;

      setErro(
        falha instanceof ApiError
          ? falha.message
          : "Não foi possível enviar a mensagem.",
      );
      // Devolve o texto para o campo: perder o que a pessoa escreveu porque a
      // rede piscou é pior que o erro em si.
      setTexto(mensagem);
      setTurnos((atuais) => atuais.slice(0, -1));
    } finally {
      setEnviando(false);
    }
  }

  function novaConversa() {
    // Invalida qualquer resposta ainda em voo. Sem isto, clicar em "Nova
    // conversa" com uma mensagem a caminho fazia a resposta antiga chegar
    // depois e RESSUSCITAR o lead que acabou de ser descartado, com o
    // `lead_id` dele de volta no localStorage.
    geracao.current += 1;
    dispensouAgenda.current = false;
    localStorage.removeItem(CHAVE_LEAD);
    conversaNaTela.current = null;
    setLeadId(null);
    setTurnos([]);
    setEstado(null);
    setConsentimento(false);
    setAbrirAgenda(false);
    // Sem estes dois, "Nova conversa" limpava a tela mas NÃO destravava o
    // botão Enviar quando a requisição anterior tinha ficado pendurada: a
    // única saída continuava sendo o F5, e o botão de escape não escapava.
    setEnviando(false);
    setErro(null);
  }

  // A altura do cartão desconta o cabeçalho MEDIDO (ver
  // `usarAlturaDoCabecalho` no App.tsx) mais os 3rem do `py-6` do <main>.
  //
  // Era `8.5rem` chumbado, tirado do cabeçalho de UMA linha do desktop. No
  // celular ele quebra em três linhas e vai a 150px, e o cartão terminava 40px
  // abaixo da dobra levando o campo de texto e o botão Enviar junto: medido,
  // acontecia no iPhone SE, no iPhone 14 e no Pixel 7. O 6.7rem de reserva é o
  // cabeçalho do desktop, para o primeiro quadro não saltar enquanto a medida
  // não chega.
  return (
    <div className="mx-auto grid max-w-[76rem] gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
      <Card
        className="flex h-[calc(100dvh-var(--altura-cabecalho,6.7rem)-3rem)] min-h-[26rem] flex-col overflow-hidden"
        titulo={
          <span className="flex min-w-0 items-center gap-2">
            <span className="shrink-0">Conversa</span>
            {/* O id virou um hexadecimal de 21 caracteres (era "lead-0011"),
                e no celular ele sozinho ocupava metade da barra, espremendo o
                botao em duas linhas. Encurtar o TEXTO em vez de truncar por
                CSS evita depender de breakpoint e funciona igual em qualquer
                largura; o valor inteiro fica no `title`, para quem precisa
                conferir ou copiar. */}
            {leadId && (
              <span className="min-w-0" title={leadId}>
                <BadgeSimples cor="bg-superficie-2 text-suave">
                  {leadId.length > 15 ? `${leadId.slice(0, 14)}…` : leadId}
                </BadgeSimples>
              </span>
            )}
          </span>
        }
        acao={
          <Botao variante="secundario" onClick={novaConversa} className="text-xs">
            Nova conversa
          </Botao>
        }
      >
        <div className="flex h-full min-h-0 flex-col">
          <div className="rolagem-suave -mx-4 min-h-0 flex-1 space-y-3 overflow-y-auto px-4">
            <Bolha papel="assistant">{SAUDACAO}</Bolha>

            {turnos.map((turno) => (
              <div key={turno.chave} className="space-y-1.5">
                <Bolha papel={turno.papel} origem={turno.origem}>
                  {turno.conteudo}
                </Bolha>

                {turno.novidades && turno.novidades.length > 0 && (
                  <ChipsNovidades novidades={turno.novidades} />
                )}

                {turno.imoveis && turno.imoveis.length > 0 && (
                  <div className="grid gap-2 pl-1 sm:grid-cols-2">
                    {turno.imoveis.map((imovel) => (
                      <ImovelSugeridoCard key={imovel.id} imovel={imovel} />
                    ))}
                  </div>
                )}
              </div>
            ))}

            {enviando && <Digitando />}
            <div ref={fim} />
          </div>

          {erro && (
            <div className="pt-3">
              <Erro mensagem={erro} />
            </div>
          )}

          {estado?.sugerir_agendamento && !abrirAgenda && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-suave border border-sucesso/40 bg-sucesso/10 px-3 py-2">
              {/* Investidor não visita imóvel, conversa com especialista. O
                  convite seguia fixo em "visita" enquanto o formulário logo
                  abaixo já marcava CONSULTORIA: a tela se contradizia. */}
              <p className="text-sm text-sucesso">
                {investidor
                  ? "Quando quiser, é só escolher o dia da conversa com o especialista."
                  : "Quando quiser, é só escolher o dia da visita."}
              </p>
              <Botao
                onClick={() => {
                  dispensouAgenda.current = false;
                  setAbrirAgenda(true);
                }}
                className="text-xs"
              >
                {investidor ? "Agendar consultoria" : "Agendar visita"}
              </Botao>
            </div>
          )}

          {abrirAgenda && leadId && (
            <FormAgendamento
              leadId={leadId}
              intencao={estado?.perfil?.intent ?? null}
              aoFechar={() => {
                dispensouAgenda.current = true;
                setAbrirAgenda(false);
              }}
            />
          )}

          {primeiraMensagem && (
            <label className="mt-3 flex items-start gap-2 rounded-suave bg-superficie-2 px-3 py-2 text-xs text-suave">
              <input
                type="checkbox"
                checked={consentimento}
                onChange={(e) => setConsentimento(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                Quero que a imobiliária me procure depois sobre imóveis, aqui ou pelo
                contato que eu deixar. É opcional: a conversa funciona sem isso, e dá
                para mudar de ideia a qualquer momento.
              </span>
            </label>
          )}

          {/* Transparência (LGPD art. 9º): o lead tem direito de saber que a
              conversa dele é processada por um terceiro, e por qual. Isto NÃO
              é o consentimento acima, que é sobre ser procurado depois; é
              informação, e por isso aparece sem caixa para marcar e sem pedir
              nada em troca. O texto fica aqui, no primeiro turno, porque é
              antes de escrever que a pessoa precisa saber. */}
          {primeiraMensagem && (
            <p className="mt-2 px-1 text-[0.6875rem] leading-relaxed text-suave">
              Esta conversa é respondida por inteligência artificial e
              processada pelo Google Gemini. Nome, telefone e e-mail são
              substituídos por apelidos antes de sair daqui. Você pode baixar
              ou apagar seus dados a qualquer momento, no painel ao lado.
            </p>
          )}

          <form onSubmit={enviar} className="mt-3 flex shrink-0 gap-2">
            <input
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              aria-label="Sua mensagem"
              placeholder="Escreva sua mensagem..."
              className={classesInput}
              maxLength={4000}
              autoFocus
            />
            <Botao type="submit" disabled={enviando || texto.trim() === ""}>
              {enviando ? "Enviando" : "Enviar"}
            </Botao>
          </form>
        </div>
      </Card>

      <PainelPerfil estado={estado} leadId={leadId} aoApagar={novaConversa} />
    </div>
  );
}

function Bolha({
  papel,
  origem,
  children,
}: {
  papel: Turno["papel"];
  origem?: string | null;
  children: React.ReactNode;
}) {
  const meu = papel === "user";
  // Follow-up é iniciativa do agente, não resposta a nada: distinguir na tela
  // evita o lead achar que perdeu uma mensagem sua.
  const followup = papel === "followup";

  return (
    <div className={`flex ${meu ? "justify-end" : "justify-start"}`}>
      <div
        data-papel={papel}
        className={`max-w-[62ch] rounded-cartao px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap shadow-cartao ${
          meu
            ? "rounded-br-md bg-acento text-acento-tinta"
            : followup
              ? "rounded-bl-md border border-dashed border-morno/40 bg-morno/10 text-morno"
              : "rounded-bl-md border border-linha bg-superficie text-texto"
        }`}
      >
        {followup && (
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-morno">
            Retomada automática
          </p>
        )}
        {children}
        {origem === "mock" && !meu && (
          <span className="ml-2 align-middle text-xs text-suave/70">mock</span>
        )}
      </div>
    </div>
  );
}

// A prova visual de que o agente tem memória: o que ele acabou de anotar.
function ChipsNovidades({ novidades }: { novidades: Novidade[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 pl-1">
      {novidades.map((n) => {
        const correcao = n.kind === "correction";
        return (
          <span
            key={`${n.field}-${n.to}`}
            className={`chip-novidade inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
              correcao
                ? "bg-morno/15 text-morno ring-1 ring-morno/30"
                : "bg-acento/15 text-acento ring-1 ring-acento/30"
            }`}
            title={
              correcao ? `Antes: ${n.from_label ?? n.from ?? "—"}` : undefined
            }
          >
            <span aria-hidden>{correcao ? "✎" : "✓"}</span>
            {correcao ? "corrigi" : "anotei"}:{" "}
            {n.field_label ?? n.field.replace(/_/g, " ")} ={" "}
            {n.to_label ?? n.to ?? "—"}
          </span>
        );
      })}
    </div>
  );
}

function Digitando() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 rounded-cartao rounded-bl-md border border-linha bg-superficie px-3.5 py-2.5 shadow-cartao">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="ponto-digitando size-2 rounded-full bg-acento"
            style={{ animationDelay: `${i * 0.16}s` }}
          />
        ))}
        <span className="ml-1 text-xs text-suave">digitando</span>
      </div>
    </div>
  );
}

// Painel lateral: o que a IA já sabe. Serve à demo (mostra a extração
// estruturada acontecendo) e ao lead (ele confere se foi entendido).
function PainelPerfil({
  estado,
  leadId,
  aoApagar,
}: {
  estado: EstadoLead | null;
  leadId: string | null;
  aoApagar: () => void;
}) {
  const [ocupado, setOcupado] = useState(false);
  // Os tres controles de LGPD compartilham um so estado de erro: eles nunca
  // rodam ao mesmo tempo (todos ficam desabilitados por `ocupado`), e a
  // mensagem sempre se refere ao ultimo que o usuario clicou.
  const [erroLgpd, setErroLgpd] = useState<string | null>(null);
  // A versão pronta para ler vem do backend, junto das outras tabelas de
  // rótulo. O perfil cru fica de reserva: um backend mais antigo não manda
  // `perfil_label`, e é melhor mostrar "RENT" do que não mostrar nada.
  const rotulado = estado?.perfil_label ?? {};
  const nomes = estado?.perfil_campos ?? {};
  const campos = Object.entries(estado?.perfil ?? {})
    .filter(([, valor]) => valor)
    .map(
      ([campo, valor]) =>
        [
          nomes[campo] ?? campo.replace(/_/g, " "),
          rotulado[campo] ?? String(valor),
        ] as const,
    );

  // `null` enquanto não se sabe: o controle não pode chutar "não autorizado" e
  // sugerir ao lead que ele recusou algo que na verdade aceitou.
  const [consentido, setConsentido] = useState<boolean | null>(null);

  // A tela do lead precisa saber o estado atual do aceite para poder oferecer
  // conceder ou revogar. Não existe rota leve para isso; o detalhe do lead é
  // o que há, e o custo é uma chamada por conversa aberta. Sem autenticação
  // neste POC, ela responde para qualquer origem.
  useEffect(() => {
    if (!leadId) {
      setConsentido(null);
      return;
    }

    let vivo = true;
    api
      .detalheLead(leadId)
      .then((lead) => vivo && setConsentido(lead.consentimento))
      .catch(() => vivo && setConsentido(null));

    return () => {
      vivo = false;
    };
  }, [leadId]);

  // Revogar tem que ser tão fácil quanto aceitar, e nenhum dos dois pede
  // confirmação: uma caixa de diálogo no caminho da revogação é atrito
  // colocado de propósito, que é justamente o que a LGPD não admite.
  async function trocarConsentimento(novo: boolean) {
    if (!leadId) return;

    setOcupado(true);
    setErroLgpd(null);
    try {
      const lead = await api.atualizarLead(leadId, { consentimento: novo });
      setConsentido(lead.consentimento);
    } catch (erro) {
      // `try/finally` sem `catch` fazia o botão piscar e nada acontecer, sem
      // uma palavra na tela. Nos controles de LGPD isso é pior do que em
      // qualquer outro lugar: quem clica em "apagar meus dados" e não recebe
      // resposta conclui que os dados NÃO foram apagados, e está certo.
      setErroLgpd(
        erro instanceof ApiError ? erro.message : "Não foi possível mudar a preferência.",
      );
    } finally {
      setOcupado(false);
    }
  }

  async function exportar() {
    if (!leadId) return;
    setOcupado(true);
    setErroLgpd(null);
    try {
      const dados = await api.exportarLead(leadId);
      // Direito de acesso (LGPD): o JSON vira download no navegador.
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `${leadId}-meus-dados.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (erro) {
      setErroLgpd(
        erro instanceof ApiError ? erro.message : "Não foi possível baixar seus dados.",
      );
    } finally {
      setOcupado(false);
    }
  }

  async function apagar() {
    if (!leadId) return;
    const confirmado = window.confirm(
      "Isso apaga seus dados e a memória da IA sobre esta conversa. Continuar?",
    );
    if (!confirmado) return;

    setOcupado(true);
    setErroLgpd(null);
    try {
      await api.apagarLead(leadId);
      aoApagar();
    } catch (erro) {
      setErroLgpd(
        erro instanceof ApiError
          ? `Seus dados NÃO foram apagados: ${erro.message}`
          : "Seus dados NÃO foram apagados. Tente de novo.",
      );
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card titulo="O que já entendi">
        {estado ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <BadgeTemperatura
                temperatura={estado.temperatura}
                label={estado.temperatura_label}
                score={estado.score}
              />
            </div>

            <div className="h-1.5 overflow-hidden rounded-full bg-superficie-2">
              <div
                className="h-full rounded-full bg-acento transition-all duration-500"
                style={{ width: `${Math.min(estado.score, 100)}%` }}
              />
            </div>

            {campos.length > 0 ? (
              <dl className="divide-y divide-linha text-sm">
                {campos.map(([nome, valor]) => (
                  <div key={nome} className="flex justify-between gap-3 py-1.5">
                    <dt className="text-suave first-letter:uppercase">{nome}</dt>
                    <dd className="text-right font-semibold first-letter:uppercase">
                      {valor}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-suave">
                Nada anotado ainda: o agente anota conforme você conta.
              </p>
            )}

            {estado.proxima_acao && (
              <p className="rounded-suave bg-superficie-2 px-3 py-2 text-xs text-suave">
                <span className="font-medium">Próximo passo do agente:</span>{" "}
                {estado.proxima_acao}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-suave">
            Manda a primeira mensagem: o que o agente for entendendo aparece aqui.
          </p>
        )}
      </Card>

      {leadId && (
        <Card titulo="Meus dados (LGPD)">
          <div className="flex flex-col gap-4">
            {consentido !== null && (
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-suave">
                  Contato fora do chat
                </p>

                <p className="text-sm">
                  {consentido
                    ? "Você autorizou a imobiliária a te procurar sobre imóveis."
                    : "Falamos com você só aqui no chat."}
                </p>

                {/* Excluir apaga tudo; isto só desliga o contato. Quem quer
                    parar de ser procurado, mas manter a conversa, não deveria
                    precisar destruir o próprio histórico para conseguir. */}
                <Botao
                  variante="secundario"
                  onClick={() => trocarConsentimento(!consentido)}
                  disabled={ocupado}
                  className="w-full"
                >
                  {consentido ? "Não quero mais ser procurado" : "Pode me procurar"}
                </Botao>

                <p className="text-xs text-suave">
                  A conversa funciona dos dois jeitos, e dá para mudar de ideia
                  quando quiser. A data do seu aceite fica no arquivo abaixo.
                </p>
              </div>
            )}

            <div className="flex flex-col gap-2 border-t border-linha pt-4">
              <Botao variante="secundario" onClick={exportar} disabled={ocupado}>
                Baixar meus dados
              </Botao>
              <Botao variante="perigo" onClick={apagar} disabled={ocupado}>
                Excluir meus dados
              </Botao>
              {erroLgpd && <Erro mensagem={erroLgpd} />}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

// Agendamento direto do chat. O tipo default sai da intenção: quem está
// investindo termina em conversa com especialista, não em visita ao imóvel.
function FormAgendamento({
  leadId,
  intencao,
  aoFechar,
}: {
  leadId: string;
  intencao: string | null;
  aoFechar: () => void;
}) {
  const [quando, setQuando] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [pronto, setPronto] = useState(false);

  const tipo = intencao === "INVEST" ? "CONSULTORIA" : "VISITA";

  async function salvar(evento: React.FormEvent) {
    evento.preventDefault();
    if (!quando) return;

    setSalvando(true);
    setErro(null);
    try {
      await api.agendar(leadId, { data_hora: paraIso(quando), tipo });
      setPronto(true);
    } catch (falha: unknown) {
      setErro(falha instanceof ApiError ? falha.message : "Não foi possível agendar.");
    } finally {
      setSalvando(false);
    }
  }

  if (pronto) {
    return (
      <div className="mt-3 rounded-suave border border-sucesso/40 bg-sucesso/10 px-3 py-2 text-sm text-sucesso">
        {tipo === "CONSULTORIA" ? "Consultoria" : "Visita"} marcada. Um corretor
        confirma com você.
      </div>
    );
  }

  return (
    <form
      onSubmit={salvar}
      className="mt-3 space-y-2 rounded-suave border border-sucesso/40 bg-sucesso/10 p-3"
    >
      <p className="text-sm font-semibold text-sucesso">
        {tipo === "CONSULTORIA"
          ? "Vamos marcar sua conversa com o especialista"
          : "Vamos marcar sua visita"}
      </p>
      <Campo
        rotulo={
          tipo === "CONSULTORIA"
            ? "Melhor dia e hora para a consultoria"
            : "Melhor dia e hora para a visita"
        }
      >
        {/* `min` = agora. Sem ele o seletor aceitava marcar visita para uma
            data JA PASSADA, e o backend confirmava com 201: o lead saia da
            conversa achando que tinha visita marcada para o mes anterior.
            `step` de 30 minutos porque visita de imovel nao comeca 13:47. */}
        <input
          type="datetime-local"
          value={quando}
          min={minimoDoSeletor()}
          step={1800}
          onChange={(e) => setQuando(e.target.value)}
          className={classesInput}
          required
        />
      </Campo>

      {erro && <Erro mensagem={erro} />}

      <div className="flex gap-2">
        <Botao type="submit" disabled={salvando || !quando} className="text-xs">
          {salvando ? "Marcando..." : "Confirmar"}
        </Botao>
        <Botao variante="secundario" type="button" onClick={aoFechar} className="text-xs">
          Depois
        </Botao>
      </div>
    </form>
  );
}
