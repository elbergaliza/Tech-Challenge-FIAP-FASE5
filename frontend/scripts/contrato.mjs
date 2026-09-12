// Teste de contrato: bate na API de verdade e confere que cada payload tem o
// que o front assume em `src/types.ts`.
//
// Os testes de componente usam dublês, então eles continuam verdes se o backend
// renomear um campo. É este script que pega essa deriva.
//
//   node scripts/contrato.mjs                 # contra http://localhost:8000
//   API_URL=http://outro:8000 node scripts/contrato.mjs
//
// Ele ESCREVE no banco: cria um lead pelo chat, agenda, remarca e apaga tudo no
// fim. Aponte para um banco de desenvolvimento, nunca para dado real. A rota de
// chat também gasta uma chamada do Gemini, se houver chave configurada.

const BASE = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");

let passou = 0;
// Fica no escopo do módulo para a limpeza do `finally` alcançar.
let leadCriado = null;
const falhas = [];
const avisos = [];

function ok(nome) {
  passou += 1;
  console.log(`  ok   ${nome}`);
}

function falhar(nome, detalhe) {
  falhas.push({ nome, detalhe });
  console.log(`  FALHA ${nome}\n        ${detalhe}`);
}

function conferir(nome, condicao, detalhe = "") {
  if (condicao) ok(nome);
  else falhar(nome, detalhe || "condição falsa");
}

// Presença E tipo: um campo que existe mas veio como null onde o front espera
// número quebra a tela do mesmo jeito que um campo ausente.
function conferirCampos(nome, objeto, esperado) {
  const problemas = [];

  for (const [campo, tipo] of Object.entries(esperado)) {
    if (!(campo in objeto)) {
      problemas.push(`falta "${campo}"`);
      continue;
    }

    const valor = objeto[campo];
    const real = Array.isArray(valor) ? "array" : valor === null ? "null" : typeof valor;
    const aceitos = tipo.split("|");

    if (!aceitos.includes(real)) {
      problemas.push(`"${campo}" veio ${real}, esperado ${tipo}`);
    }
  }

  conferir(nome, problemas.length === 0, problemas.join("; "));
}

async function pedir(caminho, opcoes = {}) {
  const resposta = await fetch(BASE + caminho, {
    method: opcoes.metodo ?? "GET",
    headers: opcoes.corpo ? { "Content-Type": "application/json" } : undefined,
    body: opcoes.corpo ? JSON.stringify(opcoes.corpo) : undefined,
  });

  const texto = await resposta.text();
  let dados = null;
  try {
    dados = texto ? JSON.parse(texto) : null;
  } catch {
    dados = texto;
  }

  return { status: resposta.status, dados };
}

function secao(titulo) {
  console.log(`\n${titulo}`);
}

// O SQLite descarta o tzinfo e a API devolve ISO sem fuso. O front compensa em
// `lib/formatar.ts`; aqui isso é registrado como aviso, para o dia em que o
// backend passar a mandar o offset e a compensação puder sair.
function conferirData(nome, valor) {
  if (typeof valor !== "string" || Number.isNaN(Date.parse(valor))) {
    falhar(nome, `não é data ISO: ${JSON.stringify(valor)}`);
    return;
  }

  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(valor)) {
    avisos.push(`${nome}: ISO sem fuso ("${valor}"), o front assume UTC`);
  }
  ok(nome);
}

