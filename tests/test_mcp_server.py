"""
Tests for the MindCache MCP server.

Covers:
- MCP server construction
- tool registration
- tool-to-MindCache delegation
- client isolation between server instances
"""

import pytest

pytest.importorskip("mcp")

from unittest.mock import MagicMock

from mindcache.mcp.server import create_mcp_server
from mindcache.retrieval.structs import RetrievalResult


EXPECTED_TOOLS = {
    "add_memory",
    "process_memory",
    "search_memory",
    "inspect_memories",
    "inspect_tree",
    "forget_memory",
}


def get_tools(server):
    """Return registered MCP tools keyed by name."""
    return {
        tool.name: tool
        for tool in server._tool_manager.list_tools()
    }


def make_mock_client():
    """Create a fully configured fake MindCache client."""
    client = MagicMock()

    client.add.return_value = 42

    client.process.return_value = {
        "success": 1,
        "failed": 0,
    }

    client.search.return_value = RetrievalResult(
        context="[USER] Prefers Python.",
        system_hint="Use concise python code snippets.",
        query_type="user_preference",
        trace={"selected_topic_ids": [10]},
    )

    client.inspect.side_effect = (
        lambda user_id="default", view="memories", memory_type=None:
        [
            {
                "id": 1,
                "type": "user",
                "content": "Prefers Python.",
                "topic": "Dev",
            }
        ]
        if view == "memories"
        else {"fake_tree": True}
    )

    client.forget.return_value = True

    return client


def test_create_mcp_server():
    """The factory should return a correctly named MCP server."""
    mock_client = make_mock_client()

    server = create_mcp_server(client=mock_client)

    assert server is not None
    assert server.name == "MindCache Memory Server"


def test_mcp_server_registers_expected_tools():
    """The server should expose exactly the intended MCP tools."""
    mock_client = make_mock_client()

    server = create_mcp_server(client=mock_client)
    tools = get_tools(server)

    assert set(tools) == EXPECTED_TOOLS


def test_mcp_tools_delegate_to_mindcache():
    """Each MCP tool should delegate to the injected MindCache client."""
    mock_client = make_mock_client()

    server = create_mcp_server(client=mock_client)
    tools = get_tools(server)

    # add_memory
    result = tools["add_memory"].fn(
        messages=[{"role": "user", "content": "hi"}],
        user_id="alice",
    )

    assert result == 42
    mock_client.add.assert_called_once_with(
        messages=[{"role": "user", "content": "hi"}],
        user_id="alice",
    )

    # process_memory
    result = tools["process_memory"].fn(user_id="alice")

    assert result == {
        "success": 1,
        "failed": 0,
    }
    mock_client.process.assert_called_once_with(
        user_id="alice",
    )

    # search_memory
    result = tools["search_memory"].fn(
        query="What does Alice prefer?",
        user_id="alice",
    )

    assert result == {
        "context": "[USER] Prefers Python.",
        "system_hint": "Use concise python code snippets.",
        "query_type": "user_preference",
        "trace": {"selected_topic_ids": [10]},
    }

    mock_client.search.assert_called_once_with(
        query="What does Alice prefer?",
        user_id="alice",
    )

    # inspect_memories
    result = tools["inspect_memories"].fn(
        user_id="alice",
    )

    assert result == [
        {
            "id": 1,
            "type": "user",
            "content": "Prefers Python.",
            "topic": "Dev",
        }
    ]

    mock_client.inspect.assert_called_with(
        user_id="alice",
        view="memories",
        memory_type=None,
    )

    # inspect_tree
    result = tools["inspect_tree"].fn(
        user_id="alice",
    )

    assert result == {"fake_tree": True}

    mock_client.inspect.assert_called_with(
        user_id="alice",
        view="tree",
    )

    # forget_memory
    result = tools["forget_memory"].fn(
        memory_id=1,
        user_id="alice",
    )

    assert result is True
    mock_client.forget.assert_called_once_with(
        memory_id=1,
        user_id="alice",
    )


def test_mcp_server_instances_use_independent_clients():
    """Different server instances should keep their MindCache clients isolated."""
    client_a = make_mock_client()
    client_b = make_mock_client()

    client_a.add.return_value = 100
    client_b.add.return_value = 200

    server_a = create_mcp_server(client=client_a)
    server_b = create_mcp_server(client=client_b)

    tools_a = get_tools(server_a)
    tools_b = get_tools(server_b)

    result_a = tools_a["add_memory"].fn(
        messages=[{"role": "user", "content": "A"}],
        user_id="alice",
    )

    result_b = tools_b["add_memory"].fn(
        messages=[{"role": "user", "content": "B"}],
        user_id="bob",
    )

    assert result_a == 100
    assert result_b == 200

    client_a.add.assert_called_once_with(
        messages=[{"role": "user", "content": "A"}],
        user_id="alice",
    )

    client_b.add.assert_called_once_with(
        messages=[{"role": "user", "content": "B"}],
        user_id="bob",
    )
