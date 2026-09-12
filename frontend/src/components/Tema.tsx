// Controle de tema no cabeçalho. Três estados de verdade, não um interruptor:
// "sistema" existe para quem já configurou o computador e não quer decidir de
// novo em cada site.

import { useState } from "react";

import { aplicarTema, lerTema, type Tema as TemaEscolhido } from "../lib/tema";

const OPCOES: { valor: TemaEscolhido; texto: string; titulo: string }[] = [
  { valor: "escuro", texto: "Escuro", titulo: "Sempre escuro" },
  { valor: "claro", texto: "Claro", titulo: "Sempre claro" },
  { valor: "sistema", texto: "Auto", titulo: "Segue o tema do sistema" },
];

export function Tema() {
  const [tema, setTema] = useState<TemaEscolhido>(() => lerTema());

  function trocar(escolha: TemaEscolhido) {
    aplicarTema(escolha);
    setTema(escolha);
  }

  return (
    <div
      role="group"
      aria-label="Tema"
      className="flex gap-0.5 rounded-full border border-linha bg-fundo p-0.5"
    >
      {OPCOES.map((opcao) => (
        <button
          key={opcao.valor}
          type="button"
          title={opcao.titulo}
          aria-pressed={tema === opcao.valor}
          onClick={() => trocar(opcao.valor)}
          className={`rounded-full px-2.5 py-1 text-xs font-semibold transition ${
            tema === opcao.valor
              ? "bg-acento text-acento-tinta"
              : "text-suave hover:text-texto"
          }`}
        >
          {opcao.texto}
        </button>
      ))}
    </div>
  );
}
