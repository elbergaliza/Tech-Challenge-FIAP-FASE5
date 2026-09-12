// Os testes de data são escritos para não depender do fuso da máquina que
// roda a suíte: em vez de comparar com uma string formatada em -03:00, eles
// comparam instantes e derivam a expectativa por um caminho independente
// (`toLocaleDateString("sv")`, que dá YYYY-MM-DD). Um teste que só passa no
// notebook de quem escreveu não vale como teste.

import { describe, expect, it } from "vitest";

import {
  chaveDia,
  dataHora,
  diaSemana,
  hora,
  moeda,
  numero,
  paraDatetimeLocal,
  paraIso,
  silencio,
} from "./formatar";

describe("moeda", () => {
  it("formata em real, sem centavos", () => {
    // O espaço do pt-BR é não separável (U+00A0), não um espaço comum.
    expect(moeda(3700)).toMatch(/^R\$\s?3\.700$/);
  });

  it("devolve travessão para valor ausente, em vez de R$ 0 ou NaN", () => {
    expect(moeda(null)).toBe("—");
    expect(moeda(undefined)).toBe("—");
  });

  it("mostra zero como zero, porque zero é um preço informado", () => {
    expect(moeda(0)).toMatch(/R\$\s?0/);
  });
});

describe("numero", () => {
  it("usa vírgula decimal", () => {
    expect(numero(47.3)).toBe("47,3");
  });

  it("respeita a quantidade de decimais pedida", () => {
    expect(numero(56.7, 0)).toBe("57");
  });

  it("não confunde ausência com zero", () => {
    expect(numero(null)).toBe("—");
    expect(numero(0)).toBe("0,0");
  });
});

describe("datas sem fuso", () => {
  // O backend grava `datetime.now(timezone.utc)`, mas o SQLite descarta o
  // tzinfo e a API devolve "2026-09-07T16:21:10.714621", sem sufixo. O JS lê
  // ISO sem fuso como hora LOCAL, o que mostraria toda mensagem 3 horas no
  // futuro num fuso -03:00. Este é o teste que trava esse comportamento.
  const semFuso = "2026-09-07T16:21:10.714621";
  const comZ = "2026-09-07T16:21:10.714621Z";

  it("trata ISO sem fuso como UTC", () => {
    expect(dataHora(semFuso)).toBe(dataHora(comZ));
    expect(hora(semFuso)).toBe(hora(comZ));
    expect(diaSemana(semFuso)).toBe(diaSemana(comZ));
    expect(chaveDia(semFuso)).toBe(chaveDia(comZ));
  });

  it("não mexe em quem já manda o fuso", () => {
    // O mesmo instante escrito de dois jeitos tem que sair igual na tela.
    expect(dataHora("2026-09-07T16:21:10+00:00")).toBe(dataHora(comZ));
    expect(dataHora("2026-09-07T13:21:10-03:00")).toBe(dataHora(comZ));
  });

  it("devolve travessão para nulo em vez de 'Invalid Date'", () => {
    expect(dataHora(null)).toBe("—");
    expect(dataHora(undefined)).toBe("—");
    expect(hora(null)).toBe("—");
  });
});

describe("chaveDia", () => {
  it("agrupa pelo dia local, não pelo dia em UTC", () => {
    const iso = "2026-09-10T18:00:00";
    const esperado = new Date(iso + "Z").toLocaleDateString("sv-SE");
    expect(chaveDia(iso)).toBe(esperado);
  });

  it("separa dois instantes que caem em dias locais diferentes", () => {
    // 03:00Z e 21:00Z do mesmo dia UTC caem em dias locais distintos em
    // qualquer fuso entre -11 e +11.
    const a = chaveDia("2026-09-10T03:00:00Z");
    const b = chaveDia("2026-09-10T21:00:00Z");
    const cruzou = new Date("2026-09-10T03:00:00Z").getDate() !==
      new Date("2026-09-10T21:00:00Z").getDate();
    if (cruzou) expect(a).not.toBe(b);
    else expect(a).toBe(b);
  });
});

describe("ida e volta do datetime-local", () => {
  it("preserva o instante", () => {
    const original = "2026-09-10T18:00:00Z";
    const local = paraDatetimeLocal(original);

    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    // O input não tem segundos, então o instante volta truncado no minuto.
    expect(new Date(paraIso(local)).getTime()).toBe(
      new Date("2026-09-10T18:00:00Z").getTime(),
    );
  });

  it("manda ISO com fuso para o backend, nunca a hora local crua", () => {
    const iso = paraIso("2026-09-10T15:00");
    expect(iso).toMatch(/Z$/);
    expect(new Date(iso).getMinutes()).toBe(new Date("2026-09-10T15:00").getMinutes());
  });
});

describe("silencio", () => {
  it("resume horas em algo que o corretor lê de relance", () => {
    expect(silencio(0.4)).toBe("agora");
    expect(silencio(3.4)).toBe("3h");
    expect(silencio(23.6)).toBe("24h");
    expect(silencio(72)).toBe("3d");
  });

  it("não inventa 0h para lead sem mensagem", () => {
    expect(silencio(null)).toBe("—");
  });
});
