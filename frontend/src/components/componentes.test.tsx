// Testes dos componentes compartilhados. O que se verifica aqui são as regras
// do sistema, não a aparência: rótulo sempre vem do backend, temperatura sempre
// sai do mesmo token, e o controle de tema tem que ter os três estados.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BadgeAgendamento, BadgeStatus, BadgeTemperatura } from "./Badges";
import { Barras, Botao, Card, Erro, Kpi, Vazio } from "./Ui";
import { Tema } from "./Tema";

describe("BadgeTemperatura", () => {
  it("mostra o rótulo em português que o backend mandou", () => {
    render(<BadgeTemperatura temperatura="HOT" label="QUENTE" score={78} />);

    expect(screen.getByText(/QUENTE/)).toBeInTheDocument();
    expect(screen.getByText("· 78")).toBeInTheDocument();
  });

  it("cai no valor cru quando o backend não mandar rótulo", () => {
    // Preferível a mostrar vazio: a informação continua na tela.
    render(<BadgeTemperatura temperatura="COLD" label={null} />);
    expect(screen.getByText("COLD")).toBeInTheDocument();
  });

  it("pinta cada temperatura com o token correspondente", () => {
    const { container: quente } = render(
      <BadgeTemperatura temperatura="HOT" label="QUENTE" />,
    );
    expect(quente.firstChild).toHaveClass("text-quente");

    const { container: morno } = render(
      <BadgeTemperatura temperatura="WARM" label="MORNO" />,
    );
    expect(morno.firstChild).toHaveClass("text-morno");

    const { container: frio } = render(
      <BadgeTemperatura temperatura="COLD" label="FRIO" />,
    );
    expect(frio.firstChild).toHaveClass("text-frio");
  });

  it("não quebra com temperatura que o front ainda não conhece", () => {
    render(
      <BadgeTemperatura
        temperatura={"TEPID" as never}
        label="MORNINHO"
      />,
    );
    expect(screen.getByText("MORNINHO")).toBeInTheDocument();
  });
});

describe("BadgeStatus", () => {
  it("usa o status_label do backend", () => {
    render(<BadgeStatus status="EM_ANDAMENTO" label="Em andamento" />);
    expect(screen.getByText("Em andamento")).toBeInTheDocument();
  });

  it("risca o lead descartado", () => {
    const { container } = render(<BadgeStatus status="DESCARTADO" label="Descartado" />);
    expect(container.firstChild).toHaveClass("line-through");
  });

  it("não usa a escala de temperatura para status", () => {
    // Status é passo do funil. Se ele pegasse as cores de quente/morno/frio,
    // a linha da lista passaria duas informações diferentes na mesma cor.
    const { container } = render(<BadgeStatus status="QUALIFICADO" label="Qualificado" />);
    const classes = String((container.firstChild as HTMLElement).className);

    expect(classes).not.toMatch(/text-(quente|morno|frio)/);
  });
});

describe("BadgeAgendamento", () => {
  it("traduz os status da agenda, que a API manda crus", () => {
    render(<BadgeAgendamento status="NAO_COMPARECEU" />);
    expect(screen.getByText("Não compareceu")).toBeInTheDocument();
  });
});

describe("Card", () => {
  it("deixa o corpo encolher, que é o que faz o chat rolar", () => {
    // Guarda de regressão, não estética. O cartão do chat tem altura fixa e
    // `overflow-hidden`; se alguém "limpar" estas duas classes do corpo, ele
    // volta a crescer com o conteúdo, a conversa para de rolar e o campo de
    // mensagem desaparece cortado no rodapé. O jsdom não faz layout, então
    // isto verifica o contrato de classes, que é o que existe para verificar.
    const { container } = render(
      <Card titulo="Conversa">
        <p>corpo</p>
      </Card>,
    );

    const corpo = container.querySelector("section > div:last-child")!;
    expect(corpo).toHaveClass("min-h-0");
    expect(corpo).toHaveClass("flex-1");
  });
});

describe("Kpi", () => {
  it("alinha o número em tabular, para a coluna não dançar", () => {
    render(<Kpi rotulo="Leads" valor={12} detalhe="ligar hoje" />);

    expect(screen.getByText("Leads")).toBeInTheDocument();
    expect(screen.getByText("12")).toHaveClass("tabular-nums");
    expect(screen.getByText("ligar hoje")).toBeInTheDocument();
  });
});

describe("Barras", () => {
  it("desenha a maior barra em 100% e as outras em proporção", () => {
    const { container } = render(
      <Barras
        itens={[
          { chave: "A", label: "Botafogo", total: 8 },
          { chave: "B", label: "Tijuca", total: 2 },
        ]}
      />,
    );

    const larguras = [...container.querySelectorAll("span[style]")].map(
      (el) => (el as HTMLElement).style.width,
    );
    expect(larguras).toEqual(["100%", "25%"]);
  });

  it("mostra estado vazio em vez de um gráfico em branco", () => {
    render(<Barras itens={[]} />);
    expect(screen.getByText("Sem dados ainda.")).toBeInTheDocument();
  });
});

describe("Vazio", () => {
  it("explica o vazio, não só constata", () => {
    render(<Vazio texto="Nenhum lead ainda" detalhe="Abra o chat e mande a primeira." />);

    expect(screen.getByText("Nenhum lead ainda")).toBeInTheDocument();
    expect(screen.getByText("Abra o chat e mande a primeira.")).toBeInTheDocument();
  });
});

describe("Erro", () => {
  it("é anunciado para leitor de tela", () => {
    render(<Erro mensagem="API fora do ar" />);
    expect(screen.getByRole("alert")).toHaveTextContent("API fora do ar");
  });

  it("só oferece 'tentar de novo' quando há o que tentar", async () => {
    const tentar = vi.fn();
    const { rerender } = render(<Erro mensagem="falhou" aoTentarNovamente={tentar} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(tentar).toHaveBeenCalledTimes(1);

    rerender(<Erro mensagem="falhou" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("Botao", () => {
  it("não dispara quando desabilitado", async () => {
    const clicou = vi.fn();
    render(
      <Botao disabled onClick={clicou}>
        Enviar
      </Botao>,
    );

    await userEvent.setup().click(screen.getByRole("button"));
    expect(clicou).not.toHaveBeenCalled();
  });
});

describe("Tema", () => {
  const raiz = document.documentElement;

  it("nasce no escuro", () => {
    render(<Tema />);
    expect(screen.getByRole("button", { name: "Escuro" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("troca para claro e estampa o atributo que o CSS lê", async () => {
    render(<Tema />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Claro" }));

    expect(raiz.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Claro" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(localStorage.getItem("sdr-tema")).toBe("claro");
  });

  it("em Auto remove o atributo, devolvendo a decisão ao sistema", async () => {
    render(<Tema />);
    const usuario = userEvent.setup();

    await usuario.click(screen.getByRole("button", { name: "Claro" }));
    await usuario.click(screen.getByRole("button", { name: "Auto" }));

    // Este é o estado que quebra primeiro quando alguém "simplifica" o
    // controle para um interruptor de dois estados.
    expect(raiz.hasAttribute("data-theme")).toBe(false);
    expect(localStorage.getItem("sdr-tema")).toBe("sistema");
  });

  it("é um grupo de botões navegável por teclado", () => {
    render(<Tema />);

    expect(screen.getByRole("group", { name: "Tema" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });
});
