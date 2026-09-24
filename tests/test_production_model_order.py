"""Regression checks for the production Claude 5.5 / GPT-6 Sol ladder."""
from pathlib import Path
import unittest

from lib import apply_stack as stack


class ProductionModelOrderTests(unittest.TestCase):
    def setUp(self):
        self.defaults = stack._load_yaml(
            str(Path(__file__).resolve().parents[1] / "stack-defaults.yaml"))
        self.patch = stack.build_persona_patch(
            self.defaults, "http://example.invalid/v1", "test-placeholder")

    def test_primary_and_exact_fallback_order_use_max(self):
        self.assertEqual(self.patch["model"]["default"], "claude-opus-5-5")
        self.assertEqual(self.patch["model"]["provider"], "custom:ccs-anthropic")
        self.assertEqual(self.defaults["models"]["primary"]["reasoning_effort"], "max")
        self.assertEqual(self.patch["agent"]["reasoning_effort"], "max")
        self.assertEqual(self.patch["agent"]["reasoning_overrides"], {})
        expected = [
            ("custom:ccs-astra", "gpt-6-sol"),
            ("custom:ccs-kimi", "kimi-k3"),
            ("custom:ccs-deepseek", "deepseek-v4-flash-0731"),
            ("custom:ccs-glm", "glm-5.2"),
        ]
        fallbacks = stack.canonical_fallbacks(self.defaults)
        self.assertEqual([(f["provider"], f["model"]) for f in fallbacks], expected)
        self.assertTrue(all(f["reasoning_effort"] == "max" for f in fallbacks))
        self.assertEqual(self.defaults["required_endpoint_models"], [
            "claude-opus-5-5", "gpt-6-sol", "claude-haiku-4-5-20251001",
            "kimi-k3", "deepseek-v4-flash-0731", "glm-5.2"])

    def test_existing_provider_names_keep_native_api_modes(self):
        providers = dict(self.patch["__providers__"])
        for alias, model, mode in [
            ("ccs-anthropic", "claude-opus-5-5", "anthropic_messages"),
            ("ccs-astra", "gpt-6-sol", "chat_completions"),
        ]:
            with self.subTest(provider=alias):
                self.assertEqual(providers[alias]["model"], model)
                self.assertEqual(providers[alias]["models"], {model: {}})
                self.assertEqual(providers[alias]["api_mode"], mode)
        self.assertEqual(providers["ccs-astra"]["extra_body"], {"reasoning_effort": "max"})

    def test_auxiliary_haiku_and_empty_effort_are_preserved(self):
        self.assertEqual(set(self.patch["auxiliary"]), set(self.defaults["auxiliary"]["tasks"]))
        for task, spec in self.patch["auxiliary"].items():
            with self.subTest(task=task):
                self.assertEqual(spec["provider"], "custom:ccs-anthropic")
                self.assertEqual(spec["model"], "claude-haiku-4-5-20251001")
                self.assertEqual(spec["reasoning_effort"], "")
                self.assertEqual(spec["fallback_chain"], [
                    {"provider": "custom:ccs-kimi", "model": "kimi-k3"},
                    {"provider": "custom:ccs-deepseek", "model": "deepseek-v4-flash-0731"},
                ])


if __name__ == "__main__":
    unittest.main()
