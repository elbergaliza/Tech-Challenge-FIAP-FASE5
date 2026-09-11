// Confere que toda classe de utilitário com token nosso existe no CSS gerado.
//
// Existe por causa de um defeito real: uma troca em massa de `bg-slate-50` para
// `bg-superficie-2` pegou o prefixo de `bg-slate-500` e deixou
// `bg-superficie-20` no código. Classe que não existe não gera erro em lugar
// nenhum: o Tailwind ignora, o TypeScript não olha para dentro de string, o
// build passa, os 107 testes passam, e o defeito só aparece para quem olha a
// tela. No caso, os três pontinhos do "digitando" ficaram sem cor.
//
//   node scripts/classes.mjs        # depois de `vite build`
//
// Roda dentro do `npm run verificar`, logo após o build.

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const TOKENS = [
  "fundo", "superficie", "texto", "suave", "linha", "acento",
  "quente", "morno", "frio", "sucesso", "erro", "cartao",
];

// Só os prefixos que geram utilitário de cor, forma ou sombra a partir de um
// token do tema. Fora disso é nome de variável, comentário ou classe própria.
const PREFIXOS = /^(bg|text|border|ring|divide|fill|stroke|shadow|rounded|from|via|to)(-[a-z0-9]+)+$/;

function arquivos(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    const caminho = join(dir, item.name);
    if (item.isDirectory()) return arquivos(caminho);
    return item.name.endsWith(".tsx") || item.name.endsWith(".ts") ? [caminho] : [];
  });
}

const cssPath = readdirSync("dist/assets").find((n) => n.endsWith(".css"));
if (!cssPath) {
  console.error("Nenhum CSS em dist/assets. Rode `vite build` antes.");
  process.exit(1);
}

const css = readFileSync(join("dist/assets", cssPath), "utf-8");
const fonte = arquivos("src").map((p) => readFileSync(p, "utf-8")).join("\n");

const usadas = new Set(
  (fonte.match(/[A-Za-z0-9:_/[\]().-]+/g) ?? []).filter((t) =>
    TOKENS.some((token) => t.includes(token)),
  ),
);

const faltando = [...usadas]
  .filter((classe) => PREFIXOS.test(classe) && !css.includes(classe))
  .sort();

if (faltando.length > 0) {
  console.error("Classes que o código usa e o CSS não tem:\n");
  for (const classe of faltando) {
    const sufixo = classe.split("-").at(-1);
    const pista = /^\d+$/.test(sufixo) ? "  (sufixo numérico sobrando?)" : "";
    console.error(`  ${classe}${pista}`);
  }
  console.error("\nClasse inexistente não quebra o build: ela some da tela em silêncio.");
  process.exit(1);
}

console.log(`Classes de token conferidas: ${usadas.size} candidatas, nenhuma faltando.`);
