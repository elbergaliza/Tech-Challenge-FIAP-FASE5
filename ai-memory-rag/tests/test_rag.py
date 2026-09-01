"""
Testes do módulo de RAG (Parte 2).

Rodam offline, sem chave de API e sem nenhuma dependência externa:

    python -m unittest discover -s ai-memory-rag/tests -v

O embedder é sempre o HashingEmbedder, para que os testes sejam
determinísticos. Testar contra a API real do Gemini deixaria a suíte lenta,
dependente de rede e dependente de cota.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

MODULE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, MODULE_ROOT)

from rag import indexer, retriever, schema                     # noqa: E402
from rag import embeddings                                      # noqa: E402
from rag.embeddings import HashingEmbedder, cosine, tokenize    # noqa: E402


def valid_property(**overrides):
    """Documento mínimo válido, para os testes de validação e de filtro."""
    base = {
        "id": "IMV-9999",
        "title": "Apartamento de 3 quartos em Copacabana",
        "description": "Apartamento com varanda e vista mar.",
        "deal_type": "SALE",
        "property_type": "APARTMENT",
        "price": 900000.0,
        "condo_fee": 1200.0,
        "property_tax": 450.0,
        "bedrooms": 3,
        "suites": 1,
        "bathrooms": 2,
        "parking": 1,
        "area_m2": 95.0,
        "neighborhood": "Copacabana",
        "zone": "Zona Sul",
        "city": "Rio de Janeiro",
        "lat": -22.9711,
        "lon": -43.1822,
        "features": ["varanda", "vista mar"],
        "accepts_financing": True,
        "annual_yield_pct": 5.4,
        "status": "AVAILABLE",
        "updated_at": "2026-08-08",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------


class TestSchema(unittest.TestCase):

    def test_resolve_region_identifies_neighborhood(self):
        self.assertEqual(schema.resolve_region("Copacabana"), ("Copacabana", None))

    def test_resolve_region_identifies_zone(self):
        self.assertEqual(schema.resolve_region("Zona Sul"), (None, "Zona Sul"))

    def test_resolve_region_ignores_case_and_spaces(self):
        self.assertEqual(schema.resolve_region("  zona sul  "), (None, "Zona Sul"))
        self.assertEqual(schema.resolve_region("LEBLON"), ("Leblon", None))

    def test_resolve_region_handles_person_1_undefined(self):
        # "undefined" é literalmente o que o agente da Pessoa 1 devolve quando
        # não conseguiu extrair o campo. Não pode virar filtro.
        self.assertEqual(schema.resolve_region("undefined"), (None, None))
        self.assertEqual(schema.resolve_region(None), (None, None))
        self.assertEqual(schema.resolve_region(""), (None, None))

    def test_unknown_region_does_not_filter(self):
        self.assertEqual(schema.resolve_region("Moema"), (None, None))

    def test_validate_accepts_a_good_document(self):
        self.assertEqual(schema.validate_property(valid_property()), [])

    def test_validate_rejects_invalid_fields(self):
        problems = schema.validate_property(valid_property(
            deal_type="INVESTIMENTO",  # not a deal type
            price=0,
            neighborhood="Moema",
        ))
        text = " | ".join(problems)
        self.assertIn("deal_type", text)
        self.assertIn("price", text)
        self.assertIn("neighborhood", text)

    def test_validate_rejects_missing_field(self):
        incomplete = valid_property()
        del incomplete["bedrooms"]
        self.assertTrue(
            any("bedrooms" in p for p in schema.validate_property(incomplete))
        )

    def test_every_catalogue_zone_is_in_zones(self):
        for neighborhood, data in schema.NEIGHBORHOODS.items():
            self.assertIn(data[0], schema.ZONES, "invalid zone in %s" % neighborhood)


# ---------------------------------------------------------------------------


class TestEmbeddings(unittest.TestCase):

    def setUp(self):
        self.embedder = HashingEmbedder(dim=256)

    def test_tokenize_produces_bigrams(self):
        tokens = tokenize("zona sul")
        self.assertIn("zona", tokens)
        self.assertIn("sul", tokens)
        self.assertIn("zona_sul", tokens)

    def test_tokenize_normalizes_accents(self):
        self.assertEqual(tokenize("Gávea"), tokenize("gavea"))

    def test_tokenize_drops_stopwords(self):
        self.assertNotIn("de", tokenize("casa de praia"))

    def test_vector_is_normalized(self):
        vector = self.embedder.embed_query("apartamento na zona sul")
        norm = sum(v * v for v in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_deterministic_across_instances(self):
        # Regressão: uma versão anterior usaria o hash() embutido, que é
        # randomizado por processo e faria o índice salvo divergir das consultas
        # de execuções seguintes.
        a = HashingEmbedder(dim=256).embed_query("cobertura no Leblon")
        b = HashingEmbedder(dim=256).embed_query("cobertura no Leblon")
        self.assertEqual(a, b)

    def test_cosine_with_itself_is_one(self):
        vector = self.embedder.embed_query("casa com quintal na Tijuca")
        self.assertAlmostEqual(cosine(vector, vector), 1.0, places=6)

    def test_related_text_scores_above_unrelated(self):
        query = self.embedder.embed_query("apartamento 3 quartos Copacabana")
        near, far = self.embedder.embed_documents([
            "Apartamento de 3 quartos em Copacabana com varanda",
            "Sala comercial no Centro, sem vaga",
        ])
        self.assertGreater(cosine(query, near), cosine(query, far))

    def test_cosine_rejects_different_dimensions(self):
        with self.assertRaises(ValueError):
            cosine([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_empty_text_does_not_break(self):
        self.assertEqual(len(self.embedder.embed_query("")), 256)


# ---------------------------------------------------------------------------


class TestEmbedderSelection(unittest.TestCase):
    """`get_embedder` no modo automatico nunca pode derrubar o processo.

    A promessa do modulo e que sem chave, com chave errada ou sem internet o
    RAG continua funcionando em modo offline. Construir o `GeminiEmbedder` nao
    e suficiente para validar isso: o SDK so fala com a API na primeira
    requisicao, entao uma chave invalida passava pela construcao e explodia
    depois, dentro do `build_index`.
    """

    def setUp(self):
        self.original = embeddings.GeminiEmbedder
        self.addCleanup(setattr, embeddings, "GeminiEmbedder", self.original)

    def _get(self, prefer=None):
        # O fallback avisa por print; o aviso e desejado, so nao no meio da
        # saida dos testes.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            embedder = embeddings.get_embedder(prefer=prefer)

        return embedder, buffer.getvalue()

    def test_falls_back_when_construction_fails(self):
        # Sem GEMINI_API_KEY: o construtor levanta na hora.
        class SemChave(object):
            def __init__(self, **kwargs):
                raise RuntimeError("GEMINI_API_KEY nao encontrada")

        embeddings.GeminiEmbedder = SemChave
        embedder, aviso = self._get()

        self.assertIsInstance(embedder, HashingEmbedder)
        self.assertIn("indisponivel", _sem_acento(aviso))

    def test_falls_back_when_the_first_call_fails(self):
        # Chave presente mas invalida: o construtor passa e a API recusa.
        # Este e o caso que quebrava o run_chat.py com um traceback cru.
        class ChaveInvalida(object):
            def __init__(self, **kwargs):
                pass

            def embed_query(self, text):
                raise RuntimeError("400 INVALID_ARGUMENT: API key not valid")

            def embed_documents(self, texts):
                raise AssertionError("nao deveria chegar aqui")

        embeddings.GeminiEmbedder = ChaveInvalida
        embedder, aviso = self._get()

        self.assertIsInstance(embedder, HashingEmbedder)
        self.assertIn("API key not valid", aviso)

    def test_uses_gemini_when_it_actually_answers(self):
        class Funcionando(object):
            name = "gemini-fake"

            def __init__(self, **kwargs):
                self.sondado = False

            def embed_query(self, text):
                self.sondado = True
                return [0.0, 1.0]

        embeddings.GeminiEmbedder = Funcionando
        embedder, aviso = self._get()

        self.assertIsInstance(embedder, Funcionando)
        self.assertTrue(embedder.sondado, "a sonda precisa ter sido chamada")
        self.assertEqual(aviso, "")

    def test_hashing_preference_never_touches_the_network(self):
        class Explode(object):
            def __init__(self, **kwargs):
                raise AssertionError("prefer=hashing nao pode instanciar o Gemini")

        embeddings.GeminiEmbedder = Explode
        embedder, _ = self._get(prefer="hashing")

        self.assertIsInstance(embedder, HashingEmbedder)

    def test_explicit_gemini_preference_still_fails_loudly(self):
        # Quem pede Gemini explicitamente quer saber que nao deu, e nao receber
        # um indice lexical silenciosamente no lugar.
        class SemChave(object):
            def __init__(self, **kwargs):
                raise RuntimeError("GEMINI_API_KEY nao encontrada")

        embeddings.GeminiEmbedder = SemChave
        with self.assertRaises(RuntimeError):
            embeddings.get_embedder(prefer="gemini")


def _sem_acento(text):
    import unicodedata
    decomposto = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


# ---------------------------------------------------------------------------


class _EmbedderContador(object):
    """Embedder que conta quantos textos foram embedados de verdade."""

    name = "contador-v1"
    dim = 8

    def __init__(self):
        self.textos = 0
        self.chamadas = 0

    def embed_documents(self, texts):
        self.textos += len(texts)
        self.chamadas += 1
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        vetor = [0.0] * self.dim
        for i, ch in enumerate(text[:self.dim]):
            vetor[i] = (ord(ch) % 17) / 17.0
        return vetor


class TestIndexCache(unittest.TestCase):
    """Indexar custa uma chamada de API por imovel.

    Reconstruir a base inteira a cada inicializacao estourava a cota do plano
    gratuito do Gemini antes da primeira mensagem do lead. `get_index` existe
    para que esse custo seja pago uma vez so.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.caminho = os.path.join(self.dir, "index.json")
        self.props = indexer.load_properties()[:12]

    def test_second_call_does_not_embed_again(self):
        primeiro = _EmbedderContador()
        index, origem = indexer.get_index(primeiro, self.props, self.caminho)
        self.assertEqual(origem, "rebuild")
        self.assertEqual(primeiro.textos, 12)

        segundo = _EmbedderContador()
        index2, origem2 = indexer.get_index(segundo, self.props, self.caminho)
        self.assertEqual(origem2, "cache")
        self.assertEqual(segundo.textos, 0, "nao pode chamar a API de novo")
        self.assertEqual(len(index2), len(index))

    def test_changing_the_base_invalidates_the_cache(self):
        indexer.get_index(_EmbedderContador(), self.props, self.caminho)

        alterados = [dict(p) for p in self.props]
        alterados[0]["description"] = alterados[0]["description"] + " com vista"

        terceiro = _EmbedderContador()
        _, origem = indexer.get_index(terceiro, alterados, self.caminho)
        self.assertEqual(origem, "rebuild")
        self.assertEqual(terceiro.textos, 12)

    def test_changing_the_embedder_invalidates_the_cache(self):
        indexer.get_index(_EmbedderContador(), self.props, self.caminho)

        outro = HashingEmbedder()
        index, origem = indexer.get_index(outro, self.props, self.caminho)
        self.assertEqual(origem, "rebuild")
        self.assertEqual(index.embedder_name, outro.name)

    def test_corrupt_index_file_is_rebuilt_not_fatal(self):
        with io.open(self.caminho, "w", encoding="utf-8") as handle:
            handle.write(u"{lixo")

        _, origem = indexer.get_index(_EmbedderContador(), self.props, self.caminho)
        self.assertEqual(origem, "rebuild")

    def test_quota_failure_falls_back_to_offline_index(self):
        # Foi exatamente isto que derrubou o run_chat.py: 429 no meio do
        # build_index, traceback cru, processo morto.
        class Estourado(object):
            name = "gemini-embedding-001"
            dim = 768

            def embed_documents(self, texts):
                raise RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")

            def embed_query(self, text):
                raise RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            index, origem = indexer.get_index(Estourado(), self.props, self.caminho)

        self.assertEqual(origem, "rebuild")
        self.assertEqual(index.embedder_name, HashingEmbedder.name)
        self.assertEqual(len(index), 12)
        self.assertIn("429", buffer.getvalue())

    def test_fallback_can_be_turned_off(self):
        class Estourado(object):
            name = "gemini-embedding-001"
            dim = 768

            def embed_documents(self, texts):
                raise RuntimeError("429 RESOURCE_EXHAUSTED")

        with self.assertRaises(RuntimeError):
            indexer.get_index(Estourado(), self.props, self.caminho, fallback=False)


