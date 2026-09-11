"""Testes da extração da Parte 1.

    .venv\\Scripts\\python.exe -m pytest ai-core/tests -q

Nasceram de um defeito que derrubou uma conversa de verdade: um lead investidor
disse "800 mil" e, no turno seguinte, o texto acumulado (contexto + fala
anterior + mensagem) tinha "800k" duas vezes. A extração estourou com
IndexError, e o backend devolveu "tive um problema técnico" mesmo com a
resposta do Gemini já paga e pronta.

Não precisa de chave: só o `extrair_preco` e amigos, que são regex puro.
"""

import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(AQUI), "src")
sys.path.insert(0, SRC)

# O `agent` exige GEMINI_API_KEY no import. Numa suíte que não fala com a API,
# um valor qualquer serve: nenhuma chamada é feita aqui.
os.environ.setdefault("GEMINI_API_KEY", "chave-de-teste")

from agent import (  # noqa: E402
    extrair_intencao,
    extrair_nome,
    extrair_preco,
    extrair_quartos,
    extrair_regiao,
    extrair_tipo_imovel,
    extrair_urgencia,
)


class TestExtrairPreco:

    def test_valor_unico(self):
        assert extrair_preco("tenho 800k") == "800k"

    def test_faixa_com_dois_valores(self):
        assert extrair_preco("entre 500k e 800k") == "500k-800k"

    def test_faixa_sai_na_ordem_em_que_foi_dita(self):
        # Com `set`, a ordem era arbitrária: "entre 500k e 800k" virava
        # "800k-500k" em boa parte das execuções, e ninguém percebia porque o
        # valor continuava "parecendo" certo.
        for _ in range(20):
            assert extrair_preco("entre 500k e 800k") == "500k-800k"

    def test_valor_repetido_nao_estoura(self):
        # O defeito original. O texto acumulado da conversa repete o mesmo
        # valor com facilidade, e o IndexError subia pela extração inteira,
        # matando o turno depois de a resposta já ter sido paga.
        assert extrair_preco("ticket de 800k, retorno sobre 800k") == "800k"

    def test_sem_valor(self):
        assert extrair_preco("quero um apartamento") == "undefined"


class TestExtrairIntencao:
    """Investir decide quando aparece junto de comprar ou alugar."""

    def test_investir_para_alugar_e_investimento(self):
        # A frase classica do investidor. Antes contava dois pontos para
        # aluguel ("alugar" e "para alugar") contra um de investimento, e o
        # investidor entrava no funil como locatario.
        assert extrair_intencao("quero investir em imoveis para alugar") == "INVESTIMENTO"

    def test_comprar_para_investir_e_investimento(self):
        assert extrair_intencao("quero comprar para investir") == "INVESTIMENTO"

    def test_rentabilidade_sozinha_basta(self):
        assert extrair_intencao("busco rentabilidade") == "INVESTIMENTO"

    def test_renda_sozinha_nao_faz_investidor(self):
        # "renda" saiu da lista: quem diz "tenho renda de 5 mil" e um
        # locatario informando salario.
        assert extrair_intencao("tenho renda de 5 mil e quero alugar") == "ALUGUEL"

    def test_alugar_e_comprar_continuam_funcionando(self):
        assert extrair_intencao("quero alugar um apartamento") == "ALUGUEL"
        assert extrair_intencao("quero comprar uma casa") == "COMPRA"

    def test_sem_sinal_nenhum(self):
        assert extrair_intencao("oi, tudo bem?") == "undefined"


class TestOutrasExtracoes:

    def test_quartos(self):
        assert extrair_quartos("preciso de 3 quartos") == "3"

    def test_regiao(self):
        assert extrair_regiao("quero em botafogo") == "Botafogo"

    def test_urgencia_alta(self):
        assert extrair_urgencia("preciso urgente") == "alta"


