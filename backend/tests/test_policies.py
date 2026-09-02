"""Fast unit checks for the agent's least-privilege and context policies."""

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-import-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import cache  # noqa: E402
import guardrails  # noqa: E402
import guidance  # noqa: E402
import memory  # noqa: E402


class ToolPolicyTests(unittest.TestCase):
    def test_order_read_requires_scalar_id_and_rewrites_projection(self):
        malicious = {"filter": {"order_id": {"$ne": None}}}
        self.assertIsNotNone(
            agent._read_denial("find", "POC.support_orders", malicious, "conv", "user")
        )

        safe = {"filter": {"order_id": "PED-1001", "$or": [{"status": {"$exists": True}}]}}
        self.assertIsNone(
            agent._read_denial("find", "POC.support_orders", safe, "conv", "user")
        )
        # ownership: o filtro reescrito prende a leitura ao usuário do turno
        self.assertEqual(safe["filter"],
                         {"order_id": "PED-1001", "owner_user_key": "user"})
        self.assertNotIn("customer_name", safe["projection"])

    def test_order_write_is_reduced_to_approved_status(self):
        tool_input = {
            "filter": {"order_id": "PED-1002", "status": {"$ne": "cancelado"}},
            "update": {
                "$set": {"status": "reembolso_solicitado", "unit_price": 0},
                "$unset": {"timeline": ""},
            },
        }
        self.assertIsNone(
            agent._write_denial("update-many", "POC.support_orders", tool_input, "user")
        )
        self.assertEqual(tool_input["filter"],
                         {"order_id": "PED-1002", "owner_user_key": "user"})
        self.assertEqual(
            tool_input["update"], {"$set": {"status": "reembolso_solicitado"}}
        )

    def test_graph_pipeline_is_rebuilt_server_side_and_bound_to_owner(self):
        # O modelo manda um $graphLookup inventado, apontando para outra collection e sem
        # filtro de dono. Nada disso sobrevive: o servidor remonta o pipeline inteiro.
        tool_input = {
            "database": "POC", "collection": "support_orders",
            "pipeline": [
                {"$match": {"order_id": "PED-1005"}},
                {"$graphLookup": {"from": "app_users", "startWith": "$owner_user_key",
                                  "connectFromField": "owner_user_key",
                                  "connectToField": "_id", "as": "leak", "maxDepth": 50}},
            ],
        }
        self.assertIsNone(
            agent._read_denial("aggregate", "POC.support_orders", tool_input, "conv", "user")
        )
        pipeline = tool_input["pipeline"]
        self.assertEqual(pipeline[0]["$match"],
                         {"order_id": "PED-1005", "owner_user_key": "user"})
        lookup = pipeline[1]["$graphLookup"]
        self.assertEqual(lookup["from"], "support_orders")
        self.assertEqual(lookup["connectFromField"], "replacement_order_id")
        self.assertEqual(lookup["restrictSearchWithMatch"], {"owner_user_key": "user"})
        self.assertLessEqual(lookup["maxDepth"], 6)
        # customer_name não pode voltar nem na raiz nem nos elos da cadeia.
        projected = pipeline[2]["$project"]
        self.assertNotIn("customer_name", projected)
        self.assertNotIn("customer_name", projected["chain"]["$map"]["in"])

    def test_graph_traversal_requires_a_scalar_starting_order(self):
        for bad in (
            {"pipeline": [{"$match": {"order_id": {"$ne": None}}}]},
            {"pipeline": [{"$match": {"owner_user_key": "outro"}}]},
            {"pipeline": []},
            {"order_id": "PED-XX"},
        ):
            with self.subTest(bad=bad):
                self.assertIsNotNone(
                    agent._read_denial("aggregate", "POC.support_orders", bad, "conv", "user")
                )

    def test_chain_summary_replaces_raw_traversal_output(self):
        raw = json.dumps([{
            "order_id": "PED-1005", "product_name": "JBL Quantum 910 Wireless",
            "sku": "JBL-Q910", "status": "troca_solicitada",
            "chain": [
                {"order_id": "PED-1006", "sku": "JBL-Q910", "depth": 0, "reason": "mic"},
                {"order_id": "PED-1007", "sku": "JBL-Q910", "depth": 1, "reason": "mic"},
            ],
        }])
        summary = json.loads(agent._summarize_chain_text(raw))
        self.assertEqual(summary["replacements"], 2)
        self.assertEqual(summary["same_sku_count"], 3)
        self.assertTrue(summary["recurring_defect"])
        self.assertTrue(summary["needs_quality_review"])
        self.assertEqual(summary["path"], ["PED-1005", "PED-1006", "PED-1007"])

    def test_chain_summary_survives_unparseable_tool_output(self):
        self.assertEqual(agent._summarize_chain_text("MCP caiu"), "MCP caiu")

    def test_catalog_pipeline_is_replaced_with_safe_shape(self):
        tool_input = {
            "pipeline": [
                {"$vectorSearch": {
                    "index": "produtos_vector", "path": "descricao",
                    "query": "fone equivalente", "numCandidates": 999, "limit": 99,
                }},
                {"$lookup": {"from": "agent_memory", "as": "memory"}},
                {"$out": "exfiltrated"},
            ]
        }
        self.assertIsNone(
            agent._read_denial("aggregate", "POC.produtos_vector", tool_input, "conv", "user")
        )
        self.assertEqual(len(tool_input["pipeline"]), 2)
        self.assertEqual(tool_input["pipeline"][0]["$vectorSearch"]["limit"], 3)
        self.assertEqual(tool_input["pipeline"][0]["$vectorSearch"]["numCandidates"], 100)
        self.assertEqual(
            tool_input["pipeline"][1], {"$project": {"nome": 1, "preco": 1, "_id": 0}}
        )


