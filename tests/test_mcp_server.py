"""
Tests for MindCache MCP server tools:
add_memory, process_memory, search_memory, inspect_memories, inspect_tree, forget_memory.
"""

import pytest
pytest.importorskip("mcp")
from unittest.mock import MagicMock
from mindcache.mcp.server import create_mcp_server
from mindcache.retrieval.structs import RetrievalResult


def test_create_mcp_server_initialization(tmp_path):
    db_path = str(tmp_path / "test_mcp.db")
    server = create_mcp_server(db_path=db_path)
    assert server is not None
    assert server.name == "MindCache Memory Server"


def test_mcp_server_tools_delegation(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_mcp_delegation.db")

    # Mock MindCache SDK instance methods
    mock_mc = MagicMock()
    mock_mc.add.return_value = 42
    mock_mc.process.return_value = {"success": 1, "failed": 0}
    mock_mc.search.return_value = RetrievalResult(
        context="[USER] Prefers Python.",
        system_hint="Use concise python code snippets.",
        query_type="user_preference",
        trace={"selected_topic_ids": [10]}
    )
    mock_mc.inspect.side_effect = lambda user_id="default", view="memories", memory_type=None: (
        [{"id": 1, "type": "user", "content": "Prefers Python.", "topic": "Dev"}]
        if view == "memories"
        else {"fake_tree": True}
    )
    mock_mc.forget.return_value = True

    monkeypatch.setattr("mindcache.mcp.server.MindCache", lambda **kw: mock_mc)

    server = create_mcp_server(db_path=db_path)

    # Tool getters or execution check
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    assert "add_memory" in tools
    assert "process_memory" in tools
    assert "search_memory" in tools
    assert "inspect_memories" in tools
    assert "inspect_tree" in tools
    assert "forget_memory" in tools
    assert "reset" not in tools  # Explicitly verified reset is omitted

    # Call tools directly via tool fn attribute or list
    add_fn = tools["add_memory"].fn
    job_id = add_fn(messages=[{"role": "user", "content": "hi"}], user_id="alice")
    assert job_id == 42
    mock_mc.add.assert_called_once_with(messages=[{"role": "user", "content": "hi"}], user_id="alice")

    process_fn = tools["process_memory"].fn
    proc_res = process_fn(user_id="alice")
    assert proc_res == {"success": 1, "failed": 0}

    search_fn = tools["search_memory"].fn
    search_res = search_fn(query="What does Alice prefer?", user_id="alice")
    assert search_res["context"] == "[USER] Prefers Python."
    assert search_res["system_hint"] == "Use concise python code snippets."
    assert search_res["query_type"] == "user_preference"
    assert search_res["trace"] == {"selected_topic_ids": [10]}

    inspect_m_fn = tools["inspect_memories"].fn
    m_res = inspect_m_fn(user_id="alice")
    assert len(m_res) == 1
    assert m_res[0]["content"] == "Prefers Python."

    inspect_t_fn = tools["inspect_tree"].fn
    t_res = inspect_t_fn(user_id="alice")
    assert t_res == {"fake_tree": True}

    forget_fn = tools["forget_memory"].fn
    forgot = forget_fn(memory_id=1, user_id="alice")
    assert forgot is True
    mock_mc.forget.assert_called_once_with(memory_id=1, user_id="alice")