async function main() {
  console.log(`Contrato da API em ${BASE}`);

  // ---------------------------------------------------------------- saúde
  secao("GET /health");
  let saude;
  try {
    saude = await pedir("/health");
  } catch (erro) {
    console.error(
      `\nAPI fora do ar em ${BASE}: nada foi verificado.\n` +
        `Suba o backend com "python backend/run.py" e rode de novo.\n(${erro.message})`,
    );
    process.exit(1);
  }

  conferir("responde 200", saude.status === 200, `status ${saude.status}`);
  conferirCampos("payload de saúde", saude.dados, {
    status: "string",
    banco: "string",
    ia: "object",
  });
  conferirCampos("bloco de IA", saude.dados.ia, {
    agente: "string",
    llm: "string",
    rag: "object",
    parte2: "boolean",
  });

  const emMock = saude.dados.ia.agente === "mock";
  console.log(`  (agente: ${saude.dados.ia.agente}${emMock ? ", sem chave" : ""})`);

  // ------------------------------------------------------------------ chat
  secao("POST /chat");
  const primeiro = await pedir("/chat", {
    metodo: "POST",
    corpo: {
      lead_id: null,
      mensagem: "quero alugar um apartamento de 2 quartos em Botafogo",
      consentimento: true,
    },
  });

  conferir("responde 200", primeiro.status === 200, `status ${primeiro.status}`);
  if (primeiro.status !== 200) {
    console.error("\nSem turno de chat não há como seguir: interrompendo.");
    process.exit(1);
  }

  const turno = primeiro.dados;
  leadCriado = turno.lead_id;
  const leadId = leadCriado;

  conferirCampos("payload do turno", turno, {
    lead_id: "string",
    resposta: "string",
    status: "string",
    score: "number",
    temperatura: "string",
    temperatura_label: "string",
    perfil: "object",
    perfil_label: "object",
    perfil_campos: "object",
    novidades: "array",
    imoveis: "array",
    proxima_acao: "string|null",
    origem: "string",
    sugerir_agendamento: "boolean",
  });

  conferir(
    "cria lead sem lead_id, como um visitante anônimo",
    typeof leadId === "string" && leadId.length > 0,
    `lead_id: ${JSON.stringify(leadId)}`,
  );
  conferir(
    "temperatura é uma das três da escala",
    ["HOT", "WARM", "COLD"].includes(turno.temperatura),
    `veio "${turno.temperatura}"`,
  );
  conferir(
    "o perfil vem também pronto para ler, sem enum cru na tela",
    Object.keys(turno.perfil).every((campo) => campo in (turno.perfil_label || {})),
    JSON.stringify(turno.perfil_label),
  );
  conferir(
    "e a intenção sai traduzida",
    !turno.perfil.intent || turno.perfil_label.intent !== turno.perfil.intent,
    `perfil.intent=${turno.perfil.intent} label=${turno.perfil_label?.intent}`,
  );
  conferir(
    "temperatura_label já vem em português",
    ["QUENTE", "MORNO", "FRIO"].includes(turno.temperatura_label),
    `veio "${turno.temperatura_label}"`,
  );

  if (turno.novidades.length > 0) {
    conferirCampos("novidade", turno.novidades[0], {
      field: "string",
      to: "string|null",
      kind: "string",
      field_label: "string",
      to_label: "string|null",
      from_label: "string|null",
    });
    conferir(
      "o chip tem nome de campo em português",
      turno.novidades.every((n) => n.field_label && n.field_label !== n.field),
      JSON.stringify(turno.novidades.map((n) => [n.field, n.field_label])),
    );
    conferir(
      "kind da novidade é new ou correction",
      turno.novidades.every((n) => ["new", "correction"].includes(n.kind)),
      JSON.stringify(turno.novidades.map((n) => n.kind)),
    );
  } else {
    avisos.push("o turno não trouxe novidades: chips de memória não verificados");
  }

  if (turno.imoveis.length > 0) {
    conferirCampos("imóvel sugerido pelo RAG", turno.imoveis[0], {
      id: "string",
      title: "string",
      neighborhood: "string",
      deal_type: "string",
      price: "number",
      bedrooms: "number",
      reason: "string|null",
    });
    conferir(
      "o imóvel vem com o porquê da escolha",
      typeof turno.imoveis[0].reason === "string" && turno.imoveis[0].reason.length > 0,
      "reason vazio: o card perde o que faz o RAG parecer inteligente",
    );
  } else {
    avisos.push("o turno não trouxe imóveis: cards do RAG não verificados");
  }

  secao("POST /chat com 422");
  const vazio = await pedir("/chat", { metodo: "POST", corpo: { mensagem: "" } });
  conferir("mensagem vazia é 422", vazio.status === 422, `status ${vazio.status}`);
  conferir(
    "o detail do 422 é lista, e o front trata como lista",
    Array.isArray(vazio.dados?.detail),
    `detail veio ${typeof vazio.dados?.detail}`,
  );

  secao("GET /chat/{id}/historico");
  const historico = await pedir(`/chat/${encodeURIComponent(leadId)}/historico`);
  conferir("responde 200", historico.status === 200, `status ${historico.status}`);
  const mensagens = historico.dados?.mensagens;
  conferir(
    "devolve as mensagens do turno",
    Array.isArray(mensagens) && mensagens.length >= 2,
    `veio ${mensagens?.length} mensagem(ns)`,
  );

  // A rota devolve o ESTADO da conversa, não só a lista: é o que permite o
  // painel "O que já entendi" voltar preenchido depois de um F5, em vez de
  // dizer "manda a primeira mensagem" para quem tem uma conversa inteira atrás.
  conferirCampos("estado da conversa reaberta", historico.dados, {
    status: "string",
    score: "number",
    temperatura: "string",
    temperatura_label: "string",
    perfil: "object",
    perfil_label: "object",
    perfil_campos: "object",
    sugerir_agendamento: "boolean",
  });

  if (Array.isArray(mensagens) && mensagens.length > 0) {
    conferirCampos("mensagem do histórico", mensagens[0], {
      id: "number",
      papel: "string",
      conteudo: "string",
      criado_em: "string",
      imoveis: "array",
    });
    conferir(
      "papel é user, assistant ou followup",
      mensagens.every((m) => ["user", "assistant", "followup"].includes(m.papel)),
      JSON.stringify(mensagens.map((m) => m.papel)),
    );
    conferirData("criado_em é ISO", mensagens[0].criado_em);

    // O front descarta novidades/imoveis ao reabrir a conversa porque esta
    // rota não os carrega. Se algum dia carregar, o Chat pode voltar a
    // reidratar os chips.
    conferir(
      "histórico não carrega novidades (o Chat conta com isso)",
      !("novidades" in mensagens[0]),
      "passou a carregar: dá para reidratar os chips ao reabrir",
    );
  }

  secao("GET /chat/{id}/historico de lead inexistente");
  const semLead = await pedir("/chat/lead-nao-existe/historico");
  conferir(
    "404 é o sinal de lead_id inválido que o Chat usa para se limpar",
    semLead.status === 404,
    `status ${semLead.status}`,
  );

  // ----------------------------------------------------------------- leads
  secao("GET /leads");
  const lista = await pedir("/leads?ordenar_por=score&limite=50");
  conferir("responde 200", lista.status === 200, `status ${lista.status}`);
  conferir("devolve array", Array.isArray(lista.dados), typeof lista.dados);

  if (Array.isArray(lista.dados) && lista.dados.length > 0) {
    conferirCampos("lead da lista", lista.dados[0], {
      id: "string",
      nome: "string|null",
      telefone: "string|null",
      status: "string",
      score: "number",
      temperatura: "string",
      total_mensagens: "number",
      horas_sem_resposta: "number|null",
      intencao_label: "string|null",
      urgencia_label: "string|null",
      faixa_preco_label: "string|null",
      temperatura_label: "string|null",
      status_label: "string|null",
      criado_em: "string",
    });

    const scores = lista.dados.map((l) => l.score);
    conferir(
      "ordenado por score desc, que é a ordem da tela",
      scores.every((s, i) => i === 0 || scores[i - 1] >= s),
      JSON.stringify(scores),
    );
  }

  secao("GET /leads com filtro");
  for (const filtro of ["temperatura=HOT", "status=QUALIFICADO", "intencao=RENT", "busca=zzz"]) {
    const resposta = await pedir(`/leads?${filtro}`);
    conferir(`?${filtro} responde 200`, resposta.status === 200, `status ${resposta.status}`);
  }

  secao("PATCH /leads/{id} é parcial");
  await pedir(`/leads/${leadId}`, {
    metodo: "PATCH",
    corpo: { nome: "Contrato Teste", telefone: "21 90000-0000" },
  });
  const soStatus = await pedir(`/leads/${leadId}`, {
    metodo: "PATCH",
    corpo: { status: "QUALIFICADO" },
  });

  conferir("responde 200", soStatus.status === 200, `status ${soStatus.status}`);
  conferir(
    "mandar só o status não apaga o telefone",
    soStatus.dados?.telefone === "21 90000-0000",
    `telefone virou ${JSON.stringify(soStatus.dados?.telefone)}`,
  );
  conferir(
    "o status mudou",
    soStatus.dados?.status === "QUALIFICADO",
    `status ${soStatus.dados?.status}`,
  );

  secao("GET /leads/{id}");
  const detalhe = await pedir(`/leads/${leadId}`);
  conferir("responde 200", detalhe.status === 200, `status ${detalhe.status}`);
  conferirCampos("detalhe do lead", detalhe.dados, {
    mensagens: "array",
    agendamentos: "array",
    resumo_ia: "object|null",
  });

  if (detalhe.dados?.resumo_ia) {
    conferirCampos("resumo para o corretor", detalhe.dados.resumo_ia, {
      summary: "string",
      next_action: "string",
      alerts: "array",
      buying_signals: "array",
      objections: "array",
      score: "number",
      source: "string",
    });
    conferir(
      "o resumo padrão é o heurístico, que não gasta cota",
      detalhe.dados.resumo_ia.source === "heuristic",
      `source veio "${detalhe.dados.resumo_ia.source}"`,
    );
    conferir(
      "traz os fatores do score, que a tela mostra como auditoria",
      Array.isArray(detalhe.dados.resumo_ia.factors),
      "sem factors: o número do score fica sem explicação na tela",
    );
  } else {
    avisos.push("lead sem resumo_ia: o card do corretor não foi verificado");
  }

  secao("GET /leads/{id}/exportar (LGPD)");
  const exportado = await pedir(`/leads/${leadId}/exportar`);
  conferir("responde 200", exportado.status === 200, `status ${exportado.status}`);
  conferir(
    "devolve JSON com conteúdo",
    exportado.dados && typeof exportado.dados === "object",
    typeof exportado.dados,
  );

  // ------------------------------------------------------------ agendamento
  secao("POST /leads/{id}/schedule");
  const imovelId = turno.imoveis[0]?.id ?? null;
  const daquiUmaSemana = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();
  const agendado = await pedir(`/leads/${leadId}/schedule`, {
    metodo: "POST",
    corpo: {
      data_hora: daquiUmaSemana,
      tipo: "VISITA",
      imovel_id: imovelId,
      corretor: "Contrato",
    },
  });

  conferir("responde 201", agendado.status === 201, `status ${agendado.status}`);
  if (agendado.status === 201) {
    conferirCampos("agendamento", agendado.dados, {
      id: "number",
      lead_id: "string",
      tipo: "string",
      status: "string",
      data_hora: "string",
      lead_nome: "string|null",
      imovel_titulo: "string|null",
    });
    conferirData("data_hora é ISO", agendado.dados.data_hora);
    conferir(
      "vem desnormalizado, sem a tela precisar de duas chamadas",
      !imovelId || typeof agendado.dados.imovel_titulo === "string",
      "imovel_titulo nulo com imovel_id informado",
    );

    const depois = await pedir(`/leads/${leadId}`);
    conferir(
      "agendar promove o lead para AGENDADO",
      depois.dados?.status === "AGENDADO",
      `status ${depois.dados?.status}`,
    );

    secao("PATCH /schedule/{id}");
    const remarcado = await pedir(`/schedule/${agendado.dados.id}`, {
      metodo: "PATCH",
      corpo: { data_hora: new Date(Date.now() + 9 * 24 * 3600 * 1000).toISOString() },
    });
    conferir("remarcar responde 200", remarcado.status === 200, `status ${remarcado.status}`);

    const cancelado = await pedir(`/schedule/${agendado.dados.id}`, {
      metodo: "PATCH",
      corpo: { status: "CANCELADO" },
    });
    conferir("cancelar responde 200", cancelado.status === 200, `status ${cancelado.status}`);

    // A premissa desta verificação é ser a ÚNICA visita do lead, então ela vem
    // antes de o script criar os outros dois agendamentos abaixo.
    const aposCancelar = await pedir(`/leads/${leadId}`);
    conferir(
      "cancelar a única visita devolve o lead para QUALIFICADO",
      aposCancelar.dados?.status === "QUALIFICADO",
      `status ${aposCancelar.dados?.status}: o lead sairia das listas de atenção`,
    );

    // Dois agendamentos criados FORA de ordem cronológica de propósito: a
    // agenda da tela é desenhada na ordem em que a API devolve, então isto
    // prova que ela ordena, e não que os itens saíram na ordem de insercão.
    // Antes a verificação dependia de o banco já ter dois na janela, e
    // simplesmente não rodava numa base limpa: verificação que desaparece sem
    // avisar é pior que verificação que falha.
    const doisDepois = [12, 10].map((dias) =>
      new Date(Date.now() + dias * 24 * 3600 * 1000).toISOString(),
    );
    for (const quando of doisDepois) {
      await pedir(`/leads/${leadId}/schedule`, {
        metodo: "POST",
        corpo: { data_hora: quando, tipo: "REUNIAO", corretor: "Contrato" },
      });
    }

    secao("GET /schedule");
    const agenda = await pedir("/schedule?proximos_dias=14");
    conferir("responde 200", agenda.status === 200, `status ${agenda.status}`);
    conferir("devolve array", Array.isArray(agenda.dados), typeof agenda.dados);

    // Sem filtro de status, a janela traz o cancelado também, e isso é o certo:
    // a tela mostra o cancelado com o selo, para o corretor saber que a visita
    // existiu. Quem conta como compromisso de pé é o AGENDADO.
    const doLead = (agenda.dados ?? []).filter((a) => a.lead_id === leadId);
    const ativos = doLead.filter((a) => a.status === "AGENDADO");

    conferir(
      "traz os dois agendamentos ativos do lead",
      ativos.length === 2,
      `veio ${ativos.length} ativo(s) de ${doLead.length} do lead`,
    );
    conferir(
      "mantém o cancelado na janela, com o status",
      doLead.some((a) => a.status === "CANCELADO"),
      JSON.stringify(doLead.map((a) => a.status)),
    );

    const instantes = doLead.map((a) => Date.parse(a.data_hora));
    conferir(
      "ordena da próxima para a última, mesmo inseridos fora de ordem",
      instantes.length > 1 && instantes.every((t, i) => i === 0 || instantes[i - 1] <= t),
      JSON.stringify(doLead.map((a) => a.data_hora)),
    );
  }

  // --------------------------------------------------------------- imóveis
  secao("GET /imoveis");
  const imoveis = await pedir("/imoveis?limite=2");
  conferir("responde 200", imoveis.status === 200, `status ${imoveis.status}`);
  conferirCampos("página de imóveis", imoveis.dados, {
    total: "number",
    limit: "number",
    offset: "number",
    items: "array",
  });

  if (imoveis.dados?.items?.length > 0) {
    conferirCampos("imóvel do catálogo", imoveis.dados.items[0], {
      id: "string",
      title: "string",
      deal_type: "string",
      property_type: "string",
      price: "number",
      bedrooms: "number",
      bathrooms: "number",
      parking: "number",
      area_m2: "number",
      neighborhood: "string",
      zone: "string",
      features: "array",
      status: "string",
      condo_fee: "number|null",
      annual_yield_pct: "number|null",
    });
    conferir(
      "total é o de antes da paginação, que a tela usa para as páginas",
      imoveis.dados.total >= imoveis.dados.items.length,
      `total ${imoveis.dados.total} < itens ${imoveis.dados.items.length}`,
    );
  }

  secao("GET /imoveis/filtros");
  const filtros = await pedir("/imoveis/filtros");
  conferir("responde 200", filtros.status === 200, `status ${filtros.status}`);
  conferirCampos("valores dos selects", filtros.dados, {
    deal_type: "array",
    property_type: "array",
    neighborhood: "array",
    zone: "array",
    bedrooms: "array",
    preco_min: "number",
    preco_max: "number",
  });

  secao("GET /imoveis/{id} inexistente");
  const semImovel = await pedir("/imoveis/IMV-9999999");
  conferir("responde 404", semImovel.status === 404, `status ${semImovel.status}`);

  // ------------------------------------------------------------- dashboard
  secao("GET /dashboard/summary");
  const resumo = await pedir("/dashboard/summary");
  conferir("responde 200", resumo.status === 200, `status ${resumo.status}`);
  conferirCampos("resumo do dashboard", resumo.dados, {
    total_leads: "number",
    leads_quentes: "number",
    leads_mornos: "number",
    leads_frios: "number",
    qualificados: "number",
    aguardando_followup: "number",
    agendamentos_proximos: "number",
    agendamentos_hoje: "number",
    total_mensagens: "number",
    total_imoveis: "number",
    taxa_qualificacao: "number",
    score_medio: "number",
    por_status: "array",
    por_intencao: "array",
    por_regiao: "array",
    ultimos_leads: "array",
  });

  for (const chave of ["por_status", "por_intencao", "por_regiao"]) {
    const itens = resumo.dados?.[chave] ?? [];
    if (itens.length === 0) continue;

    conferirCampos(`item de ${chave}`, itens[0], {
      chave: "string",
      label: "string",
      total: "number",
    });
    const totais = itens.map((i) => i.total);
    conferir(
      `${chave} ordenado por total desc, como a barra desenha`,
      totais.every((t, i) => i === 0 || totais[i - 1] >= t),
      JSON.stringify(totais),
    );
  }

  secao("GET /dashboard/followups");
  const followups = await pedir("/dashboard/followups");
  conferir("responde 200", followups.status === 200, `status ${followups.status}`);
  conferir("devolve array", Array.isArray(followups.dados), typeof followups.dados);

  if (Array.isArray(followups.dados) && followups.dados.length > 0) {
    conferirCampos("follow-up pendente", followups.dados[0], {
      lead_id: "string",
      lead_nome: "string|null",
      horas_de_silencio: "number",
      tentativa: "number",
      motivo: "string",
    });
    conferir(
      "sem ?com_texto, o texto sugerido não vem (e não gasta cota)",
      followups.dados[0].texto_sugerido === null,
      "veio texto sem pedir: a lista estaria gastando LLM por lead",
    );
  } else {
    avisos.push("nenhum follow-up pendente: a seção de atenção não foi verificada");
  }

  // ---------------------------------------------------------------- limpeza
  secao("DELETE /leads/{id} (LGPD, e limpeza deste teste)");
  const apagado = await pedir(`/leads/${leadId}`, { metodo: "DELETE" });
  conferir("responde 204 sem corpo", apagado.status === 204, `status ${apagado.status}`);

  const sumiu = await pedir(`/leads/${leadId}`);
  conferir("o lead deixou de existir", sumiu.status === 404, `status ${sumiu.status}`);
  leadCriado = null;

  // ---------------------------------------------------------------- relatório
  console.log(`\n${"=".repeat(66)}`);
  console.log(`${passou} verificações passaram, ${falhas.length} falharam`);

  if (avisos.length > 0) {
    console.log(`\nAvisos (não falham a suíte):`);
    for (const aviso of avisos) console.log(`  - ${aviso}`);
  }

  if (falhas.length > 0) {
    console.log(`\nFalhas:`);
    for (const falha of falhas) console.log(`  - ${falha.nome}: ${falha.detalhe}`);
    process.exit(1);
  }
}

// Se a suíte morrer no meio (assert que estoura, rede que cai, pipe fechado por
// um `| head`), o lead de teste ficaria no banco com nome de gente. A limpeza
// roda no `finally`, e o EPIPE deixa de derrubar o processo antes dela.
process.stdout.on("error", (erro) => {
  if (erro.code !== "EPIPE") throw erro;
});

main()
  .catch((erro) => {
    console.error(`\nErro inesperado no teste de contrato: ${erro.stack}`);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (!leadCriado) return;
    try {
      await pedir(`/leads/${leadCriado}`, { metodo: "DELETE" });
      console.error(`\n(limpeza: lead de teste ${leadCriado} apagado)`);
    } catch {
      console.error(`\n(atenção: o lead de teste ${leadCriado} ficou no banco)`);
    }
  });
