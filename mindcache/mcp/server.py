import argparse
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer
from mindcache import MindCache


DEFAULT_DB_PATH = "mindcache.db"
DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
SERVER_NAME = "MindCache Memory Server"


def create_mcp_server(
    *,
    db_path: str = DEFAULT_DB_PATH,
    provider: str = DEFAULT_PROVIDER,
    model_name: str = DEFAULT_MODEL_NAME,
    client: Optional[MindCache] = None,
) -> MCPServer:
    """
    Create and configure a MindCache MCP server.

    `client` can be supplied by tests or advanced callers to inject
    an existing MindCache-compatible client.
    """
    mc = client or MindCache(
        db_path=db_path,
        provider=provider,
        model_name=model_name,
    )

    mcp = MCPServer(SERVER_NAME)

    @mcp.tool()
    def add_memory(
        messages: List[Dict[str, str]],
        user_id: str = "default",
    ) -> int:
        """Buffer conversation turns into the MindCache queue."""
        return mc.add(
            messages=messages,
            user_id=user_id,
        )

    @mcp.tool()
    def process_memory(
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Process pending conversations and update memory state."""
        return mc.process(user_id=user_id)

    @mcp.tool()
    def search_memory(
        query: str,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Search and retrieve structured memory context."""
        result = mc.search(
            query=query,
            user_id=user_id,
        )

        return {
            "context": getattr(result, "context", str(result)),
            "system_hint": getattr(result, "system_hint", ""),
            "query_type": getattr(result, "query_type", ""),
            "trace": getattr(result, "trace", {}),
        }

    @mcp.tool()
    def inspect_memories(
        user_id: str = "default",
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Inspect stored memories."""
        return mc.inspect(
            user_id=user_id,
            view="memories",
            memory_type=memory_type,
        )

    @mcp.tool()
    def inspect_tree(
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """Inspect the dynamic topic hierarchy."""
        tree = mc.inspect(
            user_id=user_id,
            view="tree",
        )

        if hasattr(tree, "children_map"):
            def serialize(node_id):
                node = tree.topic_by_id.get(node_id)
                children = tree.children_map.get(node_id, [])

                return {
                    "id": node.id if node else None,
                    "name": node.name if node else None,
                    "children": [
                        serialize(child.id)
                        for child in children
                    ],
                }

            return serialize(None)

        return tree if isinstance(tree, dict) else {"tree": str(tree)}

    @mcp.tool()
    def forget_memory(
        memory_id: int,
        user_id: str = "default",
    ) -> bool:
        """Remove a specific memory."""
        return mc.forget(
            memory_id=memory_id,
            user_id=user_id,
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MindCache MCP Server"
    )

    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
    )

    args = parser.parse_args()

    server = create_mcp_server(
        db_path=args.db_path,
        provider=args.provider,
        model_name=args.model_name,
    )

    server.run()


if __name__ == "__main__":
    main()