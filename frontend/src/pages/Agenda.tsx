// A agenda do corretor. O backend já devolve `lead_nome` e `imovel_titulo`
// desnormalizados, então cada item se desenha sozinho, sem duas chamadas a mais
// por linha.

import { useState } from "react";
import { Link } from "react-router-dom";

import { BadgeAgendamento } from "../components/Badges";
import { Botao, Card, Carregando, Erro, Vazio, classesInput } from "../components/Ui";
import { useApi } from "../hooks/useApi";
import { chaveDia, diaSemana, hora, paraDatetimeLocal, paraIso } from "../lib/formatar";
import { ApiError, api } from "../services/api";
import type { Agendamento, StatusAgendamento } from "../types";

const JANELAS = [
  { dias: 7, texto: "7 dias" },
  { dias: 15, texto: "15 dias" },
  { dias: 30, texto: "30 dias" },
];

export default function Agenda() {
  const [dias, setDias] = useState(7);
  const [status, setStatus] = useState("");
  const { dados, carregando, erro, recarregar } = useApi(
    () => api.listarAgenda({ proximos_dias: dias, status }),
    [dias, status],
  );

  // Agrupado por dia: a agenda é lida por dia, não como lista corrida.
  const porDia = new Map<string, Agendamento[]>();
  for (const item of dados ?? []) {
    const chave = chaveDia(item.data_hora);
    porDia.set(chave, [...(porDia.get(chave) ?? []), item]);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {JANELAS.map((janela) => (
          <button
            key={janela.dias}
            type="button"
            onClick={() => setDias(janela.dias)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition ${
              dias === janela.dias
                ? "bg-acento text-acento-tinta"
                : "bg-superficie-2 text-suave hover:text-texto"
            }`}
          >
            Próximos {janela.texto}
          </button>
        ))}

        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filtrar agendamentos por status"
          className={`${classesInput} ml-auto w-auto`}
        >
          <option value="">Todos os status</option>
          <option value="AGENDADO">Agendados</option>
          <option value="REALIZADO">Realizados</option>
          <option value="CANCELADO">Cancelados</option>
          <option value="NAO_COMPARECEU">Não compareceram</option>
        </select>
      </div>

      {erro ? (
        <Erro mensagem={erro} aoTentarNovamente={recarregar} />
      ) : carregando && !dados ? (
        <Carregando texto="Carregando a agenda..." />
      ) : porDia.size === 0 ? (
        <Vazio
          texto="Nada marcado nessa janela"
          detalhe="Agendamentos nascem no chat, quando o perfil do lead fica completo, ou na tela do lead."
        />
      ) : (
        <div className="space-y-4">
          {[...porDia.entries()].map(([dia, itens]) => (
            <Card key={dia} titulo={diaSemana(itens[0].data_hora)}>
              <ul className="space-y-2">
                {itens.map((item) => (
                  <LinhaAgendamento key={item.id} item={item} aoMudar={recarregar} />
                ))}
              </ul>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function LinhaAgendamento({
  item,
  aoMudar,
}: {
  item: Agendamento;
  aoMudar: () => void;
}) {
  const [remarcando, setRemarcando] = useState(false);
  const [quando, setQuando] = useState(() => paraDatetimeLocal(item.data_hora));
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function aplicar(alteracoes: Partial<{ data_hora: string; status: StatusAgendamento }>) {
    setSalvando(true);
    setErro(null);
    try {
      await api.atualizarAgendamento(item.id, alteracoes);
      setRemarcando(false);
      aoMudar();
    } catch (falha: unknown) {
      setErro(falha instanceof ApiError ? falha.message : "Não foi possível atualizar.");
    } finally {
      setSalvando(false);
    }
  }

  const pendente = item.status === "AGENDADO";

  return (
    <li className="rounded-suave border border-linha p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold tabular-nums text-texto">
            {hora(item.data_hora)}{" "}
            <span className="font-normal text-suave">· {item.tipo.toLowerCase()}</span>
          </p>
          <p className="mt-0.5 text-sm text-texto">
            <Link
              to={`/painel/leads/${item.lead_id}`}
              className="alvo-de-toque font-medium underline-offset-2 hover:underline"
            >
              {item.lead_nome ?? item.lead_id}
            </Link>
            {item.imovel_titulo && (
              <span className="text-suave"> · {item.imovel_titulo}</span>
            )}
          </p>
          {item.corretor && (
            <p className="text-xs text-suave">corretor: {item.corretor}</p>
          )}
        </div>

        <BadgeAgendamento status={item.status} />
      </div>

      {pendente && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Acao onClick={() => aplicar({ status: "REALIZADO" })} disabled={salvando}>
            Realizado
          </Acao>
          <Acao onClick={() => aplicar({ status: "NAO_COMPARECEU" })} disabled={salvando}>
            Não compareceu
          </Acao>
          <Acao onClick={() => setRemarcando((r) => !r)} disabled={salvando}>
            Remarcar
          </Acao>
          {/* Cancelar a única visita devolve o lead para QUALIFICADO no
              backend, então ele não desaparece das listas de atenção. */}
          <Acao
            onClick={() => aplicar({ status: "CANCELADO" })}
            disabled={salvando}
            perigo
          >
            Cancelar
          </Acao>
        </div>
      )}

      {remarcando && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="text-xs text-suave">
            <span className="mb-1 block font-medium">Nova data e hora</span>
            <input
              type="datetime-local"
              value={quando}
              onChange={(e) => setQuando(e.target.value)}
              className={classesInput}
            />
          </label>
          <Botao
            onClick={() => aplicar({ data_hora: paraIso(quando) })}
            disabled={salvando || !quando}
            className="text-xs"
          >
            Salvar
          </Botao>
        </div>
      )}

      {erro && (
        <div className="mt-2">
          <Erro mensagem={erro} />
        </div>
      )}
    </li>
  );
}

function Acao({
  children,
  perigo = false,
  ...resto
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { perigo?: boolean }) {
  return (
    <button
      {...resto}
      type="button"
      className={`rounded-full border px-2.5 py-1 text-xs font-medium transition disabled:opacity-40 ${
        perigo
          ? "border-erro/40 text-erro hover:bg-erro/10"
          : "border-linha text-suave hover:bg-superficie-2"
      }`}
    >
      {children}
    </button>
  );
}