class TestEscalaDoValor:
    """"1 milhao" virava R$ 1.000, e o RAG respondia "13800% acima do orcamento"."""

    def test_milhao_nao_vira_mil(self):
        assert extrair_preco("posso ir ate 1 milhao") == "1m"
        assert extrair_preco("ticket de 2 milhoes") == "2m"
        assert extrair_preco("tenho 1 milhão") == "1m"

    def test_mil_continua_sendo_mil(self):
        assert extrair_preco("ate 800 mil") == "800k"
        assert extrair_preco("ate 600k") == "600k"

    def test_mi_e_milhao(self):
        assert extrair_preco("uns 3 mi") == "3m"

    def test_decimal(self):
        assert extrair_preco("quero 1.5m") == "1.5m"

    def test_valor_com_separador_de_milhar(self):
        # Sem "mil" nem "k" na frase, so o ponto de milhar.
        assert extrair_preco("R$ 450.000") == "450k"

    def test_numero_solto_nao_e_preco(self):
        # O "3" de "3 quartos" e o telefone eram os dois jeitos de o perfil
        # ganhar um orcamento que o lead nunca disse.
        assert extrair_preco("preciso de 3 quartos") == "undefined"
        assert extrair_preco("telefone 21 99999-4410") == "undefined"

    def test_numero_por_extenso_nao_vira_faixa_invertida(self):
        # "1 milhao e 200 mil" sao R$ 1.200.000, nao a faixa de R$ 1 mi a
        # R$ 200 mil, que e como sairia lendo os dois numeros como extremos.
        assert extrair_preco("1 milhao e 200 mil") == "1m"

    def test_faixa_de_verdade_continua_faixa(self):
        assert extrair_preco("entre 500k e 800k") == "500k-800k"


class TestRegiaoCobreABase:
    """A lista escrita a mao perdia 35 dos 140 imoveis, e a conversa entrava em loop."""

    def test_bairros_que_faltavam(self):
        # Sem estes cinco, `next_to_collect` travava em `region`, o agente
        # repetia a pergunta a cada turno e o seletor de data nunca aparecia.
        assert extrair_regiao("quero na Tijuca") == "Tijuca"
        assert extrair_regiao("no Recreio") == "Recreio"
        assert extrair_regiao("em Jacarepagua") == "Jacarepaguá"
        assert extrair_regiao("na Vila Isabel") == "Vila Isabel"
        assert extrair_regiao("no Meier") == "Méier"

    def test_sem_acento_casa_e_devolve_com_acento(self):
        # Quase ninguem digita acento no chat, e o nome canonico e a CHAVE da
        # base de imoveis: devolver "Meier" faria a busca nao achar nada.
        assert extrair_regiao("quero na gavea") == "Gávea"
        assert extrair_regiao("moro em niteroi") == "Niterói"

    def test_zona_continua_funcionando(self):
        assert extrair_regiao("procuro na zona sul") == "Zona Sul"

    def test_lugar_que_nao_existe_na_base(self):
        assert extrair_regiao("quero em Marte") == "undefined"

    def test_todos_os_bairros_da_base_sao_reconheciveis(self):
        # O teste que impede a lista de sair de sincronia de novo.
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(AQUI)),
                                         "ai-memory-rag", "src"))
        from rag import schema

        for bairro in schema.NEIGHBORHOODS:
            assert extrair_regiao("quero em %s" % bairro) == bairro, bairro


class TestNomeNaoEProfissao:

    def test_estado_civil_nao_e_nome(self):
        # "sou casado e tenho dois filhos" gravava o nome "Casado", e como o
        # perfil e monotonico o agente chamava o lead assim ate o fim.
        assert extrair_nome("sou casado e tenho dois filhos") == "undefined"
        assert extrair_nome("sou solteira") == "undefined"

    def test_profissao_nao_e_nome(self):
        assert extrair_nome("sou investidor") == "undefined"
        assert extrair_nome("sou aposentado, quero algo tranquilo") == "undefined"

    def test_apresentacao_de_verdade_continua_funcionando(self):
        assert extrair_nome("meu nome e Marcos") == "Marcos"
        assert extrair_nome("sou Marcos") == "Marcos"

    def test_artigo_e_pulado_em_vez_de_rejeitado(self):
        assert extrair_nome("eu sou a Ana") == "Ana"
        assert extrair_nome("sou o Joao") == "Joao"