class CacheIsolationFallbackTests(unittest.TestCase):
    """ADR-001 risco 2: mode 'vector-postfilter' isola por código de app, não
    pelo índice — cobrir explicitamente pra não vazar silenciosamente."""

    def test_global_and_own_area_are_visible(self):
        self.assertTrue(cache._area_visible(None, "financeiro"))  # FAQ seedada sem area
        self.assertTrue(cache._area_visible("global", "financeiro"))
        self.assertTrue(cache._area_visible("financeiro", "financeiro"))

    def test_other_area_is_not_visible(self):
        self.assertFalse(cache._area_visible("financeiro", "default"))
        self.assertFalse(cache._area_visible("default", "financeiro"))


class GuardrailPolicyTests(unittest.TestCase):
    def test_canonical_threshold_has_precedence(self):
        policy = {"denylist_threshold": 0.81, "vector_threshold": 0.72}
        self.assertEqual(guardrails._denylist_threshold(policy), 0.81)

    def test_legacy_vector_threshold_is_supported(self):
        self.assertEqual(
            guardrails._denylist_threshold({"vector_threshold": 0.7791}), 0.7791
        )

    def test_missing_or_invalid_threshold_does_not_use_obsolete_score_scale(self):
        self.assertIsNone(guardrails._denylist_threshold({}))
        self.assertIsNone(
            guardrails._denylist_threshold({"denylist_threshold": "invalid"})
        )


class ScopeRecoveryTests(unittest.TestCase):
    def test_temperature_is_redirected_before_agent_loop(self):
        self.assertTrue(guidance.is_obviously_out_of_scope("Qual é a temperatura hoje?"))

    def test_order_question_remains_in_scope(self):
        self.assertFalse(guidance.is_obviously_out_of_scope("Qual o status do PED-1001?"))


class MemoryPolicyTests(unittest.TestCase):
    def test_extraction_gate(self):
        self.assertTrue(memory.should_extract("Meu nome é Adriano e prefiro e-mail."))
        self.assertTrue(memory.should_extract("Tenho alergia a látex."))
        self.assertFalse(memory.should_extract("Onde está o pedido PED-1001?"))

    def test_prompt_memory_is_bounded(self):
        rendered = memory.format_for_prompt({
            "user_key": "u", "mode": "all", "total_active": 1,
            "facts": [{"fact": "x" * 5_000}],
        })
        self.assertEqual(rendered.count("x"), memory.MAX_PROMPT_MEMORY_CHARS - 2)