class TestGeminiThrottle(unittest.TestCase):
    """A cota do plano gratuito e por minuto, e a base tem 140 imoveis."""

    def setUp(self):
        # O throttle avisa por print quando segura a chamada. O aviso e
        # desejado em producao, so nao no meio da saida dos testes.
        silencio = contextlib.redirect_stdout(io.StringIO())
        silencio.__enter__()
        self.addCleanup(silencio.__exit__, None, None, None)

    def _embedder(self, rpm, dormidas):
        # Constroi sem passar pelo __init__, que exige chave e SDK instalado.
        embedder = embeddings.GeminiEmbedder.__new__(embeddings.GeminiEmbedder)
        embedder.rpm = rpm
        embedder._sent = []
        embedder._sleep = dormidas.append
        return embedder

    def test_stays_under_the_limit_by_waiting(self):
        dormidas = []
        embedder = self._embedder(90, dormidas)
        relogio = [1000.0]

        embedder._throttle(60, now=lambda: relogio[0])
        self.assertEqual(dormidas, [], "60 de 90 nao precisa esperar")

        embedder._throttle(60, now=lambda: relogio[0])
        self.assertEqual(len(dormidas), 1, "120 num minuto tem que esperar")
        self.assertGreater(dormidas[0], 59.0)

    def test_a_full_window_later_does_not_wait(self):
        dormidas = []
        embedder = self._embedder(90, dormidas)
        relogio = [1000.0]

        embedder._throttle(80, now=lambda: relogio[0])
        relogio[0] += 61.0
        embedder._throttle(80, now=lambda: relogio[0])

        self.assertEqual(dormidas, [], "a janela anterior ja expirou")

    def test_zero_disables_the_throttle(self):
        dormidas = []
        embedder = self._embedder(0, dormidas)
        embedder._throttle(10000, now=lambda: 1000.0)
        self.assertEqual(dormidas, [])


