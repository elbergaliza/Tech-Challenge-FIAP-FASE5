// Imprime uma pagina em PDF pelo protocolo DevTools do Chrome.
//
//   node pdf.mjs <url-ou-arquivo> <saida.pdf>
//
// O Chrome ja esta no ambiente e imprime PDF com fidelidade total ao que
// renderiza, inclusive fonte da web, cor e quebra de pagina controlada por CSS.
// Nao precisa de wkhtmltopdf, LaTeX nem biblioteca nenhuma.

import { writeFileSync } from "node:fs";

const PORTA = 9222;
const [entrada, saida] = process.argv.slice(2);

async function cdp(ws, method, params = {}) {
  const id = Math.floor(Math.random() * 1e9);
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const prazo = setTimeout(() => reject(new Error(`timeout ${method}`)), 120_000);
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

await cdp(ws, "Page.enable");
await cdp(ws, "Page.navigate", { url: entrada });

// Espera a fonte da web e as imagens assentarem: sem isso o PDF sai com a
// fonte de reserva, e a diferenca aparece na primeira pagina.
await new Promise((r) => setTimeout(r, 5000));
await cdp(ws, "Runtime.evaluate", {
  awaitPromise: true,
  expression: "document.fonts ? document.fonts.ready.then(() => 1) : Promise.resolve(1)",
});
await new Promise((r) => setTimeout(r, 1500));

const pdf = await cdp(ws, "Page.printToPDF", {
  printBackground: true,          // sem isto o fundo e as tarjas somem
  preferCSSPageSize: true,        // quem manda no tamanho e a regra @page
  displayHeaderFooter: true,
  headerTemplate: '<span></span>',
  // O Chrome nao implementa as margin boxes do CSS (@bottom-center), entao o
  // numero de pagina vem por aqui. A regra @page reserva a margem de baixo.
  footerTemplate: `
    <div style="width:100%;font-family:Karla,sans-serif;font-size:7.5pt;
                color:#6b807b;padding:0 17mm;display:flex;
                justify-content:space-between;align-items:center">
      <span>Agente SDR Imobili&#225;rio &#183; Manual do sistema</span>
      <span class="pageNumber"></span>
    </div>`,
  generateTaggedPDF: true,        // PDF acessivel, com estrutura de titulos
  generateDocumentOutline: true,  // sumario navegavel no leitor de PDF
});

writeFileSync(saida, Buffer.from(pdf.data, "base64"));
console.log("pdf:", saida);

await fetch(`http://127.0.0.1:${PORTA}/json/close/${alvo.id}`);
ws.close();
process.exit(0);
