// Tema: escuro, claro ou o que o sistema pedir.
//
// A escolha vive no localStorage e é aplicada duas vezes: pelo script do
// `index.html`, antes da primeira pintura, e por aqui quando o usuário troca.
// As duas leem a mesma chave, então não existe um estado em que o <html> e o
// controle da tela discordem.

export type Tema = "escuro" | "claro" | "sistema";

const CHAVE = "sdr-tema";

export function lerTema(): Tema {
  try {
    const salvo = localStorage.getItem(CHAVE);
    if (salvo === "claro" || salvo === "sistema") return salvo;
  } catch {
    // Navegação privada pode barrar o localStorage: o padrão resolve.
  }
  return "escuro";
}

export function aplicarTema(tema: Tema): void {
  const raiz = document.documentElement;

  // "sistema" é a ausência do atributo: é assim que o `prefers-color-scheme`
  // do CSS volta a mandar.
  if (tema === "sistema") delete raiz.dataset.theme;
  else raiz.dataset.theme = tema === "claro" ? "light" : "dark";

  try {
    localStorage.setItem(CHAVE, tema);
  } catch {
    // Sem persistência a escolha vale só para esta aba, e isso é aceitável.
  }
}
