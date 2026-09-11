import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../services/api";

// GET com os três estados que toda tela precisa: carregando, erro e dados.
// `recarregar` existe porque quase toda tela tem um botão de "tentar de novo" e
// porque uma ação (agendar, arquivar) precisa refazer a busca depois.
export function useApi<T>(
  buscar: () => Promise<T>,
  dependencias: unknown[] = [],
): {
  dados: T | null;
  carregando: boolean;
  erro: string | null;
  recarregar: () => void;
} {
  const [dados, setDados] = useState<T | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [gatilho, setGatilho] = useState(0);

  const recarregar = useCallback(() => setGatilho((n) => n + 1), []);

  useEffect(() => {
    let vivo = true;
    setCarregando(true);
    setErro(null);

    buscar()
      .then((resultado) => {
        if (!vivo) return;
        setDados(resultado);
      })
      .catch((falha: unknown) => {
        if (!vivo) return;
        setErro(
          falha instanceof ApiError ? falha.message : "Erro inesperado ao buscar dados.",
        );
      })
      .finally(() => {
        if (vivo) setCarregando(false);
      });

    return () => {
      vivo = false;
    };
    // `buscar` é recriado a cada render pelas telas; as dependências reais são
    // os filtros que a tela passa, mais o gatilho de recarga.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencias, gatilho]);

  return { dados, carregando, erro, recarregar };
}
