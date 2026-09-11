// Casca do app: navegação entre as duas experiências (o lead no chat e o
// corretor no painel), o controle de tema e o aviso global de que a IA está em
// modo mock.

import { NavLink, Route, Routes } from "react-router-dom";

import { Tema } from "./components/Tema";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import LeadDetalhe from "./pages/LeadDetalhe";
import Agenda from "./pages/Agenda";
import Imoveis from "./pages/Imoveis";
import { useSaude } from "./hooks/useSaude";

// O painel do corretor e a tela do lead são a MESMA aplicação, sem login, e o
// endereço é o mesmo. Quem abre o chat vê, na barra de cima, o atalho para o
// dashboard com o nome, o telefone e a conversa de todos os outros leads.
//
// Numa demo local isso é inofensivo, e o projeto é uma demo local. Mas basta
// alguém subir isto em qualquer lugar acessível para virar vazamento, e a
// separação custa uma variável de ambiente. O padrão é LIGADO justamente para
// a banca continuar navegando pelas quatro telas sem configurar nada; quem
// publicar em algum lugar põe `VITE_MODO_CORRETOR=false` no `.env` e o
// visitante passa a ver só o chat.
//
// As rotas continuam existindo: isto esconde a navegação, não implementa
// autenticação, e a diferença está escrita aqui para ninguém confundir as duas
// coisas na hora de apresentar.
export const MODO_CORRETOR =
  (import.meta.env.VITE_MODO_CORRETOR ?? "true").toLowerCase() !== "false";

const ABAS_DO_CORRETOR = [
  { para: "/painel", texto: "Dashboard", fim: true },
  { para: "/painel/agenda", texto: "Agenda", fim: false },
  { para: "/painel/imoveis", texto: "Imóveis", fim: false },
];

const ABAS = [
  { para: "/", texto: "Chat", fim: true },
  ...(MODO_CORRETOR ? ABAS_DO_CORRETOR : []),
];

function Cabecalho() {
  const saude = useSaude();
  const modoMock = saude !== null && saude !== false && saude.ia.agente === "mock";

  return (
    <header className="mosaico sticky top-0 z-10 border-b border-linha bg-superficie/95 backdrop-blur">
      <div className="mx-auto flex max-w-[88rem] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grid size-9 place-items-center rounded-suave bg-acento font-display text-sm font-bold text-acento-tinta">
            SDR
          </span>
          <div className="leading-tight">
            <p className="font-display text-sm font-semibold">Agente SDR Imobiliário</p>
            <p className="text-xs text-suave">Qualificação de leads por IA</p>
          </div>
        </div>

        <nav className="flex gap-1">
          {ABAS.map((aba) => (
            <NavLink
              key={aba.para}
              to={aba.para}
              end={aba.fim}
              className={({ isActive }) =>
                `rounded-suave px-3 py-1.5 text-sm font-semibold transition ${
                  isActive
                    ? "bg-acento text-acento-tinta"
                    : "text-suave hover:bg-superficie-2 hover:text-texto"
                }`
              }
            >
              {aba.texto}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 text-xs">
          {saude === null ? (
            <span className="text-suave">verificando API...</span>
          ) : saude === false ? (
            <span className="rounded-full bg-erro/15 px-2 py-0.5 font-semibold text-erro">
              API offline
            </span>
          ) : (
            <>
              {/* Honestidade sobre quem respondeu: numa demo com chave de API no
                  meio, "o agente está em mock" é a primeira coisa que alguém
                  precisa descobrir sem ler log. */}
              {modoMock && (
                <span
                  className="rounded-full bg-morno/15 px-2 py-0.5 font-semibold text-morno"
                  title="Sem GEMINI_API_KEY: as respostas vêm de um agente simulado."
                >
                  IA em modo mock
                </span>
              )}
              <span className="rounded-full bg-sucesso/15 px-2 py-0.5 font-semibold text-sucesso">
                API no ar
              </span>
            </>
          )}

          <Tema />
        </div>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <div className="min-h-dvh">
      <Cabecalho />
      <main className="mx-auto max-w-[88rem] px-4 py-6">
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/painel" element={<Dashboard />} />
          <Route path="/painel/leads/:leadId" element={<LeadDetalhe />} />
          <Route path="/painel/agenda" element={<Agenda />} />
          <Route path="/painel/imoveis" element={<Imoveis />} />
          <Route
            path="*"
            element={<p className="text-sm text-suave">Página não encontrada.</p>}
          />
        </Routes>
      </main>
    </div>
  );
}
