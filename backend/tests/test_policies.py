"""Fast unit checks for the agent's least-privilege and context policies."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-import-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import cache  # noqa: E402
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

    def test_empty_windows_are_pruned(self):
        import time as _time
        self.main._rate_windows["stale"].append(_time.monotonic() - 120)
        self.main.enforce_rate_limit("fresh")
        self.assertNotIn("stale", self.main._rate_windows)


if __name__ == "__main__":
    unittest.main()
