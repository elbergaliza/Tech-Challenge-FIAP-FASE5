import { useEffect, useState } from "react";

import { api } from "../services/api";
import type { SaudeApi } from "../types";

// Duas cadências, porque as duas situações são diferentes.
//
// Com a API no ar, reconsultar é só para o selo não mentir se o backend cair,
// e um minuto é de sobra: a 20 segundos, o terminal de quem está com o uvicorn
// aberto vira uma parede de linhas de `/health` e o log deixa de ser legível.
//
// Com a API fora, o interesse se inverte: quem acabou de subir o backend quer
// ver o selo voltar sem recarregar a página.
const NO_AR_MS = 60_000;
const FORA_MS = 10_000;

// `null` = ainda verificando, `false` = API fora do ar.
export function useSaude(): SaudeApi | false | null {
  const [saude, setSaude] = useState<SaudeApi | false | null>(null);

  useEffect(() => {
    let vivo = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function agendar(estado: SaudeApi | false) {
      // Aba escondida não precisa de selo atualizado: ninguém está olhando, e
      // a consulta volta assim que ela reaparece.
      if (!vivo || document.hidden) return;
      timer = setTimeout(consultar, estado === false ? FORA_MS : NO_AR_MS);
    }

    function consultar() {
      api
        .saude()
        .then((dados) => {
          if (!vivo) return;
          setSaude(dados);
          agendar(dados);
        })
        .catch(() => {
          if (!vivo) return;
          setSaude(false);
          agendar(false);
        });
    }

    function aoVoltar() {
      if (document.hidden) {
        clearTimeout(timer);
        return;
      }
      // Voltou para a aba: o estado pode estar velho, então confere já.
      clearTimeout(timer);
      consultar();
    }

    consultar();
    document.addEventListener("visibilitychange", aoVoltar);

    return () => {
      vivo = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", aoVoltar);
    };
  }, []);

  return saude;
}
