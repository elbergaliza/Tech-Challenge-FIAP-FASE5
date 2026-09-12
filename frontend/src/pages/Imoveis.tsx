// Catálogo de imóveis. Serve ao corretor procurando o que mandar para um lead
// e à demo, para mostrar que o RAG busca numa base real.
//
// Os selects vêm de GET /imoveis/filtros em vez de arrays chumbados aqui: uma
// lista de bairros no código sai de sincronia com a base no primeiro seed novo.

import { useState } from "react";

import { BadgeSimples } from "../components/Badges";
import { Botao, Card, Carregando, Erro, Vazio, classesInput } from "../components/Ui";
import { useApi } from "../hooks/useApi";
import { useDebounce } from "../hooks/useDebounce";
import { moeda, numero } from "../lib/formatar";
import { api } from "../services/api";
import type { Imovel } from "../types";

const POR_PAGINA = 24;

const ROTULOS_NEGOCIO: Record<string, string> = {
  SALE: "venda",
  RENTAL: "aluguel",
};

export default function Imoveis() {
  const filtros = useApi(() => api.filtrosImoveis(), []);

  const [dealType, setDealType] = useState("");
  const [bairro, setBairro] = useState("");
  const [quartosMin, setQuartosMin] = useState("");
  const [precoMax, setPrecoMax] = useState("");
  const [pagina, setPagina] = useState(0);

  const precoMaxAtrasado = useDebounce(precoMax, 400);

  const lista = useApi(
    () =>
      api.listarImoveis({
        deal_type: dealType || undefined,
        neighborhood: bairro || undefined,
        bedrooms_min: quartosMin ? Number(quartosMin) : undefined,
        preco_max: precoMaxAtrasado ? Number(precoMaxAtrasado) : undefined,
        limite: POR_PAGINA,
        offset: pagina * POR_PAGINA,
      }),
    [dealType, bairro, quartosMin, precoMaxAtrasado, pagina],
  );

  // Mudar filtro mantendo a página 3 mostraria "nenhum resultado" num filtro
  // que tem resultados: qualquer troca volta para a primeira página.
  function trocar(setter: (valor: string) => void) {
    return (valor: string) => {
      setter(valor);
      setPagina(0);
    };
  }

  const total = lista.dados?.total ?? 0;
  const ultimaPagina = Math.max(Math.ceil(total / POR_PAGINA) - 1, 0);

  return (
    <div className="space-y-4">
      <Card
        titulo={
          <span className="flex items-center gap-2">
            Catálogo
            {lista.dados && <BadgeSimples>{total} imóveis</BadgeSimples>}
          </span>
        }
      >
        <div className="grid gap-2 sm:grid-cols-4">
          <select
            value={dealType}
            onChange={(e) => trocar(setDealType)(e.target.value)}
            aria-label="Filtrar por tipo de negócio"
            className={classesInput}
          >
            <option value="">Venda e aluguel</option>
            {(filtros.dados?.deal_type ?? []).map((tipo) => (
              <option key={tipo} value={tipo}>
                {ROTULOS_NEGOCIO[tipo] ?? tipo}
              </option>
            ))}
          </select>

          <select
            value={bairro}
            onChange={(e) => trocar(setBairro)(e.target.value)}
            aria-label="Filtrar por bairro"
            className={classesInput}
          >
            <option value="">Todos os bairros</option>
            {(filtros.dados?.neighborhood ?? []).map((nome) => (
              <option key={nome} value={nome}>
                {nome}
              </option>
            ))}
          </select>

          <select
            value={quartosMin}
            onChange={(e) => trocar(setQuartosMin)(e.target.value)}
            aria-label="Filtrar por número mínimo de quartos"
            className={classesInput}
          >
            <option value="">Qualquer nº de quartos</option>
            {(filtros.dados?.bedrooms ?? []).map((n) => (
              <option key={n} value={n}>
                {n}+ quartos
              </option>
            ))}
          </select>

          <input
            type="number"
            min={0}
            value={precoMax}
            onChange={(e) => trocar(setPrecoMax)(e.target.value)}
            aria-label="Preço máximo"
            placeholder="Preço máximo"
            className={classesInput}
          />
        </div>
      </Card>

      {lista.erro ? (
        <Erro mensagem={lista.erro} aoTentarNovamente={lista.recarregar} />
      ) : lista.carregando && !lista.dados ? (
        <Carregando texto="Buscando imóveis..." />
      ) : lista.dados && lista.dados.items.length > 0 ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {lista.dados.items.map((imovel) => (
              <CardImovel key={imovel.id} imovel={imovel} />
            ))}
          </div>

          {total > POR_PAGINA && (
            <div className="flex items-center justify-center gap-3">
              <Botao
                variante="secundario"
                onClick={() => setPagina((p) => Math.max(p - 1, 0))}
                disabled={pagina === 0}
                className="text-xs"
              >
                Anterior
              </Botao>
              <span className="text-xs text-suave tabular-nums">
                página {pagina + 1} de {ultimaPagina + 1}
              </span>
              <Botao
                variante="secundario"
                onClick={() => setPagina((p) => Math.min(p + 1, ultimaPagina))}
                disabled={pagina >= ultimaPagina}
                className="text-xs"
              >
                Próxima
              </Botao>
            </div>
          )}
        </>
      ) : (
        <Vazio
          texto="Nenhum imóvel com esses filtros"
          detalhe="Tente um preço máximo mais alto ou tire o bairro: a base tem imóveis em vinte bairros."
        />
      )}
    </div>
  );
}

