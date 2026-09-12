// Captura telas para o manual, com recorte por elemento.
//
//   node capturar.mjs <rota> <saida.png> [opcoes]
//
//   --largura=1440 --altura=900     viewport
//   --seletor="header"              recorta so aquele elemento
//   --margem=12                     folga em volta do recorte, em px
//   --lead=lead-xxxx                semeia o localStorage antes de carregar
//   --rolar=400                     rola a pagina antes de capturar
//   --esperar=6000                  tempo de espera antes do disparo
//   --clicar="texto do botao"       clica num botao pelo texto antes
//   --digitar="frase"               escreve no campo do chat e envia
//   --celular                       emula aparelho de toque

import { writeFileSync } from "node:fs";

const PORTA = 9222;
const BASE = "http://localhost:5173";
const [rota, saida, ...resto] = process.argv.slice(2);

const opt = (nome, padrao) => {
  const achado = resto.find((r) => r.startsWith(`--${nome}=`));
  return achado ? achado.slice(nome.length + 3) : padrao;
};
const tem = (nome) => resto.includes(`--${nome}`);

async function cdp(ws, method, params = {}) {
  const id = Math.floor(Math.random() * 1e9);
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const prazo = setTimeout(() => reject(new Error(`timeout ${method}`)), 60_000);
    function ouvir(e) {
      const m = JSON.parse(e.data);
      if (m.id !== id) return;
      clearTimeout(prazo);
      ws.removeEventListener("message", ouvir);
      m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result);
    }
    ws.addEventListener("message", ouvir);
  });
}

const alvo = await (await fetch(`http://127.0.0.1:${PORTA}/json/new?about:blank`, {
  method: "PUT",
})).json();
const ws = new WebSocket(alvo.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));

const largura = Number(opt("largura", 1440));
const altura = Number(opt("altura", 900));

await cdp(ws, "Emulation.setDeviceMetricsOverride", {
  width: largura, height: altura, deviceScaleFactor: 2, mobile: tem("celular"),
});
if (tem("celular")) {
  await cdp(ws, "Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await cdp(ws, "Emulation.setEmulatedMedia", {
    features: [{ name: "pointer", value: "coarse" }, { name: "hover", value: "none" }],
  });
}

const lead = opt("lead", "");
if (lead) {
  await cdp(ws, "Page.addScriptToEvaluateOnNewDocument", {
    source: `try { localStorage.setItem("lead_id", ${JSON.stringify(lead)}); } catch (e) {}`,
  });
}

await cdp(ws, "Page.enable");
// Rota comecando com http e endereco completo: serve para capturar o /docs
// do backend, que mora noutra porta.
await cdp(ws, "Page.navigate", { url: /^(https?|file):/.test(rota) ? rota : BASE + rota });
await new Promise((r) => setTimeout(r, Number(opt("esperar", 6000))));

const digitar = opt("digitar", "");
if (digitar) {
  await cdp(ws, "Runtime.evaluate", {
    awaitPromise: true,
    expression: `(async () => {
      const campo = document.querySelector("input[placeholder*='mensagem']");
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value").set;
      setter.call(campo, ${JSON.stringify(digitar)});
      campo.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      campo.form.requestSubmit();
      await new Promise(r => setTimeout(r, 28000));
    })()`,
  });
}

const clicar = opt("clicar", "");
if (clicar) {
  await cdp(ws, "Runtime.evaluate", {
    awaitPromise: true,
    expression: `(async () => {
      const alvo = [...document.querySelectorAll("button, a")]
        .find(e => e.textContent.trim().includes(${JSON.stringify(clicar)}));
      if (alvo) alvo.click();
      await new Promise(r => setTimeout(r, 3000));
    })()`,
  });
}

const rolar = Number(opt("rolar", 0));
if (rolar) {
  await cdp(ws, "Runtime.evaluate", { expression: `window.scrollTo(0, ${rolar})` });
  await new Promise((r) => setTimeout(r, 900));
}

// `--contendo="Meus dados"` acha a section que TEM aquele texto. CSS nao
// seleciona por conteudo, e recortar um cartao pelo titulo e o jeito mais
// direto de pedir um recorte num manual.
let seletor = opt("seletor", "");
const contendo = opt("contendo", "");
if (contendo) {
  const { result } = await cdp(ws, "Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const alvo = [...document.querySelectorAll("section, form, div")]
        .filter(e => e.textContent.includes(${JSON.stringify(contendo)}))
        .sort((a, b) => a.textContent.length - b.textContent.length)[0];
      if (!alvo) return null;
      alvo.setAttribute("data-recorte", "1");
      return "1";
    })()`,
  });
  if (!result.value) {
    console.error("nada contem:", contendo);
    process.exit(1);
  }
  seletor = "[data-recorte='1']";
}

let clip;
if (seletor) {
  const { result } = await cdp(ws, "Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(seletor)});
      if (!el) return null;
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return JSON.stringify({ x: r.left, y: r.top, width: r.width, height: r.height });
    })()`,
  });
  if (!result.value) {
    console.error("seletor nao encontrou nada:", seletor);
    process.exit(1);
  }
  await new Promise((r) => setTimeout(r, 700));
  const { result: r2 } = await cdp(ws, "Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(seletor)});
      const r = el.getBoundingClientRect();
      return JSON.stringify({ x: r.left, y: r.top, width: r.width, height: r.height });
    })()`,
  });
  const caixa = JSON.parse(r2.value);
  const m = Number(opt("margem", 10));

  // Quando o elemento comeca acima da dobra, `y` vem negativo. Prender o
  // recorte em 0 sem descontar a diferenca da altura cortava o topo do alvo e
  // sobrava espaco embaixo, que foi como a primeira captura do seletor de data
  // saiu: com meia bolha de conversa em cima.
  const y = caixa.y - m;
  const perdido = y < 0 ? -y : 0;
  clip = {
    x: Math.max(0, caixa.x - m),
    y: Math.max(0, y),
    width: Math.min(largura, caixa.width + m * 2),
    height: caixa.height + m * 2 - perdido,
    scale: 1,
  };
}

const tiro = await cdp(ws, "Page.captureScreenshot",
  clip ? { format: "png", clip, captureBeyondViewport: true } : { format: "png" });
writeFileSync(saida, Buffer.from(tiro.data, "base64"));
console.log("ok", saida.split(/[\\/]/).pop(),
            clip ? `(recorte ${Math.round(clip.width)}x${Math.round(clip.height)})` : "");

await fetch(`http://127.0.0.1:${PORTA}/json/close/${alvo.id}`);
ws.close();
process.exit(0);
