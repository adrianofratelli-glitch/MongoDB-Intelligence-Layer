"""Fast unit checks for the agent's least-privilege and context policies."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-import-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
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
        self.assertEqual(safe["filter"], {"order_id": "PED-1001"})
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
            agent._write_denial("update-many", "POC.support_orders", tool_input)
        )
        self.assertEqual(tool_input["filter"], {"order_id": "PED-1002"})
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


if __name__ == "__main__":
    unittest.main()
