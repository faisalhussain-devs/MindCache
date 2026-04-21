import sys
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import Database.db_manager as db_manager_module
import Database.db_setup as db_setup_module
import api_server
import retrieval.active_path as active_path_module
import retrieval.root_descent as root_descent_module
from Database.db_setup import Base, Topic, EpisodicMemory, DecisionMemory, MemoryRegistry
from api_server import get_tree_cache
from useless.node_selector import AdaptiveNodeSelector, SelectionResult
from retrieval.structs import RetrievalContext, CandidateTopic

class TestRetrievalPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # PATCH BEFORE IMPORTS to avoid loading heavy deps
        cls.embedder_mock = MagicMock()
        cls.modules_patcher = patch.dict(sys.modules, {'Database.embedder': cls.embedder_mock})
        cls.modules_patcher.start()

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_db_path = os.path.join(cls.temp_dir.name, 'test_mindcache.db')
        cls.test_engine = create_engine(
            f"sqlite:///{cls.test_db_path}",
            connect_args={'check_same_thread': False},
        )

        db_setup_module.engine = cls.test_engine
        db_manager_module.engine = cls.test_engine
        api_server.engine = cls.test_engine
        api_server.Session = sessionmaker(bind=cls.test_engine)
        active_path_module.engine = cls.test_engine
        root_descent_module.engine = cls.test_engine

        get_tree_cache.cache_clear()
        Base.metadata.create_all(cls.test_engine)

    @classmethod
    def tearDownClass(cls):
        get_tree_cache.cache_clear()
        cls.test_engine.dispose()
        cls.temp_dir.cleanup()
        cls.modules_patcher.stop()

    def setUp(self):
        from retrieval.active_path import ActivePathRetrieval
        self.retriever = ActivePathRetrieval()
        self.session = self.retriever.Session()
        
        # Clear data
        self.session.query(DecisionMemory).delete()
        self.session.query(EpisodicMemory).delete()
        self.session.query(Topic).delete()
        self.session.commit()
        get_tree_cache.cache_clear()

    def tearDown(self):
        get_tree_cache.cache_clear()
        self.session.close()

    def test_topic_retrieval_flow(self):
        """Verify that retrieval follows the topic tree and fetches correct memories"""
        # 1. Setup Data
        root = Topic(name="Coding", level=0, description="Software development")
        self.session.add(root)
        self.session.commit()
        
        child = Topic(name="Python", level=1, parent_id=root.id, description="Python language")
        self.session.add(child)
        self.session.commit()

        # Add a decision
        # 1. Create Registry Entry
        reg = MemoryRegistry(memory_type="decision")
        self.session.add(reg)
        self.session.flush()

        # 2. Create Decision
        dec = DecisionMemory(
            id=reg.id,
            topic_id=child.id,
            content="Use PyTorch for ML",
            status="active",
            context="Standardizing on PyTorch"
        )
        self.session.add(dec)
        self.session.commit()

        # 2. Mock the pipeline phases to isolate ActivePath logic
        # We simulate that ContextBridge and RootSearch found the right entry point
        mock_ctx = RetrievalContext(current_prompt="What ML lib usage in Python?", query_text="[CURRENT] What ML lib usage in Python?", query_vector=[0.1]*1536)
        
        # Mock retrieval.active_path.ActivePathRetrieval.retrieve flow components
        with patch.object(self.retriever.bridge, 'process', return_value=mock_ctx):
            with patch.object(self.retriever.search, 'scan', return_value=[root]):
                with patch.object(self.retriever.descent, 'descend', return_value=[
                    CandidateTopic(
                        name="Python",
                        path="Coding > Python",
                        topic_id=child.id,
                        sim_score=0.9,
                        bm25_score=0.4,
                        timestamp="?",
                        is_leaf=True,
                    )
                ]):
                    with patch.object(self.retriever.refiner, 'refine', return_value={"selected_topics": [{"id": child.id, "chain": ["Coding", "Python"], "depth": "leaf"}]}):
                        
                        # 3. Execute Retrieve
                        result = self.retriever.retrieve("What ML lib usage in Python?")
                        
                        # 4. Assertions
                        self.assertIn("Use PyTorch for ML", result.context)
                        self.assertIn("[Decision:active]", result.context)
                        print("\n[Test] Context Retrieved:\n", result.context)

    def test_decision_filtering(self):
        """Verify only active/conditional decisions are returned"""
        t = Topic(name="Decisions", level=0)
        self.session.add(t)
        self.session.commit()
        
        # Helper to create decision with ID
        def create_dec(content, status):
            reg = MemoryRegistry(memory_type="decision")
            self.session.add(reg)
            self.session.flush()
            return DecisionMemory(id=reg.id, topic_id=t.id, content=content, status=status)

        d1 = create_dec("Old Decision", "superseded")
        d2 = create_dec("Bad Decision", "rejected")
        d3 = create_dec("Current Decision", "active")
        
        self.session.add_all([d1, d2, d3])
        self.session.commit()

        # Direct call to _fetch_leaf_data
        output = self.retriever._fetch_leaf_data(self.session, t)
        
        self.assertNotIn("Old Decision", output)
        self.assertNotIn("Bad Decision", output)
        self.assertIn("Current Decision", output)
        print("\n[Test] Decision Filter Output:\n", output)

    def test_selected_path_uses_deepest_selected_nodes_as_starting_frontier(self):
        root = Topic(name="Projects", level=0, description="All projects")
        self.session.add(root)
        self.session.commit()

        child = Topic(name="Atlas", level=1, parent_id=root.id, description="Atlas project")
        self.session.add(child)
        self.session.commit()

        leaf = Topic(name="Deployments", level=2, parent_id=child.id, description="Deployment memories")
        self.session.add(leaf)
        self.session.commit()

        resolved = self.retriever._resolve_selected_nodes({
            0: [root.id],
            1: [child.id],
            2: [leaf.id],
        })

        self.assertEqual(resolved["starting_node_ids"], [leaf.id])
        self.assertEqual(resolved["selected_nodes_by_level"], {
            0: [root.id],
            1: [child.id],
            2: [leaf.id],
        })
        self.assertEqual(resolved["constraint_path_ids"], [root.id, child.id, leaf.id])

    def test_adaptive_selector_falls_back_to_vectors_when_llm_fails(self):
        selector = AdaptiveNodeSelector(ai=MagicMock())
        selector.ai.generate.return_value = None

        nodes = [SimpleNamespace(id=i, name=f"Node {i}") for i in range(8)]
        scores = {0: 0.91, 1: 0.83, 2: 0.74, 3: 0.2}

        result = selector.select_nodes(
            query_text="deployment notes",
            nodes=nodes,
            max_selected=3,
            llm_trigger_count=7,
            threshold=0.5,
            vector_score=lambda node: scores.get(node.id),
            system_prompt="test",
            user_prompt_builder=lambda query_text, batch: "ignored",
            vector_limit=2,
        )

        self.assertEqual(result.strategy, "vector")
        self.assertEqual([node.id for node in result.nodes], [0, 1])
        selector.ai.generate.assert_called_once()

    def test_root_search_uses_vectors_without_llm_for_small_root_sets(self):
        roots = [
            Topic(
                name="Python",
                level=0,
                embedding=np.array([0.95, 0.05], dtype=np.float32).tobytes(),
            ),
            Topic(
                name="Travel",
                level=0,
                embedding=np.array([0.4, 0.6], dtype=np.float32).tobytes(),
            ),
            Topic(
                name="Cooking",
                level=0,
                embedding=np.array([0.1, 0.9], dtype=np.float32).tobytes(),
            ),
        ]
        self.session.add_all(roots)
        self.session.commit()

        ctx = RetrievalContext(
            current_prompt="python deployment",
            query_text="python deployment",
            query_vector=np.array([1.0, 0.0], dtype=np.float32),
        )

        with patch.object(self.retriever.search.selector.ai, "generate", return_value='{"nodes": ["Cooking"]}') as mocked_generate:
            results = self.retriever.search.scan(ctx, top_k=1)

        self.assertEqual([root.name for root in results], ["Python"])
        mocked_generate.assert_not_called()

    def test_root_descent_uses_llm_selected_children_on_wide_levels(self):
        root = Topic(name="Projects", level=0, description="Project memories")
        self.session.add(root)
        self.session.commit()

        children = []
        for idx in range(8):
            child = Topic(
                name=f"Child {idx}",
                level=1,
                parent_id=root.id,
                description=f"Branch {idx}",
                embedding=np.array([0.8, 0.2], dtype=np.float32).tobytes(),
            )
            children.append(child)
        self.session.add_all(children)
        self.session.commit()

        selected_child = children[3]
        unselected_child = children[5]

        selected_leaf = Topic(
            name="Selected Leaf",
            level=2,
            parent_id=selected_child.id,
            description="Chosen branch detail",
            embedding=np.array([1.0, 0.0], dtype=np.float32).tobytes(),
        )
        unselected_leaf = Topic(
            name="Unselected Leaf",
            level=2,
            parent_id=unselected_child.id,
            description="Should not be visited",
            embedding=np.array([1.0, 0.0], dtype=np.float32).tobytes(),
        )
        self.session.add_all([selected_leaf, unselected_leaf])
        self.session.commit()
        get_tree_cache.cache_clear()

        ctx = RetrievalContext(
            current_prompt="find chosen branch",
            query_text="find chosen branch",
            query_vector=np.array([1.0, 0.0], dtype=np.float32),
        )

        real_select_nodes = self.retriever.descent.node_selector.select_nodes

        def patched_select_nodes(*args, **kwargs):
            nodes = kwargs.get("nodes") or []
            if len(nodes) > 7:
                return SelectionResult(nodes=[selected_child], strategy="llm")
            return real_select_nodes(*args, **kwargs)

        with patch.object(
            self.retriever.descent.node_selector,
            "select_nodes",
            side_effect=patched_select_nodes,
        ):
            results = self.retriever.descent.descend([root], ctx)

        result_ids = [candidate.topic_id for candidate in results]
        self.assertIn(selected_child.id, result_ids)
        self.assertIn(selected_leaf.id, result_ids)
        self.assertNotIn(unselected_child.id, result_ids)
        self.assertNotIn(unselected_leaf.id, result_ids)

if __name__ == '__main__':
    unittest.main()
