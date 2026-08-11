"""
MindCache Model Context Protocol (MCP) Server
----------------------------------------------
Exposes the core MindCache memory SDK as structured MCP tools for AI assistants
and agent frameworks (Claude, Codex, etc.) over stdio transport.
"""

import sys
import argparse
from typing import List, Dict, Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    FastMCP = None
    _MCP_AVAILABLE = False


from mindcache import MindCache


def create_mcp_server(
    db_path: str = "mindcache.db",
    provider: str = "gemini",
    model_name: str = "gemini-2.5-flash",
) -> FastMCP:
    """
    Instantiate a FastMCP server and register MindCache memory tools.
    """
    if not _MCP_AVAILABLE:
        raise ImportError(
            "The 'mcp' package is required to run the MindCache MCP server.\n"
            "Please install it using: pip install 'mindcache[mcp]'"
        )

    mcp = FastMCP("MindCache Memory Server")
    mc = MindCache(db_path=db_path, provider=provider, model_name=model_name)

    @mcp.tool()
    def add_memory(messages: List[Dict[str, str]], user_id: str = "default") -> int:
        """
        Buffer conversation turns into the MindCache queue.

        Args:
            messages: List of turn dicts, e.g. [{"role": "user", "content": "..."}].
            user_id: Scope for user/session identifier.

        Returns:
            Ingestion Job ID (int).
        """
        return mc.add(messages=messages, user_id=user_id)

    @mcp.tool()
    def process_memory(user_id: str = "default") -> Dict[str, Any]:
        """
        Process pending queued conversations: extracts memories & updates topic tree.

        Args:
            user_id: Scope for user/session identifier.

        Returns:
            Execution summary dict containing success and failure counts.
        """
        return mc.process(user_id=user_id)

    @mcp.tool()
    def search_memory(query: str, user_id: str = "default") -> Dict[str, Any]:
        """
        Search for relevant context and memories matching a query.

        Args:
            query: Question or search query string.
            user_id: Scope for user/session identifier.

        Returns:
            Structured dictionary with 'context', 'system_hint', 'query_type', and 'trace'.
        """
        res = mc.search(query=query, user_id=user_id)
        return {
            "context": getattr(res, "context", str(res)),
            "system_hint": getattr(res, "system_hint", ""),
            "query_type": getattr(res, "query_type", ""),
            "trace": getattr(res, "trace", {}),
        }

    @mcp.tool()
    def inspect_memories(user_id: str = "default", memory_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Inspect stored memory records for a user.

        Args:
            user_id: Scope for user/session identifier.
            memory_type: Optional memory type filter ('user', 'knowledge', 'episodic', 'decision').

        Returns:
            List of memory record dictionaries.
        """
        return mc.inspect(user_id=user_id, view="memories", memory_type=memory_type)

    @mcp.tool()
    def inspect_tree(user_id: str = "default") -> Dict[str, Any]:
        """
        Inspect the dynamic hierarchical topic tree for a user.

        Args:
            user_id: Scope for user/session identifier.

        Returns:
            Refreshed hierarchical topic tree dictionary structure.
        """
        tree = mc.inspect(user_id=user_id, view="tree")
        if hasattr(tree, "children_map"):
            def _serialize_node(node_id):
                node = tree.topic_by_id.get(node_id)
                children = tree.children_map.get(node_id, [])
                return {
                    "id": node.id if node else None,
                    "name": node.name if node else "Root",
                    "children": [_serialize_node(c.id) for c in children]
                }
            return _serialize_node(None)
        return tree if isinstance(tree, dict) else {"tree": str(tree)}

    @mcp.tool()
    def forget_memory(memory_id: int, user_id: str = "default") -> bool:
        """
        Remove a specific memory by its ID for a user.

        Args:
            memory_id: Memory registry ID to remove.
            user_id: Scope for user/session identifier.

        Returns:
            True if deleted successfully, False if not found.
        """
        return mc.forget(memory_id=memory_id, user_id=user_id)

    return mcp


def main():
    parser = argparse.ArgumentParser(description="MindCache MCP Server")
    parser.add_argument("--db-path", default="mindcache.db", help="SQLite path or PostgreSQL connection URL")
    parser.add_argument("--provider", default="gemini", help="LLM provider (gemini, openai, anthropic)")
    parser.add_argument("--model-name", default="gemini-2.5-flash", help="LLM model name")
    args = parser.parse_args()

    server = create_mcp_server(
        db_path=args.db_path,
        provider=args.provider,
        model_name=args.model_name,
    )
    server.run()


if __name__ == "__main__":
    main()