class TestGeminiCallDoesNotDoubleRequests(unittest.TestCase):
    """Um erro de API nao pode disparar uma segunda requisicao identica.

    O `except Exception` antigo existia para tolerar assinaturas antigas do
    SDK, mas capturava erro de API tambem: um 429 gerava outro 429 na hora,
    dobrando o consumo justamente quando a cota ja tinha acabado.
    """

    def _embedder(self):
        embedder = embeddings.GeminiEmbedder.__new__(embeddings.GeminiEmbedder)
        embedder.model = "fake"
        embedder.dim = 4
        return embedder

    def test_api_error_is_raised_once(self):
        tentativas = []

        def chamada(chunk, config):
            tentativas.append(config)
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

        embedder = self._embedder()
        embedder._call_once = chamada

        with self.assertRaises(RuntimeError):
            embedder._call(["a"], "RETRIEVAL_DOCUMENT")

        self.assertEqual(len(tentativas), 1, "erro de API nao pode retentar aqui")

    def test_signature_error_still_retries_without_config(self):
        tentativas = []

        class Valor(object):
            values = [1.0, 0.0, 0.0, 0.0]

        class Resposta(object):
            embeddings = [Valor()]

        def chamada(chunk, config):
            tentativas.append(config)
            if config is not None:
                raise TypeError("unexpected keyword argument 'config'")
            return Resposta()

        embedder = self._embedder()
        embedder._call_once = chamada

        vetores = embedder._call(["a"], "RETRIEVAL_DOCUMENT")

        self.assertEqual(len(tentativas), 2)
        self.assertIsNone(tentativas[1])
        self.assertEqual(len(vetores), 1)




