# -*- coding: utf-8 -*-
"""DeviceResolver: token-span match, not substring-in-token (R10 2d/2e)."""
import unittest

import DeviceResolver as dr
from core.clarification_slots import build_pending_clarification, resolve_light_target_answer


class _E:
    def __init__(self, eid, name):
        self.entity_id = eid
        self.friendly_name = name
        self.domain = "light"
        self.object_id = eid.split(".", 1)[-1]
        self.aliases = ()

    def display_name(self):
        return self.friendly_name


class DeviceResolverSubstringHonestyTests(unittest.TestCase):
    def setUp(self):
        self.lamp_a = _E("light.a", "Lampa A")

    def test_short_object_id_not_substring_of_room_word(self):
        self.assertIn("a", dr._entity_names(self.lamp_a))
        self.assertLess(dr._score_entity(self.lamp_a, "sypialnia", "light"), 0.78)
        self.assertLess(dr._score_entity(self.lamp_a, "jaśniej", "light"), 0.78)
        match = dr.resolve_device(
            "w sypialni jaśniej", domain="light", entities=[self.lamp_a]
        )
        self.assertNotEqual(match.status, "exact")
        self.assertIsNone(getattr(match, "entity", None))

    def test_standalone_token_a_still_resolves(self):
        match = dr.resolve_device("włącz a", domain="light", entities=[self.lamp_a])
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "light.a")

    def test_room_without_matching_entity_is_not_false_resolve(self):
        pending = build_pending_clarification(
            goal="w sypialni jaśniej",
            question="Które światło mam rozjaśnić?",
            intent_family="SMART_HOME",
            action="brightness_increase",
            question_type="light_target",
            missing_slots=["target"],
            known_slots={"desired": "brighter"},
            candidate_entities=[
                {"entity_id": self.lamp_a.entity_id, "name": self.lamp_a.friendly_name}
            ],
        )
        result = resolve_light_target_answer(
            pending, "w sypialni jaśniej", entities=[self.lamp_a]
        )
        self.assertNotEqual(result.status, "resolved")
        self.assertFalse(result.target)

    def test_two_bedroom_lights_stay_ambiguous(self):
        ents = [
            _E("light.sypialnia_1", "Lampa sypialnia 1"),
            _E("light.sypialnia_2", "Lampa sypialnia 2"),
        ]
        match = dr.resolve_device("w sypialni jaśniej", domain="light", entities=ents)
        self.assertEqual(match.status, "ambiguous")
        self.assertGreaterEqual(len(match.candidates), 2)



    def test_lampe_a_exact_not_ambiguous(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("lampe a", domain="light", entities=ents)
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "light.a")

    def test_zarowke_a_exact(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("zarowke a", domain="light", entities=ents)
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "light.a")

    def test_lampe_b_exact(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("lampe b", domain="light", entities=ents)
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "light.b")

    def test_klime_gree_exact(self):
        class C:
            def __init__(self, eid, name):
                self.entity_id = eid
                self.friendly_name = name
                self.domain = "climate"
                self.object_id = eid.split(".", 1)[-1]
                self.aliases = ()
            def display_name(self):
                return self.friendly_name
        ents = [C("climate.gree", "Klima Gree"), C("climate.salon", "Klima salon")]
        match = dr.resolve_device("klime gree", domain="climate", entities=ents)
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "climate.gree")

    def test_lampe_alone_still_ambiguous(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("lampe", domain="light", entities=ents)
        self.assertEqual(match.status, "ambiguous")


    def test_gree_brand_is_identifier_not_domain_word(self):
        self.assertNotIn("gree", dr.DOMAIN_WORDS["climate"])
        self.assertNotIn("pular", dr.DOMAIN_WORDS["climate"])

    def test_lampe_a_exact_without_domain_hint(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("lampe a", entities=ents)
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "light.a")

    def test_room_plus_id_does_not_skip_room_filter(self):
        ents = [self.lamp_a, _E("light.b", "Lampa B")]
        match = dr.resolve_device("w sypialni lampe a", domain="light", entities=ents)
        self.assertEqual(match.status, "none")


if __name__ == "__main__":
    unittest.main()
