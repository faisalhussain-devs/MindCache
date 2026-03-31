import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api_server
from retrieval.structs import RetrievalResult


def make_topic(topic_id, name, level, parent_id=None, description=""):
    return SimpleNamespace(
        id=topic_id,
        name=name,
        level=level,
        parent_id=parent_id,
        description=description,
    )


class TestApiServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api_server.app)

    def test_root_route_serves_graph_ui(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("MindCache Workbench", response.text)

    def test_graph_route_serves_graph_explorer_ui(self):
        response = self.client.get("/graph")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Graph Explorer", response.text)

    def test_tree_and_node_tree_endpoints_return_serialized_subtrees(self):
        root = make_topic(1, "Work", 0, description="Root topic")
        child = make_topic(2, "Project Atlas", 1, parent_id=1, description="Branch topic")
        leaf = make_topic(3, "Launch Plan", 2, parent_id=2, description="Leaf topic")
        fake_tree = SimpleNamespace(
            topic_by_id={1: root, 2: child, 3: leaf},
            children_map={
                None: [root],
                1: [child],
                2: [leaf],
                3: [],
            },
        )

        with patch.object(api_server, "get_tree_cache", return_value=fake_tree):
            response = self.client.get("/tree?depth=2")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["roots"][0]["id"], 1)
            self.assertEqual(payload["roots"][0]["children"][0]["id"], 2)
            self.assertEqual(payload["roots"][0]["children"][0]["children"][0]["id"], 3)

            node_response = self.client.get("/node/2/tree?depth=1")
            self.assertEqual(node_response.status_code, 200)
            node_payload = node_response.json()
            self.assertEqual(node_payload["node"]["id"], 2)
            self.assertEqual(node_payload["node"]["children"][0]["id"], 3)

    def test_retrieve_passes_selected_nodes_by_level_and_returns_trace(self):
        fake_pipeline = MagicMock()
        fake_pipeline.retrieve.return_value = RetrievalResult(
            context="Summary: constrained retrieval result",
            trace={
                "selected_nodes_by_level": {"0": [1], "1": [2]},
                "constraint_path_ids": [1, 2],
                "starting_node_ids": [2],
                "root_ids": [1],
                "candidate_topic_ids": [2, 3],
                "selected_topic_ids": [3],
            },
        )

        with patch.object(api_server, "get_pipeline", return_value=fake_pipeline):
            response = self.client.post(
                "/retrieve",
                json={
                    "query": "What did I decide about Atlas?",
                    "selected_nodes_by_level": {
                        "0": [1],
                        "1": [2],
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["trace"]["starting_node_ids"], [2])
        fake_pipeline.retrieve.assert_called_once_with(
            current_prompt="What did I decide about Atlas?",
            selected_nodes_by_level={"0": [1], "1": [2]},
        )


if __name__ == "__main__":
    unittest.main()
