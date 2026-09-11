// Peças de interface reusadas pelas telas. Nada de regra de negócio aqui.
//
// Toda cor vem de token (`bg-superficie`, `text-suave`, `border-linha`): é o
// que faz a troca de tema custar zero e a direção visual ficar num arquivo só.

import type { ReactNode } from "react";

export function Card({
  titulo,
  acao,
  children,
  className = "",
}: {
  titulo?: ReactNode;
  acao?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-cartao border border-linha bg-superficie shadow-cartao ${className}`}
    >
      {(titulo || acao) && (
        <header className="flex items-center justify-between gap-3 border-b border-linha px-4 py-3">
          <h2 className="font-display text-sm font-semibold">{titulo}</h2>
          {acao}
        </header>
      )}
      {/* `min-h-0 flex-1` é o que sustenta o cartão de altura fixa do chat.
          Sem eles, este corpo cresce com o conteúdo, estoura a seção e o
          `overflow-hidden` do cartão corta o rodapé: o campo de mensagem e o
          botão de enviar desaparecem, e a conversa não rola porque a área de
          rolagem nunca recebe uma altura limitada. Num cartão de altura
          natural, que é a maioria, as duas classes não têm efeito. */}
      <div className="min-h-0 flex-1 p-4">{children}</div>
    </section>
  );
}

export function Kpi({
  rotulo,
  valor,
  detalhe,
  cor = "text-texto",
}: {
  rotulo: string;
  valor: ReactNode;
  detalhe?: string;
  cor?: string;
}) {
  return (
    <div className="rounded-cartao border border-linha bg-superficie p-4 shadow-cartao">
      <p className="text-xs font-semibold uppercase tracking-wide text-suave">
        {rotulo}
      </p>
      <p className={`mt-1 font-display text-3xl font-semibold tabular-nums ${cor}`}>
        {valor}
      </p>
      {detalhe && <p className="mt-0.5 text-xs text-suave">{detalhe}</p>}
    </div>
  );
}

// Barras em CSS puro em vez de uma lib de gráfico: são três listas curtas que o
// backend já manda ordenadas por total e com label em português.
export function Barras({
  itens,
  cor = "bg-acento",
}: {
  itens: { chave: string; label: string; total: number }[];
  cor?: string;
}) {
  if (itens.length === 0) return <Vazio texto="Sem dados ainda." />;

  const maximo = Math.max(...itens.map((i) => i.total), 1);

  return (
    <ul className="space-y-2">
      {itens.map((item) => (
        <li
          key={item.chave}
          className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-2"
        >
          <span className="truncate text-xs text-suave" title={item.label}>
            {item.label}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-superficie-2">
            <span
              className={`block h-full rounded-full ${cor}`}
              style={{ width: `${(item.total / maximo) * 100}%` }}
            />
          </span>
          <span className="text-right text-xs font-semibold tabular-nums">
            {item.total}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function Carregando({ texto = "Carregando..." }: { texto?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-suave">
      <span className="size-4 animate-spin rounded-full border-2 border-linha border-t-acento" />
      {texto}
    </div>
  );
}

// Todas as listas do backend voltam `[]` numa base vazia, e é exatamente o que
// aparece no primeiro boot da demo: o estado vazio é tela de verdade, com
// título, explicação e a saída mais provável.
export function Vazio({
  texto,
  detalhe,
  children,
}: {
  texto: string;
  detalhe?: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-cartao border border-dashed border-linha bg-fundo px-4 py-10 text-center">
      <p className="font-display text-sm font-semibold">{texto}</p>
      {detalhe && (
        <p className="mx-auto mt-1 max-w-[42ch] text-xs leading-relaxed text-suave">
          {detalhe}
        </p>
      )}
      {children && <div className="mt-4 flex justify-center">{children}</div>}
    </div>
  );
}

export function Erro({
  mensagem,
  aoTentarNovamente,
}: {
  mensagem: string;
  aoTentarNovamente?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-suave border border-erro/40 bg-erro/10 px-4 py-3 text-sm text-erro"
    >
      <span>{mensagem}</span>
      {aoTentarNovamente && (
        <button
          type="button"
          onClick={aoTentarNovamente}
          className="rounded-suave border border-erro/40 px-2.5 py-1 text-xs font-semibold hover:bg-erro/15"
        >
          Tentar de novo
        </button>
      )}
    </div>
  );
}

export function Botao({
  children,
  variante = "primario",
  className = "",
  ...resto
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variante?: "primario" | "secundario" | "perigo";
}) {
  const estilos = {
    primario:
      "bg-acento text-acento-tinta hover:brightness-110 disabled:brightness-100 disabled:opacity-50",
    secundario:
      "border border-linha bg-superficie text-texto hover:bg-superficie-2 disabled:opacity-50",
    perigo: "border border-erro/40 text-erro hover:bg-erro/10 disabled:opacity-50",
  }[variante];

  return (
    <button
      {...resto}
      className={`rounded-suave px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed ${estilos} ${className}`}
    >
      {children}
    </button>
  );
}

export function Campo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-suave">{rotulo}</span>
      {children}
    </label>
  );
}

export const classesInput =
  "w-full rounded-suave border border-linha bg-fundo px-3 py-2 text-sm text-texto placeholder:text-suave/70 outline-none focus:border-acento";
