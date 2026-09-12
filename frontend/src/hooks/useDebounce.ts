import { useEffect, useState } from "react";

// Evita uma requisição por tecla em campos de busca.
export function useDebounce<T>(valor: T, atrasoMs = 350): T {
  const [atrasado, setAtrasado] = useState(valor);

  useEffect(() => {
    const id = setTimeout(() => setAtrasado(valor), atrasoMs);
    return () => clearTimeout(id);
  }, [valor, atrasoMs]);

  return atrasado;
}
