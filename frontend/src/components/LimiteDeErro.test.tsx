// Sem ErrorBoundary, uma exceção de render deixava a página EM BRANCO: sem
// cabeçalho, sem navegação, sem uma palavra. Quem está testando não tem como
// distinguir "travou" de "cliquei errado".

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LimiteDeErro } from "./LimiteDeErro";

function Explode({ quando }: { quando: boolean }): React.ReactElement {
  if (quando) throw new Error("lead.mensagens is undefined");
  return <p>conteúdo normal</p>;
}

let erroDoConsole: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // O React imprime o erro capturado no console, e o `componentDidCatch`
  // imprime o dele: sem silenciar, a saída do teste vira parede de vermelho e
  // esconde a falha de verdade quando houver uma.
  erroDoConsole = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  erroDoConsole.mockRestore();
});

describe("LimiteDeErro", () => {
  it("não aparece quando nada quebra", () => {
    render(
      <LimiteDeErro>
        <Explode quando={false} />
      </LimiteDeErro>,
    );

    expect(screen.getByText("conteúdo normal")).toBeInTheDocument();
    expect(screen.queryByText(/Algo quebrou/i)).toBeNull();
  });

  it("mostra a tela de recuperação em vez da página em branco", () => {
    render(
      <LimiteDeErro>
        <Explode quando />
      </LimiteDeErro>,
    );

    expect(screen.getByText(/Algo quebrou nesta tela/i)).toBeInTheDocument();
  });

  it("diz qual foi o erro, para dar o que procurar", () => {
    render(
      <LimiteDeErro>
        <Explode quando />
      </LimiteDeErro>,
    );

    expect(screen.getByText(/lead\.mensagens is undefined/)).toBeInTheDocument();
  });

  it("oferece duas saídas, e não só recarregar", () => {
    // Recarregar não ajuda quando a causa persiste: se o defeito está no
    // detalhe do lead, o F5 cai nele de novo. Voltar ao chat sempre sai.
    render(
      <LimiteDeErro>
        <Explode quando />
      </LimiteDeErro>,
    );

    expect(screen.getByRole("button", { name: "Recarregar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Voltar ao chat" })).toBeInTheDocument();
  });

  it("registra o erro no console, que é onde fica o rastro completo", () => {
    render(
      <LimiteDeErro>
        <Explode quando />
      </LimiteDeErro>,
    );

    expect(erroDoConsole).toHaveBeenCalled();
  });
});