# ---------------------------------------------------------------------------


class TestGeminiSkipsEmptyText(unittest.TestCase):
    """Consulta em branco nao pode virar requisicao.

    A API responde 400 "EmbedContentRequest.content contains an empty Part", e
    o comando /imoveis sem argumento busca exatamente com consulta vazia. O
    HashingEmbedder aceitava e devolvia vetor zero, entao o problema so
    aparecia com o Gemini ligado.
    """

    def _embedder(self, enviados):
        embedder = embeddings.GeminiEmbedder.__new__(embeddings.GeminiEmbedder)
        embedder.model = "fake"
        embedder.dim = 4
        embedder.batch = 32
        embedder.rpm = 0
        embedder._sent = []
        embedder._sleep = lambda s: None

        def chamada(chunk, task_type):
            enviados.extend(chunk)
            return [[1.0, 0.0, 0.0, 0.0] for _ in chunk]

        embedder._call = chamada
        return embedder

    def test_empty_query_never_reaches_the_api(self):
        enviados = []
        embedder = self._embedder(enviados)

        vetor = embedder.embed_query("")

        self.assertEqual(enviados, [], "string vazia nao pode ser enviada")
        self.assertEqual(vetor, [0.0, 0.0, 0.0, 0.0])

    def test_blank_query_never_reaches_the_api(self):
        enviados = []
        embedder = self._embedder(enviados)

        embedder.embed_query("   ")

        self.assertEqual(enviados, [])

    def test_empty_entries_do_not_shift_the_others(self):
        # O risco de filtrar e devolver os vetores fora de ordem.
        enviados = []
        embedder = self._embedder(enviados)

        vetores = embedder.embed_documents(["", "casa", "  ", "apartamento"])

        self.assertEqual(enviados, ["casa", "apartamento"])
        self.assertEqual(len(vetores), 4)
        self.assertEqual(vetores[0], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(vetores[1], [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(vetores[2], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(vetores[3], [1.0, 0.0, 0.0, 0.0])


class TestEmptyQuerySearchStillWorks(unittest.TestCase):
    """Buscar sem consulta textual e um caso valido, nao um erro."""

    def test_search_with_empty_text_returns_properties(self):
        embedder = HashingEmbedder()
        props = indexer.load_properties()
        index = indexer.build_index(props, embedder)

        resultado = retriever.search_for_lead(
            index, {"intent": "BUY", "region": "Botafogo"}, "", embedder=embedder,
        )

        self.assertGreater(len(resultado), 0,
                           "sem texto, o ranking cai nos filtros e bonus")


class TestParsers(unittest.TestCase):

    def test_range_with_two_values(self):
        self.assertEqual(retriever.parse_price_range("500k-800k"), (500000.0, 800000.0))

    def test_range_is_order_independent(self):
        # A Pessoa 1 monta a faixa via set(), cuja ordem de iteração varia
        # entre execuções do Python. As duas grafias têm de concordar.
        self.assertEqual(
            retriever.parse_price_range("800k-500k"),
            retriever.parse_price_range("500k-800k"),
        )

    def test_single_value_is_a_ceiling_not_a_floor(self):
        self.assertEqual(retriever.parse_price_range("300k"), (None, 300000.0))

    def test_understands_millions(self):
        self.assertEqual(retriever.parse_price_range("1.5m"), (None, 1500000.0))
        self.assertEqual(retriever.parse_price_range("2 milhões"), (None, 2000000.0))

    def test_understands_free_text(self):
        self.assertEqual(retriever.parse_price_range("até 600 mil"), (None, 600000.0))

    def test_rental_in_reais(self):
        self.assertEqual(retriever.parse_price_range("3500"), (None, 3500.0))

    def test_undefined_creates_no_filter(self):
        self.assertEqual(retriever.parse_price_range("undefined"), (None, None))
        self.assertEqual(retriever.parse_price_range(None), (None, None))

    def test_percentage(self):
        self.assertEqual(retriever.parse_percentage("espero 8% ao ano"), 8.0)
        self.assertEqual(retriever.parse_percentage("7,5%"), 7.5)
        self.assertIsNone(retriever.parse_percentage("undefined"))


# ---------------------------------------------------------------------------


class TestFiltersFromProfile(unittest.TestCase):

    def test_purchase_becomes_sale(self):
        filters = retriever.filters_from_profile({"intencao": "COMPRA"})
        self.assertEqual(filters.deal_type, "SALE")

    def test_rental_stays_rental(self):
        filters = retriever.filters_from_profile({"intencao": "ALUGUEL"})
        self.assertEqual(filters.deal_type, "RENTAL")

    def test_investment_becomes_sale(self):
        # Investidor compra. INVESTIMENTO é perfil, não tipo de negócio.
        filters = retriever.filters_from_profile({"intencao": "INVESTIMENTO"})
        self.assertEqual(filters.deal_type, "SALE")

    def test_investment_uses_expected_return(self):
        filters = retriever.filters_from_profile(
            {"intencao": "INVESTIMENTO"},
            expected_return="quero pelo menos 6% ao ano",
        )
        self.assertEqual(filters.min_yield, 6.0)

    def test_full_profile_from_person_1(self):
        # Formato exato do `dados_coletados` documentado no README do repo.
        data = {
            "nome": "João",
            "intencao": "COMPRA",
            "preco_faixa": "500k-800k",
            "regiao": "Copacabana",
            "bedrooms": "3",
            "urgencia": "alta",
            "email": "undefined",
            "telefone": "undefined",
        }
        filters = retriever.filters_from_profile(data)

        self.assertEqual(filters.deal_type, "SALE")
        self.assertEqual(filters.min_price, 500000.0)
        self.assertEqual(filters.max_price, 800000.0)
        self.assertEqual(filters.min_bedrooms, 3)
        self.assertEqual(filters.neighborhood, "Copacabana")
        self.assertIsNone(filters.zone)

    def test_empty_profile_creates_no_filters(self):
        filters = retriever.filters_from_profile({
            "intencao": "undefined",
            "preco_faixa": "undefined",
            "regiao": "undefined",
            "bedrooms": "undefined",
        })
        for field in ("deal_type", "min_price", "max_price", "min_bedrooms",
                      "neighborhood", "zone"):
            self.assertIsNone(getattr(filters, field), field)

    def test_radius_turns_neighborhood_into_coordinates(self):
        filters = retriever.filters_from_profile({"regiao": "Leblon"}, radius_km=5)
        self.assertIsNone(filters.neighborhood)
        self.assertEqual(filters.radius_km, 5)
        self.assertAlmostEqual(filters.lat, -22.9838, places=4)

    def test_filters_reject_unknown_field(self):
        with self.assertRaises(TypeError):
            retriever.Filters(maximum_price=100)


# ---------------------------------------------------------------------------


class TestGeography(unittest.TestCase):

    def test_leblon_and_ipanema_are_neighbours(self):
        d = retriever.distance_km(-22.9838, -43.2226, -22.9838, -43.2045)
        self.assertLess(d, 3.0)

    def test_leblon_and_barra_are_far_apart(self):
        d = retriever.distance_km(-22.9838, -43.2226, -23.0045, -43.3650)
        self.assertGreater(d, 10.0)

    def test_distance_from_a_point_to_itself_is_zero(self):
        self.assertAlmostEqual(
            retriever.distance_km(-22.98, -43.22, -22.98, -43.22), 0.0, places=6
        )


# ---------------------------------------------------------------------------


class TestGeneratedBase(unittest.TestCase):
    """A base sintética versionada precisa estar íntegra."""

    @classmethod
    def setUpClass(cls):
        cls.properties = indexer.load_properties()

    def test_base_has_reasonable_volume(self):
        self.assertGreaterEqual(len(self.properties), 100)

    def test_every_document_is_valid(self):
        # load_properties() já levanta se houver problema; este teste torna a
        # garantia explícita e legível no relatório da suíte.
        for prop in self.properties:
            self.assertEqual(schema.validate_property(prop), [], prop["id"])

    def test_ids_are_unique(self):
        ids = [p["id"] for p in self.properties]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_catalogue_neighborhood_has_available_stock(self):
        available = [p for p in self.properties if p["status"] == "AVAILABLE"]
        with_stock = {p["neighborhood"] for p in available}
        missing = set(schema.NEIGHBORHOODS) - with_stock
        self.assertEqual(missing, set(), "neighborhoods without stock: %s" % missing)

    def test_both_deal_types_exist(self):
        self.assertEqual(
            {p["deal_type"] for p in self.properties}, {"SALE", "RENTAL"}
        )

    def test_rentals_have_no_yield_and_sales_do(self):
        for prop in self.properties:
            if prop["deal_type"] == "RENTAL":
                self.assertIsNone(prop["annual_yield_pct"], prop["id"])
            else:
                self.assertIsNotNone(prop["annual_yield_pct"], prop["id"])

    def test_rentals_are_far_cheaper_than_sales(self):
        sales = [p["price"] for p in self.properties if p["deal_type"] == "SALE"]
        rentals = [p["price"] for p in self.properties if p["deal_type"] == "RENTAL"]
        self.assertLess(max(rentals), min(sales))

    def test_json_schema_was_written(self):
        path = os.path.join(REPO_ROOT, "shared", "schemas", "imovel_schema.json")
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["title"], "Property")
        self.assertIn("price", data["properties"])


# ---------------------------------------------------------------------------


class TestIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.properties = indexer.load_properties()
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def test_index_covers_the_whole_base(self):
        self.assertEqual(len(self.index), len(self.properties))

    def test_search_text_repeats_weighted_fields(self):
        text = indexer.build_search_text(valid_property())
        # title tem peso 3
        self.assertEqual(text.count("Apartamento de 3 quartos em Copacabana"), 3)
        # a zona entra mesmo quando o anúncio nunca a menciona
        self.assertIn("Zona Sul", text)

    def test_search_text_marks_the_deal_type(self):
        self.assertIn("aluguel", indexer.build_search_text(
            valid_property(deal_type="RENTAL")))
        self.assertIn("venda", indexer.build_search_text(
            valid_property(deal_type="SALE")))

    def test_by_id(self):
        first = self.properties[0]["id"]
        self.assertEqual(self.index.by_id(first)["id"], first)
        self.assertIsNone(self.index.by_id("IMV-0000"))

    def test_facets_sum_to_the_total(self):
        facets = self.index.facets("deal_type")
        self.assertEqual(sum(facets.values()), len(self.index))

    def test_disk_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "index.json")
            indexer.save_index(self.index, path)
            reloaded = indexer.load_index(path)

        self.assertEqual(len(reloaded), len(self.index))
        self.assertEqual(reloaded.embedder_name, self.index.embedder_name)
        # Consulta feita depois do recarregamento tem de bater com a original.
        query = self.embedder.embed_query("cobertura no Leblon")
        before = [d["id"] for d, _ in self.index.similarities(query)[:5]]
        after = [d["id"] for d, _ in reloaded.similarities(query)[:5]]
        self.assertEqual(before, after)

    def test_index_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            indexer.Index("x", 3, [valid_property()], [])


# ---------------------------------------------------------------------------


class TestSearch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.properties = indexer.load_properties()
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def search(self, filters, text="", top_k=3, relax=True):
        return retriever.search(
            self.index, filters, text,
            embedder=self.embedder, top_k=top_k, allow_relaxation=relax,
        )

    # -- filtros duros ------------------------------------------------------

    def test_price_filter_is_respected(self):
        filters = retriever.Filters(deal_type="SALE", max_price=700000)
        result = self.search(filters, "apartamento", relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertLessEqual(recommendation.property["price"], 700000)

    def test_bedroom_filter_is_respected(self):
        filters = retriever.Filters(min_bedrooms=3)
        result = self.search(filters, "familia", top_k=10, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertGreaterEqual(recommendation.property["bedrooms"], 3)

    def test_unavailable_properties_never_surface(self):
        # Sem nenhum filtro, todos são candidatos exceto os indisponíveis.
        # Este é o teste que impede o corretor de oferecer algo já vendido.
        ids = retriever.apply_filters(self.index, retriever.Filters())

        self.assertGreater(len(ids), 0)
        for property_id in ids:
            self.assertEqual(self.index.by_id(property_id)["status"], "AVAILABLE")

    def test_filter_matches_a_manual_count(self):
        filters = retriever.Filters(
            deal_type="SALE", zone="Zona Sul", min_bedrooms=3, max_price=2_000_000
        )
        expected = {
            p["id"] for p in self.properties
            if p["status"] == "AVAILABLE"
            and p["deal_type"] == "SALE"
            and p["zone"] == "Zona Sul"
            and p["bedrooms"] >= 3
            and p["price"] <= 2_000_000
        }
        self.assertEqual(retriever.apply_filters(self.index, filters), expected)

    def test_geographic_filter(self):
        _, _, lat, lon, _ = schema.NEIGHBORHOODS["Leblon"]
        filters = retriever.Filters(lat=lat, lon=lon, radius_km=4)
        result = self.search(filters, "apartamento", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertLessEqual(recommendation.distance_km, 4.0)
            # Nada da Barra cabe num raio de 4 km do Leblon.
            self.assertNotEqual(recommendation.property["neighborhood"], "Barra")

    def test_feature_filter(self):
        filters = retriever.Filters(features=["vista mar"])
        result = self.search(filters, "quero vista para o mar", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertIn("vista mar", recommendation.property["features"])

    def test_minimum_yield_for_investors(self):
        filters = retriever.Filters(deal_type="SALE", min_yield=6.0)
        result = self.search(filters, "investimento renda", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertGreaterEqual(recommendation.property["annual_yield_pct"], 6.0)

    # -- ranking ------------------------------------------------------------

    def test_respects_top_k(self):
        self.assertEqual(len(self.search(retriever.Filters(), "apartamento", top_k=3)), 3)

    def test_results_come_sorted_by_score(self):
        result = self.search(retriever.Filters(), "cobertura com piscina", top_k=5)
        scores = [r.score for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_exact_neighborhood_gets_a_bonus(self):
        filters = retriever.Filters(neighborhood="Ipanema")
        result = self.search(filters, "apartamento", top_k=3, relax=False)
        for recommendation in result:
            self.assertEqual(recommendation.property["neighborhood"], "Ipanema")
            self.assertIn("no bairro pedido", recommendation.reason)

    def test_semantics_influence_the_order(self):
        # Mesmos filtros, consultas diferentes: a ordem tem de mudar, senão o
        # ranking semântico não está fazendo nada.
        filters = retriever.Filters(deal_type="SALE", zone="Zona Sul")
        beach = [r.id for r in self.search(
            filters, "quero vista para o mar, perto da praia", top_k=5)]
        house = [r.id for r in self.search(
            filters, "casa grande com quintal para cachorro", top_k=5)]
        self.assertNotEqual(beach, house)

    # -- relaxamento --------------------------------------------------------

    def test_satisfiable_profile_does_not_relax(self):
        filters = retriever.Filters(deal_type="SALE", zone="Zona Sul", min_bedrooms=2)
        result = self.search(filters, "apartamento")

        self.assertFalse(result.was_relaxed)
        self.assertEqual(result.mismatches, [])

    def test_impossible_profile_relaxes_and_still_returns_options(self):
        # 4 quartos no Leblon por até 300 mil não existe em lugar nenhum.
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters)

        self.assertTrue(result.was_relaxed)
        self.assertGreater(len(result), 0, "relaxation should find alternatives")

    def test_relaxation_is_reported_in_text(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters)
        self.assertTrue(any("orçamento" in r for r in result.relaxations))

    def test_relaxation_disabled_returns_empty_without_breaking(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters, relax=False)

        self.assertEqual(len(result), 0)
        self.assertEqual(result.candidate_count, 0)

    # -- saída --------------------------------------------------------------

    def test_reason_describes_the_property(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Botafogo", min_bedrooms=2
        )
        recommendation = self.search(filters, "apartamento", top_k=1).recommendations[0]

        self.assertIn("Botafogo", recommendation.reason)
        self.assertIn("R$", recommendation.reason)
        self.assertIn("quartos", recommendation.reason)

    def test_to_dict_serializes(self):
        result = self.search(retriever.Filters(), "apartamento", top_k=2)
        # Precisa atravessar a API da Pessoa 3 como JSON.
        self.assertIn("reason", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_prompt_lists_ids_and_forbids_invention(self):
        result = self.search(retriever.Filters(deal_type="SALE"), "apartamento", top_k=2)
        prompt = retriever.format_for_prompt(result)

        self.assertIn("não invente", prompt)
        for recommendation in result:
            self.assertIn(recommendation.id, prompt)

    def test_empty_prompt_tells_the_model_not_to_hallucinate(self):
        empty = retriever.SearchResult([], [], 0, retriever.Filters())
        prompt = retriever.format_for_prompt(empty)

        self.assertIn("nenhum", prompt)
        self.assertIn("não invente", prompt)

    def test_prompt_warns_about_relaxation(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        prompt = retriever.format_for_prompt(self.search(filters))
        self.assertIn("OBSERVAÇÃO", prompt)


# ---------------------------------------------------------------------------


class TestMismatches(unittest.TestCase):
    """Precisão do que é reportado ao lead quando o resultado foge do pedido.

    Usa um índice minúsculo montado à mão, para que o cenário seja exato em vez
    de depender de como a base sintética caiu.
    """

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=128)
        cls.properties = [
            valid_property(
                id="IMV-0001", neighborhood="Copacabana", zone="Zona Sul",
                bedrooms=3, price=500000.0,
                title="Apartamento de 3 quartos em Copacabana",
            ),
            valid_property(
                id="IMV-0002", neighborhood="Copacabana", zone="Zona Sul",
                bedrooms=3, price=900000.0,
                title="Apartamento de 3 quartos em Copacabana",
            ),
            valid_property(
                id="IMV-0003", neighborhood="Botafogo", zone="Zona Sul",
                bedrooms=1, price=400000.0,
                title="Apartamento de 1 quarto em Botafogo",
            ),
        ]
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def search(self, filters, top_k):
        return retriever.search(
            self.index, filters, "apartamento", embedder=self.embedder, top_k=top_k
        )

    def test_satisfied_profile_produces_no_mismatch(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        result = self.search(filters, top_k=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.recommendations[0].id, "IMV-0001")
        self.assertEqual(result.mismatches, [])
        self.assertTrue(result.matches_profile)
        self.assertFalse(result.was_relaxed)

    def test_mismatch_does_not_mention_a_respected_constraint(self):
        # Regressão do bug encontrado na demo: a escada precisa subir o degrau
        # "orçamento ampliado" para completar o top_k, mas os dois imóveis
        # devolvidos (500k e 400k) cabem no orçamento de 600k. O prompt NÃO pode
        # dizer ao lead que o orçamento foi estourado.
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        result = self.search(filters, top_k=2)

        self.assertEqual(len(result), 2)
        for recommendation in result:
            self.assertLessEqual(recommendation.property["price"], 600000)

        # O degrau foi subido...
        self.assertTrue(any("orçamento" in r for r in result.relaxations))
        # ...mas não é reportado como desvio, porque nada estourou.
        self.assertFalse(
            any("orçamento" in m for m in result.mismatches),
            "mismatches wrongly mention the budget: %r" % result.mismatches,
        )

    def test_mismatch_reports_real_neighborhood_and_bedrooms(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        text = " | ".join(self.search(filters, top_k=2).mismatches)

        self.assertIn("Copacabana", text)
        self.assertIn("Botafogo", text)
        self.assertIn("quartos", text)

    def test_prompt_uses_mismatches_not_the_ladder(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        prompt = retriever.format_for_prompt(self.search(filters, top_k=2))

        self.assertIn("OBSERVAÇÃO", prompt)
        self.assertNotIn("orçamento ampliado", prompt)

    def test_budget_mismatch_is_reported_when_real(self):
        # Aqui o estouro é verdadeiro: as únicas opções de 3 quartos em
        # Copacabana acima de 450k custam 500k e 900k, então o desvio tem de
        # aparecer.
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=450000
        )
        result = self.search(filters, top_k=1)

        self.assertTrue(any("orçamento" in m for m in result.mismatches),
                        result.mismatches)


# ---------------------------------------------------------------------------


class TestIntegrationWithPerson1(unittest.TestCase):
    """Cenários do PDF, entrando pelo formato real da Pessoa 1."""

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.index = indexer.build_index(indexer.load_properties(), cls.embedder)

    def search_lead(self, data, text="", **kwargs):
        return retriever.search_for_lead(
            self.index, data, text, embedder=self.embedder, **kwargs
        )

    def test_scenario_1_buying_in_the_south_zone(self):
        data = {
            "nome": "João", "intencao": "COMPRA", "preco_faixa": "800k-1.5m",
            "regiao": "Zona Sul", "bedrooms": "3", "urgencia": "alta",
            "email": "undefined", "telefone": "undefined",
        }
        result = self.search_lead(
            data, "Estou procurando apartamento na zona sul, 3 quartos"
        )

        self.assertGreater(len(result), 0)
        for recommendation in result:
            prop = recommendation.property
            self.assertEqual(prop["deal_type"], "SALE")
            self.assertEqual(prop["zone"], "Zona Sul")
            self.assertGreaterEqual(prop["bedrooms"], 3)
            self.assertLessEqual(prop["price"], 1_500_000)

    def test_scenario_2_investing_for_income(self):
        data = {"intencao": "INVESTIMENTO", "preco_faixa": "1m", "regiao": "undefined"}
        result = self.search_lead(
            data,
            "Quero investir em imóveis para renda",
            expected_return="espero 6% ao ano",
        )

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertEqual(recommendation.property["deal_type"], "SALE")
            self.assertGreaterEqual(recommendation.property["annual_yield_pct"], 6.0)
            self.assertLessEqual(recommendation.property["price"], 1_000_000)

    def test_rental_scenario(self):
        data = {
            "intencao": "ALUGUEL", "preco_faixa": "4000",
            "regiao": "Tijuca", "bedrooms": "2",
        }
        result = self.search_lead(data, "procuro para alugar na Tijuca, 2 quartos")

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertEqual(recommendation.property["deal_type"], "RENTAL")

    def test_first_message_with_no_data_still_works(self):
        # Na primeira interação a Pessoa 1 devolve quase tudo como "undefined".
        # O RAG não pode explodir nem devolver vazio: ele mostra o que tem.
        data = {
            "nome": "undefined", "intencao": "undefined", "preco_faixa": "undefined",
            "regiao": "undefined", "bedrooms": "undefined", "urgencia": "undefined",
            "email": "undefined", "telefone": "undefined",
        }
        self.assertGreater(len(self.search_lead(data, "Oi, tudo bem?")), 0)

    def test_missing_collected_data_does_not_break(self):
        self.assertIsNotNone(self.search_lead(None, "oi"))
        self.assertIsNotNone(self.search_lead({}, "oi"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