class TestUrgenciaSemFragmento:

    def test_ja_sozinho_nao_e_pressa(self):
        # Rendia "alta" para quem acabou de dizer o contrario: 15 pontos a mais
        # no score e follow-up a cada 4 horas.
        assert extrair_urgencia("ja tenho imovel, sem pressa") == "baixa"

    def test_logo_de_lugar_nao_e_logo_de_tempo(self):
        assert extrair_urgencia("moro logo ali no Leblon") == "baixa"

    def test_negacao_vence_a_pista_indireta(self):
        assert extrair_urgencia("preciso rapido, mas sem pressa") == "baixa"

    def test_pressa_de_verdade_continua_alta(self):
        assert extrair_urgencia("preciso urgente") == "alta"
        assert extrair_urgencia("quero o quanto antes") == "alta"
        assert extrair_urgencia("preciso essa semana") == "alta"

    def test_media(self):
        assert extrair_urgencia("procuro em breve") == "media"


class TestNegacaoDeIntencao:
    """Errar a intencao troca a ordem de coleta inteira e o que o agente oferece."""

    def test_nao_quero_comprar_quero_alugar(self):
        assert extrair_intencao("nao quero comprar, quero alugar") == "ALUGUEL"

    def test_nao_quero_alugar_quero_comprar(self):
        # Antes empatava 2 a 2 e o desempate caia em COMPRA por acaso, nao por
        # analise: o resultado certo pelo motivo errado nao e resultado.
        assert extrair_intencao("não quero alugar, quero comprar") == "COMPRA"

    def test_nao_e_para_investir_e_para_morar(self):
        assert extrair_intencao(
            "não é para investir, quero comprar para morar com minha familia"
        ) == "COMPRA"

    def test_negacao_longe_nao_alcanca(self):
        # A negacao so vale para o que vem logo depois dela.
        assert extrair_intencao(
            "nao gostei do ultimo apartamento que vi, mas quero comprar"
        ) == "COMPRA"

    def test_investidor_continua_investidor(self):
        assert extrair_intencao("quero investir em imoveis para alugar") == "INVESTIMENTO"
        assert extrair_intencao("quero comprar para investir") == "INVESTIMENTO"


class TestTipoDeImovel:
    """A base tem 9 salas comerciais e 11 studios de 0 quartos."""

    def test_tipos_nomeados(self):
        assert extrair_tipo_imovel("quero uma casa") == "HOUSE"
        assert extrair_tipo_imovel("procuro um apartamento") == "APARTMENT"
        assert extrair_tipo_imovel("quero uma cobertura") == "PENTHOUSE"
        assert extrair_tipo_imovel("um studio pequeno") == "STUDIO"
        assert extrair_tipo_imovel("preciso de uma sala comercial") == "COMMERCIAL"

    def test_cobertura_vence_apartamento(self):
        # Uma cobertura tambem e descrita como apartamento; o mais especifico
        # tem que ganhar.
        assert extrair_tipo_imovel("quero uma cobertura, apartamento amplo") == "PENTHOUSE"

    def test_sala_de_estar_nao_e_sala_comercial(self):
        # "sala" sozinha ficou de fora do mapa justamente por isto.
        assert extrair_tipo_imovel("com sala ampla e dois quartos") == "undefined"

    def test_sem_tipo(self):
        assert extrair_tipo_imovel("quero 3 quartos em Botafogo") == "undefined"


class TestNumeroSoltoNaoEOrcamento:
    """Custou uma conversa real: telefone sem DDD virou orcamento de R$ 33 mi.

    O efeito nao parou no painel. Para um investidor, o orcamento inventado
    atropelava o ticket informado na hora de filtrar imoveis, e quem disse
    "tenho 800 mil" recebia casas de R$ 6,5 milhoes no Leblon.
    """

    def test_telefone_sem_ddd(self):
        assert extrair_preco("Marcos, 33410549") == "undefined"

    def test_telefone_completo(self):
        assert extrair_preco("21 99999-4410") == "undefined"

    def test_cpf(self):
        assert extrair_preco("meu cpf 12345678901") == "undefined"

    def test_quantidade_de_quartos(self):
        assert extrair_preco("preciso de 3 quartos") == "undefined"

    def test_cifrao_basta(self):
        assert extrair_preco("R$ 600000") == "600k"

    def test_separador_de_milhar_basta(self):
        # "450.000" e dinheiro escrito por gente; "33410549" nao e.
        assert extrair_preco("450.000") == "450k"

    def test_aluguel_pequeno_nao_e_descartado_por_ser_pequeno(self):
        # A regra antiga exigia magnitude e descartava valores abaixo de 100
        # mil: um aluguel de R$ 8.550 sumia justamente na tela em que ele e a
        # resposta certa.
        assert extrair_preco("R$ 8.550") == "8.55k"
