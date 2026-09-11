// O imóvel como o RAG devolveu. O `reason` vem escrito em português pelo
// backend e é o que faz a sugestão parecer inteligente em vez de aleatória,
// por isso ele fica visível no card, e não num tooltip.

import { moeda, numero } from "../lib/formatar";
import type { ImovelSugerido } from "../types";

export function ImovelSugeridoCard({ imovel }: { imovel: ImovelSugerido }) {
  const aluguel = imovel.deal_type === "RENTAL";

  return (
    <article className="rounded-cartao border border-linha bg-superficie p-3 shadow-cartao">
      <div className="flex items-start justify-between gap-2">
        <h4 className="font-display text-sm font-semibold leading-snug">
          {imovel.title}
        </h4>
        <span className="shrink-0 rounded bg-superficie-2 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-suave">
          {imovel.id}
        </span>
      </div>

      <p className="mt-1 font-display text-lg font-semibold tabular-nums">
        {moeda(imovel.price)}
        {aluguel && <span className="text-xs font-normal text-suave">/mês</span>}
      </p>

      <p className="mt-0.5 text-xs text-suave">
        {imovel.neighborhood}
        {imovel.zone ? ` · ${imovel.zone}` : ""} · {imovel.bedrooms}{" "}
        {imovel.bedrooms === 1 ? "quarto" : "quartos"}
        {imovel.area_m2 ? ` · ${numero(imovel.area_m2, 0)} m²` : ""}
      </p>

      {imovel.reason && (
        <p className="mt-2 border-t border-linha pt-2 text-xs leading-relaxed text-acento">
          <span className="font-semibold">Por que este:</span> {imovel.reason}
        </p>
      )}
    </article>
  );
}
