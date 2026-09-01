"""
Testes de privacidade e memória conversacional (Parte 2).

    python -m unittest discover -s ai-memory-rag/tests -v

Sem dependências e sem rede. O relógio é injetado, então retenção e cadência de
follow-up são testáveis sem esperar 180 dias.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import lead_profile                                            # noqa: E402
from memory.conversation_memory import (                       # noqa: E402
    DEFAULT_WINDOW, SUMMARY_THRESHOLD, ConversationMemory,
    InMemoryStore, JsonFileStore, validate_lead_id,
)
from privacy import (                                          # noqa: E402
    Pseudonymizer, contains_pii, detect_names, mask_for_log,
)


class FakeClock:
    """Relógio controlável, para testar prazos sem esperar por eles."""

    def __init__(self, start=None):
        self.moment = start or datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment = self.moment + timedelta(**delta)
        return self.moment


# ===========================================================================
# Privacidade
# ===========================================================================


class TestPseudonymizer(unittest.TestCase):

    def setUp(self):
        self.pseudo = Pseudonymizer()

    def test_masks_email(self):
        text, mapping = self.pseudo.mask("meu email é joao@exemplo.com")
        self.assertNotIn("joao@exemplo.com", text)
        self.assertIn("[EMAIL_1]", text)
        self.assertEqual(mapping["[EMAIL_1]"], "joao@exemplo.com")

    def test_masks_phone(self):
        text, _ = self.pseudo.mask("meu whats é (21) 98765-4321")
        self.assertNotIn("98765-4321", text)
        self.assertIn("[TELEFONE_1]", text)

    def test_masks_formatted_cpf(self):
        self.assertIn("[CPF_1]", self.pseudo.mask("CPF 123.456.789-00")[0])

    def test_masks_postcode(self):
        self.assertIn("[CEP_1]", self.pseudo.mask("moro no 22041-011")[0])

    def test_masks_a_known_name(self):
        text, mapping = self.pseudo.mask("Oi, aqui é o João", names=["João"])
        self.assertIn("[NOME_1]", text)
        self.assertEqual(mapping["[NOME_1]"], "João")

    def test_very_short_name_is_not_masked(self):
        self.assertIn("Jo", self.pseudo.mask("o Jo chegou", names=["Jo"])[0])

    def test_does_not_mistake_a_price_for_personal_data(self):
        # Regressão: valores de imóvel são as sequências numéricas mais comuns
        # nesta conversa. Se virarem telefone ou CEP, o agente perde o preço.
        for price in ["R$ 1.125.000", "R$ 806.000", "500000", "R$ 3.150"]:
            text, mapping = self.pseudo.mask("o valor é %s" % price)
            self.assertEqual(mapping, {}, "wrongly masked in %r" % price)
            self.assertIn(price, text)

    def test_alias_is_stable_across_turns(self):
        # O mesmo e-mail precisa receber sempre o mesmo apelido, senão o LLM vê
        # [EMAIL_1] e [EMAIL_2] e conclui que são pessoas diferentes.
        _, mapping = self.pseudo.mask("escreve pra ana@x.com")
        text2, mapping2 = self.pseudo.mask("confirma ana@x.com?", mapping=mapping)

        self.assertIn("[EMAIL_1]", text2)
        self.assertNotIn("[EMAIL_2]", text2)
        self.assertEqual(len(mapping2), 1)

    def test_different_emails_get_different_aliases(self):
        _, mapping = self.pseudo.mask("a@x.com e b@y.com")
        self.assertEqual(set(mapping), {"[EMAIL_1]", "[EMAIL_2]"})

    def test_round_trip_preserves_the_text(self):
        original = "Sou o João, joao@x.com, (21) 98765-4321"
        masked, mapping = self.pseudo.mask(original, names=["João"])
        self.assertEqual(self.pseudo.restore(masked, mapping), original)

    def test_restores_inside_the_llm_reply(self):
        _, mapping = self.pseudo.mask("sou o João, joao@x.com", names=["João"])
        reply = "Claro, [NOME_1]! Envio para [EMAIL_1] ainda hoje."

        self.assertEqual(
            self.pseudo.restore(reply, mapping),
            "Claro, João! Envio para joao@x.com ainda hoje.",
        )

    def test_alias_invented_by_the_llm_never_reaches_the_lead(self):
        # O modelo às vezes inventa um apelido que nunca existiu. O lead não
        # pode ver "[EMAIL_9]" na tela.
        self.assertNotIn("[", self.pseudo.restore("Enviarei para [EMAIL_9] agora", {}))

    def test_empty_text_does_not_break(self):
        self.assertEqual(self.pseudo.mask(None), ("", {}))
        self.assertEqual(self.pseudo.restore(None, {}), "")


class TestNameDetection(unittest.TestCase):
    """O nome chega declarado; é preciso pegá-lo já na primeira menção."""

    def setUp(self):
        self.pseudo = Pseudonymizer()

    def test_recognises_the_introduction_forms(self):
        cases = [
            ("Meu nome é João Pereira", "João"),
            ("nome e Carlos", "Carlos"),
            ("chamo-me Beatriz", "Beatriz"),
            ("chamo me Beatriz", "Beatriz"),
            ("Eu sou Marina", "Marina"),
            ("sou o Rafael", "Rafael"),
            ("aqui é a Camila", "Camila"),
        ]
        for text, expected in cases:
            self.assertIn(expected, detect_names(text), text)

    def test_first_name_comes_first(self):
        # É a forma usada na restauração: "Prazer, João!" e não
        # "Prazer, João Pereira!".
        self.assertEqual(detect_names("Meu nome é João Pereira")[0], "João")

    def test_returns_the_full_name_and_its_parts(self):
        names = detect_names("Meu nome é João Pereira")
        self.assertIn("João Pereira", names)
        self.assertIn("Pereira", names)

    def test_does_not_mistake_an_adjective_for_a_name(self):
        # Minúscula depois de "sou" não é nome próprio.
        self.assertEqual(detect_names("sou casado e sou de são paulo"), [])

    def test_text_without_an_introduction(self):
        self.assertEqual(detect_names("quero comprar na zona sul"), [])
        self.assertEqual(detect_names(""), [])

    def test_name_variants_share_one_alias(self):
        # Regressão: "João Pereira", "João" e "Pereira" viravam [NOME_1],
        # [NOME_2] e [NOME_3], e o LLM tratava como três pessoas.
        text, mapping = self.pseudo.mask(
            "Sou João Pereira. Pode me chamar de João, senhor Pereira não.",
            names=detect_names("Meu nome é João Pereira"),
        )

        aliases = {a for a in mapping if a.startswith("[NOME")}
        self.assertEqual(len(aliases), 1, mapping)
        self.assertNotIn("João", text)
        self.assertNotIn("Pereira", text)

    def test_full_name_is_not_left_half_masked(self):
        text, _ = self.pseudo.mask(
            "Sou João Pereira", names=detect_names("Sou João Pereira")
        )
        self.assertNotIn("Pereira", text)
        self.assertEqual(text.count("[NOME_1]"), 1)

    def test_restoration_uses_the_friendly_form(self):
        _, mapping = self.pseudo.mask(
            "Meu nome é João Pereira", names=detect_names("Meu nome é João Pereira")
        )
        self.assertEqual(self.pseudo.restore("Prazer, [NOME_1]!", mapping), "Prazer, João!")


class TestPrivacyUtilities(unittest.TestCase):

    def test_contains_pii(self):
        self.assertEqual(contains_pii("a@b.com"), ["EMAIL"])
        self.assertEqual(contains_pii("nada aqui"), [])
        self.assertIn("TELEFONE", contains_pii("liga (21) 98765-4321"))

    def test_mask_for_log_is_irreversible(self):
        record = mask_for_log("João pediu contato: joao@x.com", names=["João"])
        self.assertNotIn("joao@x.com", record)
        self.assertNotIn("João", record)
        # Sem numeração: não há como remontar o valor a partir do log.
        self.assertIn("[EMAIL]", record)
        self.assertIn("[NOME]", record)


# ===========================================================================
# Memória
# ===========================================================================


class TestLeadProfileAdapter(unittest.TestCase):
    """O único lugar onde chaves de dado em português podem existir."""

    def test_translates_person_1_keys(self):
        profile = lead_profile.from_agent({
            "nome": "João", "intencao": "COMPRA", "preco_faixa": "500k-800k",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
            "email": "joao@x.com", "telefone": "(21) 98765-4321",
        })

        self.assertEqual(profile, {
            "name": "João", "intent": "BUY", "price_range": "500k-800k",
            "region": "Copacabana", "bedrooms": "3", "urgency": "high",
            "email": "joao@x.com", "phone": "(21) 98765-4321",
        })

    def test_translates_the_three_intents(self):
        for theirs, ours in (("COMPRA", "BUY"), ("ALUGUEL", "RENT"),
                             ("INVESTIMENTO", "INVEST")):
            self.assertEqual(
                lead_profile.from_agent({"intencao": theirs})["intent"], ours
            )

    def test_drops_unknown_values_instead_of_carrying_them(self):
        # O código lá na frente nunca deveria precisar lembrar que "undefined"
        # é uma string mágica.
        profile = lead_profile.from_agent({
            "intencao": "COMPRA", "regiao": "undefined", "quartos": "", "nome": None,
        })
        self.assertEqual(profile, {"intent": "BUY"})

    def test_is_idempotent(self):
        # Perfil já traduzido precisa sobreviver a uma segunda passada, senão
        # fundir de volta na memória um perfil guardado o corromperia.
        once = lead_profile.from_agent({"intencao": "COMPRA", "quartos": "3"})
        self.assertEqual(lead_profile.from_agent(once), once)

    def test_ignores_fields_outside_the_contract(self):
        self.assertEqual(lead_profile.from_agent({"cor_favorita": "azul"}), {})

    def test_handles_missing_input(self):
        self.assertEqual(lead_profile.from_agent(None), {})
        self.assertEqual(lead_profile.from_agent({}), {})

    def test_round_trip_back_to_person_1_dialect(self):
        original = {
            "nome": "João", "intencao": "COMPRA", "preco_faixa": "500k",
            "regiao": "Copacabana", "quartos": "3", "urgencia": "alta",
            "email": "joao@x.com", "telefone": "(21) 98765-4321",
        }
        self.assertEqual(lead_profile.to_agent(lead_profile.from_agent(original)),
                         original)

    def test_to_agent_fills_gaps_with_their_undefined_marker(self):
        result = lead_profile.to_agent({"intent": "BUY"})
        self.assertEqual(result["intencao"], "COMPRA")
        self.assertEqual(result["regiao"], "undefined")

    def test_labels_render_enums_in_portuguese(self):
        # O código decide com "BUY"; o agente diz "compra".
        self.assertEqual(lead_profile.label("intent", "BUY"), "compra")
        self.assertEqual(lead_profile.label("urgency", "high"), "alta")
        self.assertEqual(lead_profile.label("region", "Copacabana"), "Copacabana")

    def test_has_contact(self):
        self.assertTrue(lead_profile.has_contact({"phone": "(21) 99999-9999"}))
        self.assertTrue(lead_profile.has_contact({"email": "a@b.com"}))
        self.assertFalse(lead_profile.has_contact({"email": "undefined"}))
        self.assertFalse(lead_profile.has_contact({}))
        self.assertFalse(lead_profile.has_contact(None))


class TestMemoryBasics(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.memory = ConversationMemory(InMemoryStore(), clock=self.clock)

    def test_new_lead_starts_empty(self):
        self.assertFalse(self.memory.exists("lead-1"))
        self.assertEqual(self.memory.profile("lead-1"), {})
        self.assertEqual(self.memory.message_count("lead-1"), 0)

    def test_records_and_returns_person_1_format(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.memory.record_message("lead-1", "assistant", "olá!")

        self.assertEqual(
            self.memory.history("lead-1"),
            [{"role": "user", "content": "oi"},
             {"role": "assistant", "content": "olá!"}],
        )

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(ValueError):
            self.memory.record_message("lead-1", "system", "oi")

    def test_window_limits_the_history(self):
        for i in range(25):
            self.memory.record_message("lead-1", "user", "msg %d" % i)

        self.assertEqual(len(self.memory.history("lead-1")), DEFAULT_WINDOW)
        self.assertEqual(len(self.memory.history("lead-1", window=3)), 3)
        self.assertEqual(len(self.memory.history("lead-1", window=None)), 25)
        self.assertEqual(
            self.memory.history("lead-1", window=1)[0]["content"], "msg 24"
        )

    def test_malicious_lead_id_is_rejected(self):
        # O lead_id vira nome de arquivo no store em disco.
        for bad in ["../../.env", "a/b", "", "x" * 65, None, "lead 1"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_lead_id(bad)

        self.assertEqual(validate_lead_id("lead-1_ABC"), "lead-1_ABC")

    def test_leads_lists_what_exists(self):
        self.memory.record_message("lead-b", "user", "oi")
        self.memory.record_message("lead-a", "user", "oi")
        self.assertEqual(self.memory.leads(), ["lead-a", "lead-b"])


class TestDefaultUrgencyIsNotEvidence(unittest.TestCase):
    """A `extrair_urgencia` da Pessoa 1 devolve "baixa" para qualquer texto.

    Ela nao tem ramo "undefined" nem lista de palavras de urgencia baixa, entao
    um "oi, meu nome e Marcos" produz urgencia baixa. Deixar isso entrar trava
    o campo para sempre, porque o perfil e monotonico.
    """

    def test_greeting_does_not_set_urgency(self):
        perfil = lead_profile.from_agent(
            {"nome": "Marcos", "urgencia": "baixa"},
            message="Oi, meu nome e Marcos",
        )

        self.assertEqual(perfil, {"name": "Marcos"})

    def test_real_low_urgency_is_kept(self):
        perfil = lead_profile.from_agent(
            {"urgencia": "baixa"},
            message="sem pressa, quero comprar so ano que vem",
        )

        self.assertEqual(perfil["urgency"], "low")

    def test_high_urgency_is_never_filtered(self):
        perfil = lead_profile.from_agent(
            {"urgencia": "alta"}, message="preciso mudar essa semana",
        )

        self.assertEqual(perfil["urgency"], "high")

    def test_medium_urgency_is_never_filtered(self):
        perfil = lead_profile.from_agent(
            {"urgencia": "media"}, message="qualquer coisa",
        )

        self.assertEqual(perfil["urgency"], "medium")

    def test_without_a_message_nothing_is_discarded(self):
        # Chamador programatico remontando um perfil ja validado nao pode
        # perder dado por causa de uma defesa que nao tem como julgar.
        perfil = lead_profile.from_agent({"urgencia": "baixa"})

        self.assertEqual(perfil["urgency"], "low")

    def test_cues_are_accent_and_case_tolerant(self):
        for texto in ("SEM PRESSA", "Nao tenho pressa", "Não tenho pressa",
                      "so olhando por enquanto", "Só Olhando"):
            perfil = lead_profile.from_agent({"urgencia": "baixa"}, message=texto)
            self.assertEqual(perfil.get("urgency"), "low", texto)


class TestUrgencyDoesNotLockTheProfile(unittest.TestCase):
    """O efeito da defesa onde ele importa: o campo continua sendo perseguido."""

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore())

    def test_greeting_leaves_urgency_open_for_collection(self):
        dados = {"nome": "Marcos", "urgencia": "baixa", "intencao": "undefined"}
        self.memory.update_profile("lead-1", dados, message="Oi, meu nome e Marcos")

        perfil = self.memory.profile("lead-1")
        self.assertNotIn("urgency", perfil)

    def test_urgency_can_still_be_learned_later(self):
        self.memory.update_profile(
            "lead-2", {"urgencia": "baixa"}, message="Oi!",
        )
        self.memory.update_profile(
            "lead-2", {"urgencia": "alta"}, message="preciso urgente",
        )

        self.assertEqual(self.memory.profile("lead-2")["urgency"], "high")

    def test_the_agent_keeps_pursuing_urgency_after_a_greeting(self):
        # Com a urgencia travada em "low" no primeiro turno, `next_to_collect`
        # pulava o campo e o agente nunca perguntava sobre prazo.
        dados = {
            "intencao": "COMPRA", "regiao": "Copacabana", "quartos": "2",
            "preco_faixa": "900k", "urgencia": "baixa",
        }
        self.memory.update_profile("lead-3", dados, message="quero comprar em Copacabana")

        self.assertEqual(lead_profile.next_to_collect(self.memory.profile("lead-3")), "urgency")


class TestMonotonicProfile(unittest.TestCase):
    """O motivo principal de a memória existir."""

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def test_undefined_never_erases_a_known_value(self):
        # A extração da Pessoa 1 é sem estado e pode regredir entre chamadas.
        # A memória protege o que já foi descoberto.
        self.memory.update_profile("lead-1", {"regiao": "Copacabana", "bedrooms": "3"})
        self.memory.update_profile("lead-1", {"regiao": "undefined", "bedrooms": "undefined"})

        profile = self.memory.profile("lead-1")
        self.assertEqual(profile["region"], "Copacabana")
        self.assertEqual(profile["bedrooms"], "3")

    def test_a_new_field_is_flagged_as_new(self):
        changes = self.memory.update_profile("lead-1", {"intencao": "COMPRA"})

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["kind"], "new")
        self.assertEqual(changes[0]["to"], "BUY")

    def test_a_lead_correction_is_recorded(self):
        # "o lead subiu o orçamento" é sinal de compra que o corretor quer ver.
        self.memory.update_profile("lead-1", {"preco_faixa": "500k"})
        changes = self.memory.update_profile("lead-1", {"preco_faixa": "800k"})

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["kind"], "correction")
        self.assertEqual(changes[0]["from"], "500k")
        self.assertEqual(changes[0]["to"], "800k")
        self.assertEqual(self.memory.profile("lead-1")["price_range"], "800k")

    def test_repeated_value_produces_no_change(self):
        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        self.assertEqual(self.memory.update_profile("lead-1", {"intencao": "COMPRA"}), [])

    def test_field_outside_the_contract_is_ignored(self):
        self.memory.update_profile("lead-1", {"cor_favorita": "azul"})
        self.assertNotIn("cor_favorita", self.memory.profile("lead-1"))

    def test_counts_revisions_per_field(self):
        for value in ("2", "3", "4"):
            self.memory.update_profile("lead-1", {"bedrooms": value})

        meta = self.memory.state("lead-1")["profile_meta"]["bedrooms"]
        self.assertEqual(meta["revisions"], 3)

    def test_empty_first_message_profile_does_not_break(self):
        self.assertEqual(self.memory.update_profile("lead-1", None), [])
        self.assertEqual(self.memory.update_profile("lead-1", {}), [])

    def test_known_fields(self):
        self.memory.update_profile(
            "lead-1", {"intencao": "COMPRA", "regiao": "undefined", "bedrooms": "3"}
        )
        self.assertEqual(self.memory.known_fields("lead-1"), ["bedrooms", "intent"])


class TestShownProperties(unittest.TestCase):

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def test_accumulates_without_repeating(self):
        self.memory.record_shown_properties("lead-1", ["IMV-0001", "IMV-0002"])
        self.memory.record_shown_properties("lead-1", ["IMV-0002", "IMV-0003"])

        self.assertEqual(
            self.memory.shown_properties("lead-1"),
            ["IMV-0001", "IMV-0002", "IMV-0003"],
        )

    def test_preserves_presentation_order(self):
        self.memory.record_shown_properties("lead-1", ["IMV-0009"])
        self.memory.record_shown_properties("lead-1", ["IMV-0001"])
        self.assertEqual(
            self.memory.shown_properties("lead-1"), ["IMV-0009", "IMV-0001"]
        )


class TestIncrementalSummary(unittest.TestCase):

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def test_short_conversation_needs_no_summary(self):
        for i in range(5):
            self.memory.record_message("lead-1", "user", "msg %d" % i)
        self.assertFalse(self.memory.needs_summary("lead-1"))

    def test_long_conversation_asks_for_a_summary(self):
        for i in range(SUMMARY_THRESHOLD + 5):
            self.memory.record_message("lead-1", "user", "msg %d" % i)
        self.assertTrue(self.memory.needs_summary("lead-1"))

    def test_summary_preserves_the_live_window(self):
        # As últimas mensagens continuam literais; só o miolo antigo é resumido.
        for i in range(30):
            self.memory.record_message("lead-1", "user", "msg %d" % i)

        to_summarize = self.memory.messages_to_summarize("lead-1")
        self.assertEqual(len(to_summarize), 30 - DEFAULT_WINDOW)
        self.assertEqual(to_summarize[0]["content"], "msg 0")

    def test_after_summarising_it_does_not_ask_again(self):
        for i in range(30):
            self.memory.record_message("lead-1", "user", "msg %d" % i)

        self.memory.set_summary("lead-1", "Lead quer 3 quartos.", up_to_index=20)

        self.assertEqual(self.memory.summary("lead-1"), "Lead quer 3 quartos.")
        self.assertFalse(self.memory.needs_summary("lead-1"))

    def test_summary_index_is_clamped(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.memory.set_summary("lead-1", "x", up_to_index=999)
        self.assertEqual(self.memory.state("lead-1")["summarized_up_to"], 1)


class TestDodgedFields(unittest.TestCase):
    """O lead que não responde a uma pergunta específica.

    Só a memória consegue detectar isso: o agente recebe uma janela de dez
    mensagens e nenhum contador, então repetiria a mesma pergunta para sempre.
    """

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def conversar(self, *mensagens):
        """Mensagens do lead que não respondem nada, com o agente insistindo."""
        for m in mensagens:
            self.memory.record_message("lead-1", "user", m)
            self.memory.record_message("lead-1", "assistant", "e então?")

    def test_poucas_mensagens_nao_marcam_esquiva(self):
        self.conversar("oi", "bom dia")
        self.assertEqual(self.memory.dodged_fields("lead-1"), {})

    def test_persegue_um_campo_por_vez(self):
        # Regressão: a primeira versão contava esquiva para TODO campo
        # desconhecido, então após 3 mensagens os seis campos eram marcados de
        # uma vez e o contexto virava "desista de todos os assuntos", que é
        # instrução pior do que o problema original.
        self.conversar("oi", "bom dia", "tudo bem?", "kkkk")
        dodged = self.memory.dodged_fields("lead-1")

        self.assertEqual(list(dodged), ["intent"])

    def test_campo_perseguido_vira_esquiva_no_limiar(self):
        self.conversar("oi", "bom dia", "tudo bem?")
        self.assertEqual(self.memory.dodged_fields("lead-1"), {"intent": 3})

    def test_responder_zera_e_passa_para_o_proximo(self):
        self.conversar("oi", "bom dia", "tudo bem?")
        self.assertIn("intent", self.memory.dodged_fields("lead-1"))

        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        self.assertEqual(self.memory.dodged_fields("lead-1"), {})

        # Agora a perseguição é a região, e a contagem dela começa do zero.
        self.conversar("sei lá", "depois eu vejo", "kkkk")
        self.assertEqual(self.memory.dodged_fields("lead-1"), {"region": 3})

    def test_so_mensagem_do_lead_conta(self):
        # Dez falas do agente não são dez perguntas ignoradas: sem resposta do
        # lead, não houve esquiva nenhuma.
        for _ in range(10):
            self.memory.record_message("lead-1", "assistant", "e o orçamento?")

        self.assertEqual(self.memory.dodged_fields("lead-1"), {})

    def test_nome_e_email_nunca_sao_perseguidos(self):
        # Nome vem de graça quando o lead se apresenta, e e-mail é alternativa
        # ao telefone. Nenhum dos dois é perguntado, então nenhum dos dois pode
        # ser contado como esquivado.
        self.memory.update_profile("lead-1", {
            "intencao": "COMPRA", "regiao": "Copacabana", "quartos": "3",
            "preco_faixa": "500k", "urgencia": "alta", "telefone": "(21) 99999-9999",
        })
        self.conversar("oi", "bom dia", "tudo bem?", "kkkk", "e aí")

        self.assertEqual(self.memory.dodged_fields("lead-1"), {})

    def test_limiar_e_configuravel(self):
        self.conversar("oi", "bom dia")
        self.assertEqual(self.memory.dodged_fields("lead-1"), {})
        self.assertIn("intent", self.memory.dodged_fields("lead-1", threshold=2))

    def test_contexto_manda_parar_de_perguntar(self):
        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        self.conversar("bom dia", "tudo bem?", "kkkk")

        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("O LEAD NÃO RESPONDEU SOBRE", context)
        self.assertIn("região (3 mensagens)", context)
        self.assertIn("Pare de perguntar isso", context)

    def test_contexto_continua_apontando_o_que_falta(self):
        # Regressão: com todos os campos marcados de uma vez, a linha "AINDA
        # FALTA DESCOBRIR" sumia e o agente ficava sem próximo assunto.
        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        self.conversar("bom dia", "tudo bem?", "kkkk")

        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("AINDA FALTA DESCOBRIR", context)
        self.assertIn("quartos", context)

    def test_contexto_nao_se_contradiz(self):
        # Mandar "descubra a região" e "não insista na região" no mesmo prompt
        # é instrução contraditória, e o modelo escolhe uma das duas ao acaso.
        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        self.conversar("bom dia", "tudo bem?", "kkkk")

        context = self.memory.build_context("lead-1", mask=False)
        linha_faltando = context.split("AINDA FALTA DESCOBRIR:")[1].split("\n")[0]

        self.assertNotIn("região", linha_faltando)

    def test_estado_antigo_sem_o_campo_nao_quebra(self):
        # Conversas gravadas antes deste recurso existir não têm "unanswered".
        state = self.memory.state("lead-1")
        del state["unanswered"]
        self.memory.store.write("lead-1", state)

        self.assertEqual(self.memory.dodged_fields("lead-1"), {})
        self.memory.record_message("lead-1", "user", "oi")


class TestContext(unittest.TestCase):

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def test_context_lists_what_is_already_known(self):
        self.memory.update_profile(
            "lead-1", {"intencao": "COMPRA", "regiao": "Copacabana", "bedrooms": "3"}
        )
        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("Copacabana", context)
        self.assertIn("compra", context)
        self.assertIn("não pergunte de novo", context)

    def test_context_points_out_what_is_missing(self):
        self.memory.update_profile("lead-1", {"intencao": "COMPRA"})
        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("AINDA FALTA DESCOBRIR", context)
        self.assertIn("região", context)

    def test_context_avoids_re_presenting_a_property(self):
        self.memory.record_shown_properties("lead-1", ["IMV-0007"])
        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("IMV-0007", context)
        self.assertIn("Não os apresente como novidade", context)

    def test_context_includes_the_summary(self):
        self.memory.set_summary("lead-1", "Lead sumiu depois da proposta.", 0)
        self.assertIn(
            "Lead sumiu depois da proposta.",
            self.memory.build_context("lead-1", mask=False),
        )

    def test_context_warns_about_followups(self):
        self.memory.record_followup("lead-1")
        self.memory.record_followup("lead-1")
        context = self.memory.build_context("lead-1", mask=False)

        self.assertIn("2 follow-up", context)
        self.assertIn("Não insista", context)

    def test_masked_context_leaks_no_pii(self):
        self.memory.update_profile(
            "lead-1", {"nome": "João", "email": "joao@x.com", "intencao": "COMPRA"}
        )
        context = self.memory.build_context("lead-1", mask=True)

        self.assertNotIn("joao@x.com", context)
        self.assertNotIn("João", context)
        self.assertIn("[EMAIL_1]", context)
        self.assertIn("compra", context)

    def test_lead_with_nothing_gets_an_empty_context(self):
        self.assertEqual(self.memory.build_context("lead-1"), "")


class TestTurnCycle(unittest.TestCase):

    def setUp(self):
        self.memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

    def test_turn_delivers_a_masked_message(self):
        turn = self.memory.start_turn("lead-1", "sou o Pedro, pedro@x.com")

        self.assertNotIn("pedro@x.com", turn.message)
        self.assertIn("[EMAIL_1]", turn.message)
        self.assertEqual(turn.original_message, "sou o Pedro, pedro@x.com")

    def test_name_is_masked_on_the_introduction_itself(self):
        # Regressão: o nome só era mascarado depois de entrar no perfil, então
        # vazava em claro para o LLM exatamente na mensagem em que o lead se
        # apresenta, que é a que mais precisa de proteção.
        turn = self.memory.start_turn("lead-1", "Oi! Meu nome é João Pereira")

        self.assertNotIn("João", turn.message)
        self.assertNotIn("Pereira", turn.message)
        self.assertIn("[NOME_1]", turn.message)

    def test_name_reaches_the_profile_even_though_masked_for_the_llm(self):
        # Consequência do teste acima: a Pessoa 1 lê "[NOME_1]" e não extrai
        # nome nenhum, então a memória precisa extrair do texto original.
        turn = self.memory.start_turn("lead-1", "Sou a Beatriz")
        self.memory.finish_turn(
            "lead-1", turn, "Prazer, [NOME_1]!", {"nome": "undefined"}
        )

        self.assertEqual(self.memory.profile("lead-1")["name"], "Beatriz")

    def test_turn_history_excludes_the_current_message(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.memory.record_message("lead-1", "assistant", "olá")
        turn = self.memory.start_turn("lead-1", "quero comprar")

        self.assertEqual(len(turn.history), 2)
        self.assertNotIn("quero comprar", [m["content"] for m in turn.history])

    def test_llm_reply_comes_back_with_real_data(self):
        self.memory.update_profile("lead-1", {"nome": "Pedro"})
        turn = self.memory.start_turn("lead-1", "meu email é pedro@x.com")

        reply, _ = self.memory.finish_turn(
            "lead-1", turn, "Combinado, [NOME_1]! Envio para [EMAIL_1].", {}
        )

        self.assertEqual(reply, "Combinado, Pedro! Envio para pedro@x.com.")
        self.assertNotIn("[", reply)

    def test_email_is_extracted_even_though_masked_for_the_llm(self):
        # A extração da Pessoa 1 roda sobre o texto mascarado e fica cega para
        # e-mail e telefone. A memória extrai do original, que só ela tem.
        turn = self.memory.start_turn(
            "lead-1", "me chama no (21) 98765-4321 ou ana@x.com"
        )
        self.memory.finish_turn(
            "lead-1", turn, "ok", {"email": "undefined", "telefone": "undefined"}
        )

        profile = self.memory.profile("lead-1")
        self.assertEqual(profile["email"], "ana@x.com")
        self.assertIn("98765-4321", profile["phone"])

    def test_profile_extracted_from_masked_text_is_restored(self):
        self.memory.update_profile("lead-1", {"nome": "Pedro"})
        turn = self.memory.start_turn("lead-1", "sim, sou o Pedro")

        # A Pessoa 1 leu "[NOME_1]" e devolveu isso como nome.
        self.memory.finish_turn("lead-1", turn, "ok", {"nome": "[NOME_1]"})

        self.assertEqual(self.memory.profile("lead-1")["name"], "Pedro")

    def test_a_full_turn_records_both_messages(self):
        turn = self.memory.start_turn("lead-1", "oi")
        self.memory.finish_turn("lead-1", turn, "olá!", {"intencao": "COMPRA"})

        self.assertEqual(self.memory.message_count("lead-1"), 2)
        self.assertEqual(self.memory.profile("lead-1")["intent"], "BUY")

    def test_alias_stays_stable_across_several_turns(self):
        t1 = self.memory.start_turn("lead-1", "escreve pra ana@x.com")
        self.memory.finish_turn("lead-1", t1, "ok", {})
        t2 = self.memory.start_turn("lead-1", "confirma ana@x.com?")

        self.assertIn("[EMAIL_1]", t2.message)
        self.assertNotIn("[EMAIL_2]", t2.message)

    def test_turn_mapping_covers_history_and_context(self):
        # Regressão: histórico e contexto eram mascarados contra mapas
        # próprios, então apelidos criados por eles não voltavam no turno e a
        # resposta do LLM chegaria ao lead com "[EMAIL_1]" cru.
        t1 = self.memory.start_turn("lead-1", "meu email é bia@x.com")
        self.memory.finish_turn("lead-1", t1, "ok", {})

        t2 = self.memory.start_turn("lead-1", "e aí?")

        self.assertIn("[EMAIL_1]", t2.history[0]["content"])
        self.assertIn("[EMAIL_1]", t2.mapping)
        self.assertEqual(t2.mapping["[EMAIL_1]"], "bia@x.com")


class TestLGPD(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.memory = ConversationMemory(InMemoryStore(), clock=self.clock)

    def test_consent(self):
        self.assertFalse(self.memory.has_consent("lead-1"))

        self.memory.record_consent("lead-1", True, "atendimento imobiliário")
        self.assertTrue(self.memory.has_consent("lead-1"))

        record = self.memory.state("lead-1")["consent"]
        self.assertEqual(record["purpose"], "atendimento imobiliário")
        self.assertIn("2026", record["at"])

    def test_consent_denied(self):
        self.memory.record_consent("lead-1", False, "marketing")
        self.assertFalse(self.memory.has_consent("lead-1"))

    def test_forget_wipes_everything(self):
        turn = self.memory.start_turn("lead-1", "sou o João, joao@x.com")
        self.memory.finish_turn("lead-1", turn, "ok", {"nome": "João"})

        self.assertTrue(self.memory.forget("lead-1"))
        self.assertFalse(self.memory.exists("lead-1"))
        self.assertEqual(self.memory.profile("lead-1"), {})
        # O mapa de apelidos guarda PII em claro; some junto.
        self.assertEqual(self.memory.state("lead-1")["alias_map"], {})

    def test_forget_a_nonexistent_lead(self):
        self.assertFalse(self.memory.forget("does-not-exist"))

    def test_export_returns_everything(self):
        turn = self.memory.start_turn("lead-1", "oi")
        self.memory.finish_turn("lead-1", turn, "olá", {"intencao": "COMPRA"})

        package = self.memory.export("lead-1")
        self.assertEqual(package["profile"]["intent"], "BUY")
        self.assertEqual(len(package["messages"]), 2)
        self.assertIn("created_at", package)

    def test_export_a_nonexistent_lead(self):
        self.assertIsNone(self.memory.export("does-not-exist"))

    def test_retention_purges_a_cold_lead(self):
        self.memory.record_message("old", "user", "oi")
        self.clock.advance(days=200)
        self.memory.record_message("recent", "user", "oi")

        self.assertEqual(self.memory.expired(), ["old"])
        self.assertEqual(self.memory.purge_expired(), ["old"])
        self.assertFalse(self.memory.exists("old"))
        self.assertTrue(self.memory.exists("recent"))

    def test_retention_honours_a_custom_window(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.clock.advance(days=40)

        self.assertEqual(self.memory.expired(days=90), [])
        self.assertEqual(self.memory.expired(days=30), ["lead-1"])

    def test_log_carries_no_personal_data(self):
        turn = self.memory.start_turn("lead-1", "sou o João, joao@x.com")
        self.memory.finish_turn("lead-1", turn, "ok", {"nome": "João"})

        record = self.memory.log_summary("lead-1")
        self.assertNotIn("João", record)
        self.assertNotIn("joao@x.com", record)
        self.assertIn("lead-1", record)


class TestFollowUpFields(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.memory = ConversationMemory(InMemoryStore(), clock=self.clock)

    def test_hours_of_silence(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.clock.advance(hours=48)
        self.assertAlmostEqual(self.memory.hours_of_silence("lead-1"), 48.0, places=3)

    def test_followup_counter(self):
        self.memory.record_followup("lead-1")
        self.memory.record_followup("lead-1")

        state = self.memory.state("lead-1")
        self.assertEqual(state["followups_sent"], 2)
        self.assertIsNotNone(state["last_followup_at"])


class TestJsonFileStore(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.memory = ConversationMemory(JsonFileStore(self.folder), clock=FakeClock())

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_survives_a_restart(self):
        turn = self.memory.start_turn("lead-1", "quero comprar na Tijuca")
        self.memory.finish_turn(
            "lead-1", turn, "legal!", {"intencao": "COMPRA", "regiao": "Tijuca"}
        )

        other = ConversationMemory(JsonFileStore(self.folder))

        self.assertEqual(other.profile("lead-1")["region"], "Tijuca")
        self.assertEqual(other.message_count("lead-1"), 2)

    def test_one_file_per_lead(self):
        self.memory.record_message("lead-a", "user", "oi")
        self.memory.record_message("lead-b", "user", "oi")

        self.assertEqual(sorted(os.listdir(self.folder)),
                         ["lead-a.json", "lead-b.json"])

    def test_forget_removes_the_file_from_disk(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.assertTrue(self.memory.forget("lead-1"))
        self.assertEqual(os.listdir(self.folder), [])

    def test_write_leaves_no_temporary_file(self):
        self.memory.record_message("lead-1", "user", "oi")
        self.assertFalse(any(n.endswith(".tmp") for n in os.listdir(self.folder)))

    def test_malicious_lead_id_cannot_escape_the_directory(self):
        with self.assertRaises(ValueError):
            self.memory.record_message("../escaped", "user", "oi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
