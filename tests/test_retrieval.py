import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from Database.db_setup import Base, engine, Topic, EpisodicMemory, DecisionMemory, MemoryRegistry
from retrieval.structs import RetrievalContext

class TestRetrievalPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # PATCH BEFORE IMPORTS to avoid loading heavy deps
        cls.embedder_mock = MagicMock()
        cls.modules_patcher = patch.dict(sys.modules, {'Database.embedder': cls.embedder_mock})
        cls.modules_patcher.start()

        # Database setup
        cls.test_db_url = 'sqlite:///:memory:'
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    @classmethod
    def tearDownClass(cls):
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

    def tearDown(self):
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
            with patch.object(self.retriever.search, 'scan', return_value=root):
                with patch.object(self.retriever.descent, 'descend', return_value=[{"topic": child, "score": 0.9}]):
                    with patch.object(self.retriever.refiner, 'refine', return_value={"selected_topics": [{"chain": ["Coding", "Python"], "depth": "leaf"}]}):
                        
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

if __name__ == '__main__':
    unittest.main()
