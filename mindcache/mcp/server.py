import argparse
from typing import List, Dict, Any, Optional

from mcp.server import MCPServer
from mindcache import MindCache


mcp = MCPServer("MindCache Memory Server")
_mc = None


def get_client() -> MindCache:
    global _mc

    if _mc is None:
        _mc = MindCache(
            db_path="mindcache.db",
            provider="gemini",
            model_name="gemini-2.5-flash",
        )

    return _mc


@mcp.tool()
def add_memory(
    messages: List[Dict[str, str]],
    user_id: str = "default",
) -> int:
    """Buffer conversation turns into the MindCache queue."""
    return get_client().add(
        messages=messages,
        user_id=user_id,
    )


@mcp.tool()
def process_memory(
    user_id: str = "default",
) -> Dict[str, Any]:
    """Process pending conversations and update memory state."""
    return get_client().process(user_id=user_id)


@mcp.tool()
def search_memory(
    query: str,
    user_id: str = "default",
) -> Dict[str, Any]:
    """Search and retrieve structured memory context."""
    result = get_client().search(
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
    return get_client().inspect(
        user_id=user_id,
        view="memories",
        memory_type=memory_type,
    )


@mcp.tool()
def inspect_tree(
    user_id: str = "default",
) -> Dict[str, Any]:
    """Inspect the dynamic topic hierarchy."""
    tree = get_client().inspect(
        user_id=user_id,
        view="tree",
    )

    if hasattr(tree, "children_map"):
        def serialize(node_id):
            node = tree.topic_by_id.get(node_id)
            children = tree.children_map.get(node_id, [])

            return {
                "id": node.id if node else None,
                "name": node.name if node else "None",
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
    return get_client().forget(
        memory_id=memory_id,
        user_id=user_id,
    )


def main():
    global _mc

    parser = argparse.ArgumentParser(
        description="MindCache MCP Server"
    )

    parser.add_argument(
        "--db-path",
        default="mindcache.db",
    )
    parser.add_argument(
        "--provider",
        default="gemini",
    )
    parser.add_argument(
        "--model-name",
        default="gemini-2.5-flash",
    )

    args = parser.parse_args()

    _mc = MindCache(
        db_path=args.db_path,
        provider=args.provider,
        model_name=args.model_name,
    )

    mcp.run()


if __name__ == "__main__":
    main()