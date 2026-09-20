"""Orçamento do cliente aplicado pelo SERVIDOR na busca de catálogo (não pelo prompt)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import agent  # noqa: E402


def _catalog_input(limit=3):
    return {"database": "POC", "collection": "produtos_vector", "connectionId": "x",
            "pipeline": [{"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                                            "query": "notebook", "numCandidates": 100,
                                            "limit": limit}}]}


def _rewrite(budget=None):
    tool_input = _catalog_input()
    denial = agent._read_denial("aggregate", "POC.produtos_vector", tool_input,
                                "conv", "user", budget_brl=budget)
    return denial, tool_input["pipeline"]


class BudgetPolicyTests(unittest.TestCase):
    def test_without_budget_pipeline_is_unchanged_shape(self):
        denial, pipeline = _rewrite(None)
        self.assertIsNone(denial)
        self.assertEqual([next(iter(s)) for s in pipeline], ["$vectorSearch", "$project"])
        self.assertEqual(pipeline[0]["$vectorSearch"]["limit"], 3)

    def test_budget_adds_server_side_price_match_and_widens_candidates(self):
        denial, pipeline = _rewrite(800)
        self.assertIsNone(denial)
        self.assertEqual([next(iter(s)) for s in pipeline],
                         ["$vectorSearch", "$match", "$limit", "$project"])
        self.assertEqual(pipeline[1], {"$match": {"preco": {"$lte": 800.0}}})
        self.assertEqual(pipeline[2], {"$limit": 3})
        # busca larga o bastante para sobrar itens depois do corte de preço
        self.assertGreater(pipeline[0]["$vectorSearch"]["limit"], 3)
        self.assertGreaterEqual(pipeline[0]["$vectorSearch"]["numCandidates"],
                                pipeline[0]["$vectorSearch"]["limit"])
        self.assertEqual(pipeline[3], {"$project": {"nome": 1, "preco": 1, "_id": 0}})

    def test_model_cannot_override_budget_with_its_own_match(self):
        tool_input = _catalog_input()
        tool_input["pipeline"].append({"$match": {"preco": {"$lte": 999999}}})
        agent._read_denial("aggregate", "POC.produtos_vector", tool_input,
                           "conv", "user", budget_brl=500)
        matches = [s for s in tool_input["pipeline"] if "$match" in s]
        self.assertEqual(matches, [{"$match": {"preco": {"$lte": 500.0}}}])

    def test_invalid_budget_values_are_ignored(self):
        for bad in (0, -10, float("nan"), float("inf"), "800"):
            with self.subTest(bad=bad):
                _, pipeline = _rewrite(bad)
                self.assertNotIn("$match", [next(iter(s)) for s in pipeline])


if __name__ == "__main__":
    unittest.main()
