// Badges de enum. O TEXTO vem sempre do `*_label` que o backend manda; aqui só
// se decide a COR. Traduzir de novo no front era o caminho garantido para a
// tela e a API discordarem.
//
// As três cores de temperatura saem dos mesmos tokens usados pela faixa da
// lista e pelos KPIs: quente é a mesma cor em toda tela do app.

import type { StatusAgendamento, StatusLead, Temperatura } from "../types";

const base =
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap";

const CORES_TEMPERATURA: Record<Temperatura, string> = {
  HOT: "bg-quente/15 text-quente ring-1 ring-quente/30",
  WARM: "bg-morno/15 text-morno ring-1 ring-morno/30",
  COLD: "bg-frio/15 text-frio ring-1 ring-frio/30",
};

export function BadgeTemperatura({
  temperatura,
  label,
  score,
}: {
  temperatura: Temperatura;
  label?: string | null;
  score?: number;
}) {
  const cor = CORES_TEMPERATURA[temperatura] ?? "bg-superficie-2 text-suave";
  return (
    <span className={`${base} ${cor}`}>
      {label ?? temperatura}
      {score !== undefined && <span className="tabular-nums opacity-70">· {score}</span>}
    </span>
  );
}

// Status é o passo do funil, não a temperatura: fica em cinza e no acento, para
// não competir com a escala quente/morno/frio na mesma linha.
const CORES_STATUS: Record<StatusLead, string> = {
  NOVO: "bg-superficie-2 text-suave",
  EM_ANDAMENTO: "bg-superficie-2 text-texto",
  QUALIFICADO: "bg-acento/15 text-acento ring-1 ring-acento/30",
  AGENDADO: "bg-sucesso/15 text-sucesso ring-1 ring-sucesso/30",
  DESCARTADO: "bg-superficie-2 text-suave line-through",
};

export function BadgeStatus({
  status,
  label,
}: {
  status: StatusLead;
  label?: string | null;
}) {
  return (
    <span className={`${base} ${CORES_STATUS[status] ?? "bg-superficie-2 text-suave"}`}>
      {label ?? status}
    </span>
  );
}

const CORES_AGENDAMENTO: Record<StatusAgendamento, string> = {
  AGENDADO: "bg-acento/15 text-acento",
  REALIZADO: "bg-sucesso/15 text-sucesso",
  CANCELADO: "bg-superficie-2 text-suave",
  NAO_COMPARECEU: "bg-morno/15 text-morno",
};

const ROTULOS_AGENDAMENTO: Record<StatusAgendamento, string> = {
  AGENDADO: "Agendado",
  REALIZADO: "Realizado",
  CANCELADO: "Cancelado",
  NAO_COMPARECEU: "Não compareceu",
};

export function BadgeAgendamento({ status }: { status: StatusAgendamento }) {
  return (
    <span className={`${base} ${CORES_AGENDAMENTO[status] ?? "bg-superficie-2"}`}>
      {ROTULOS_AGENDAMENTO[status] ?? status}
    </span>
  );
}

export function BadgeSimples({
  children,
  cor = "bg-superficie-2 text-suave",
}: {
  children: React.ReactNode;
  cor?: string;
}) {
  return <span className={`${base} ${cor}`}>{children}</span>;
}
