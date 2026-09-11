// Rede de segurança da árvore inteira.
//
// Uma exceção durante o render desmonta TUDO: some o cabeçalho, some a
// navegação, e a tela fica branca sem uma palavra. Quem está testando não tem
// como saber se travou, se o backend caiu ou se clicou errado, e o F5 só
// ajuda quando a causa era passageira.
//
// Os pontos que podem estourar são conhecidos: um campo ausente numa resposta
// da API (`lead.mensagens.length`, `imovel.property_type.toLowerCase()`) e o
// `localStorage` bloqueado pelo navegador, que lança na leitura.
//
// É uma classe porque `componentDidCatch` não existe em hook: é o único lugar
// do projeto onde o React ainda exige classe.

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type Estado = { erro: Error | null };

export class LimiteDeErro extends Component<Props, Estado> {
  state: Estado = { erro: null };

  static getDerivedStateFromError(erro: Error): Estado {
    return { erro };
  }

  componentDidCatch(erro: Error, info: ErrorInfo) {
    // O console continua sendo o lugar do rastro completo: a tela mostra o
    // suficiente para a pessoa decidir o que fazer, sem virar um dump.
    console.error("[ui] Render falhou:", erro, info.componentStack);
  }

  render() {
    if (!this.state.erro) return this.props.children;

    return (
      <div className="mx-auto max-w-xl p-6">
        <div className="rounded-cartao border border-linha bg-superficie p-6 shadow-cartao">
          <h1 className="text-lg font-semibold">Algo quebrou nesta tela</h1>

          <p className="mt-2 text-sm text-suave">
            O resto do sistema continua no ar. Recarregar costuma resolver; se
            voltar a acontecer, a mensagem abaixo diz onde olhar.
          </p>

          <pre className="mt-4 overflow-x-auto rounded-cartao bg-superficie-2 p-3 text-xs text-suave">
            {this.state.erro.message}
          </pre>

          <div className="mt-4 flex gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-cartao bg-acento px-4 py-2 text-sm font-medium text-fundo"
            >
              Recarregar
            </button>

            {/* Uma saída que NÃO depende de a rota atual voltar a funcionar:
                se o defeito está no detalhe do lead, recarregar cairia nele de
                novo. */}
            <button
              type="button"
              onClick={() => {
                window.location.href = "/";
              }}
              className="rounded-cartao border border-linha px-4 py-2 text-sm"
            >
              Voltar ao chat
            </button>
          </div>
        </div>
      </div>
    );
  }
}
