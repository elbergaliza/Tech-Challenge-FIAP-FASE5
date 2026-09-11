// Formatação para exibição. O backend manda ISO 8601 em UTC e número cru; a
// conversão para o fuso e para a vírgula decimal do pt-BR acontece só aqui.

const MOEDA = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

export function moeda(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "—";
  return MOEDA.format(valor);
}

export function numero(valor: number | null | undefined, decimais = 1): string {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("pt-BR", {
    minimumFractionDigits: decimais,
    maximumFractionDigits: decimais,
  });
}

// O backend grava `datetime.now(timezone.utc)`, mas o SQLite não guarda o fuso:
// os timestamps voltam da API como "2026-09-07T16:21:10.714621", sem sufixo.
// Um ISO sem fuso é interpretado pelo JS como hora LOCAL, e num fuso -03:00 isso
// mostraria toda mensagem 3 horas no futuro. O valor é UTC, então quando o fuso
// não vem escrito, ele é assumido aqui.
function paraData(iso: string): Date {
  const temFuso = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(temFuso ? iso : `${iso}Z`);
}

export function dataHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  return paraData(iso).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function hora(iso: string | null | undefined): string {
  if (!iso) return "—";
  return paraData(iso).toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Chave de agrupamento por dia, no fuso de quem está olhando. Cortar os 10
// primeiros caracteres do ISO agruparia pela data em UTC, e uma visita às 21h
// de terça cairia na quarta-feira da agenda.
export function chaveDia(iso: string): string {
  const d = paraData(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function diaSemana(iso: string): string {
  return paraData(iso).toLocaleDateString("pt-BR", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
  });
}

// "3h sem resposta" lê melhor que "3.4". Acima de um dia, dias.
export function silencio(horas: number | null | undefined): string {
  if (horas === null || horas === undefined) return "—";
  if (horas < 1) return "agora";
  if (horas < 24) return `${Math.round(horas)}h`;
  return `${Math.round(horas / 24)}d`;
}

// `datetime-local` devolve hora local sem fuso ("2026-09-10T15:00"); o backend
// espera ISO 8601. `new Date` interpreta como local e `toISOString` converte
// para UTC, que é o que a API guarda.
export function paraIso(valorLocal: string): string {
  return new Date(valorLocal).toISOString();
}

// O caminho de volta, para preencher o input de remarcar com a data atual do
// agendamento sem o fuso escorregar uma hora.
export function paraDatetimeLocal(iso: string): string {
  const d = paraData(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

// O `min` do seletor de data: agora, no formato que o `datetime-local` aceita.
//
// Sem ele o navegador permite escolher uma data passada, e o backend aceitava
// com 201. O lead saia do chat convencido de que tinha visita marcada para o
// mes anterior, e o corretor via a visita no passado na agenda.
export function minimoDoSeletor(): string {
  // Arredondado para CIMA, na proxima meia hora fechada, e isso nao e estetica.
  //
  // O `step` de 30 minutos do input conta a partir do `min`, nao da meia-noite:
  // com `min` em 17:35, os horarios validos passariam a ser 18:05, 18:35, e
  // escolher 15:00 de outro dia viraria "stepMismatch". O formulario inteiro
  // ficava invalido e o navegador simplesmente NAO submetia, sem erro visivel,
  // o que e exatamente o tipo de travamento que estamos tirando do caminho.
  const d = new Date();
  d.setSeconds(0, 0);
  const resto = d.getMinutes() % 30;
  d.setMinutes(d.getMinutes() + (resto === 0 ? 0 : 30 - resto));
  return paraDatetimeLocal(d.toISOString());
}

// Os nomes de campo e os valores prontos para ler NÃO moram mais aqui.
//
// Existia uma tabela `CAMPOS` neste arquivo, cópia da `FIELD_LABELS` da Parte
// 2, e as duas já tinham divergido: aqui dizia "orçamento" e lá "Faixa de
// preço". O backend passou a mandar `perfil_campos`, `perfil_label` e os
// `*_label` de cada novidade, então a tela só renderiza. É a mesma regra dos
// outros rótulos do projeto, e o motivo é este: cópia de tabela de tradução
// diverge, sempre.