function CardImovel({ imovel }: { imovel: Imovel }) {
  const aluguel = imovel.deal_type === "RENTAL";

  return (
    <article className="flex flex-col rounded-cartao border border-linha bg-superficie p-4 shadow-cartao">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-display text-sm font-semibold leading-snug">
          {imovel.title}
        </h3>
        <span className="shrink-0 rounded bg-superficie-2 px-1.5 py-0.5 text-xs font-medium text-suave">
          {imovel.id}
        </span>
      </div>

      <p className="mt-1.5 font-display text-lg font-semibold tabular-nums">
        {moeda(imovel.price)}
        {aluguel && <span className="text-xs font-normal text-suave">/mês</span>}
      </p>

      <p className="text-xs text-suave">
        {imovel.neighborhood} · {imovel.zone} · {imovel.property_type.toLowerCase()}
      </p>

      <p className="mt-2 text-xs text-suave">
        {imovel.bedrooms} {imovel.bedrooms === 1 ? "quarto" : "quartos"} ·{" "}
        {imovel.bathrooms} {imovel.bathrooms === 1 ? "banheiro" : "banheiros"} ·{" "}
        {numero(imovel.area_m2, 0)} m²
        {imovel.parking > 0 ? ` · ${imovel.parking} vaga(s)` : ""}
      </p>

      {imovel.condo_fee ? (
        <p className="mt-0.5 text-xs text-suave">
          condomínio {moeda(imovel.condo_fee)}
        </p>
      ) : null}

      {imovel.features.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {imovel.features.slice(0, 4).map((f) => (
            <span
              key={f}
              className="rounded-full bg-superficie-2 px-2 py-0.5 text-xs text-suave"
            >
              {f}
            </span>
          ))}
        </div>
      )}

      {imovel.annual_yield_pct !== null && (
        <p className="mt-2 text-xs font-medium text-sucesso">
          retorno estimado {numero(imovel.annual_yield_pct)}% ao ano
        </p>
      )}

      <p className="mt-auto pt-2 text-xs uppercase tracking-wide text-suave/70">
        {ROTULOS_NEGOCIO[imovel.deal_type] ?? imovel.deal_type} · {imovel.status}
      </p>
    </article>
  );
}