class WritePolicyHardeningTests(unittest.TestCase):
    def test_write_strips_extra_options_like_upsert(self):
        tool_input = {
            "database": "POC", "collection": "support_orders",
            "filter": {"order_id": "PED-1002"},
            "update": {"$set": {"status": "reembolso_solicitado"}},
            "upsert": True,  # criaria pedido fantasma se sobrevivesse
        }
        self.assertIsNone(
            agent._write_denial("update-many", "POC.support_orders", tool_input, "user")
        )
        self.assertNotIn("upsert", tool_input)
        self.assertEqual(set(tool_input), {"database", "collection", "filter", "update"})
        self.assertEqual(tool_input["filter"]["owner_user_key"], "user")

    def test_read_strips_extra_options(self):
        tool_input = {
            "database": "POC", "collection": "support_orders",
            "filter": {"order_id": "PED-1001"},
            "sort": {"unit_price": -1}, "limit": 999,
        }
        self.assertIsNone(
            agent._read_denial("find", "POC.support_orders", tool_input, "conv", "user")
        )
        self.assertNotIn("sort", tool_input)
        self.assertNotIn("limit", tool_input)

    def test_specific_order_id_rejects_operators(self):
        self.assertIsNone(agent._specific_order_id({"filter": {"order_id": {"$ne": None}}}))
        self.assertIsNone(agent._specific_order_id({"filter": {"order_id": "not-an-id"}}))
        self.assertEqual(
            agent._specific_order_id({"filter": {"order_id": "PED-1001"}}), "PED-1001"
        )


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main
        main._rate_windows.clear()

    def tearDown(self):
        self.main._rate_windows.clear()

    def test_limit_reached_raises_429(self):
        from fastapi import HTTPException
        for _ in range(self.main.RATE_LIMIT_PER_MINUTE):
            self.main.enforce_rate_limit("id-a")
        with self.assertRaises(HTTPException) as ctx:
            self.main.enforce_rate_limit("id-a")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_identities_are_independent(self):
        for _ in range(self.main.RATE_LIMIT_PER_MINUTE):
            self.main.enforce_rate_limit("id-a")
        self.main.enforce_rate_limit("id-b")  # não levanta

    def test_own_window_is_pruned_passively(self):
        # enforce_rate_limit só poda a janela DA IDENTIDADE atual (O(1) amortizado) —
        # não faz mais scan de todas as identidades a cada request.
        import time as _time
        self.main._rate_windows["id-a"].append(_time.monotonic() - 120)
        self.main.enforce_rate_limit("id-a")
        # o timestamp expirado (>60s) saiu da própria janela; só o novo ficou
        self.assertEqual(len(self.main._rate_windows["id-a"]), 1)

    def test_enforce_rate_limit_never_touches_other_identities(self):
        # ao contrário do scan antigo, uma chamada para "fresh" não mexe na
        # janela de "stale" — a limpeza de identidades inativas é responsabilidade
        # exclusiva da tarefa periódica em background (_rate_windows_janitor).
        import time as _time
        self.main._rate_windows["stale"].append(_time.monotonic() - 120)
        self.main.enforce_rate_limit("fresh")
        self.assertIn("stale", self.main._rate_windows)

    def test_janitor_prunes_dead_identities(self):
        import asyncio
        import time as _time

        async def run_one_pass():
            stop = asyncio.Event()
            self.main._rate_windows["stale"].append(_time.monotonic() - 120)
            self.main._rate_windows["fresh"].append(_time.monotonic())
            orig = self.main.RATE_WINDOWS_JANITOR_SECONDS
            self.main.RATE_WINDOWS_JANITOR_SECONDS = 0
            try:
                task = asyncio.create_task(self.main._rate_windows_janitor(stop))
                await asyncio.sleep(0.05)
                stop.set()
                await task
            finally:
                self.main.RATE_WINDOWS_JANITOR_SECONDS = orig

        asyncio.run(run_one_pass())
        self.assertNotIn("stale", self.main._rate_windows)
        self.assertIn("fresh", self.main._rate_windows)


class RuntimeSecurityTests(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main

    def test_secure_production_config_is_accepted(self):
        self.main.validate_runtime_security(
            "production", "a" * 24, "j" * 32, True, False,
            ["https://pov.example.com"],
        )

    def test_demo_issuer_is_rejected_in_production(self):
        with self.assertRaises(RuntimeError):
            self.main.validate_runtime_security(
                "production", "a" * 24, "j" * 32, True, True,
                ["https://pov.example.com"],
            )

    def test_variant_model_name_rejects_mongodb_path_injection(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self.main.VariantBody(model_name="safe.$where")

    def test_quick_chat_rejects_blank_question(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self.main.QuickChatBody(question="")


if __name__ == "__main__":
    unittest.main()
