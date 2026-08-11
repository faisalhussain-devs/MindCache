"""
MindCache Terminal Display Utilities
------------------------------------

Human-readable terminal presentation for MindCache inspection
and retrieval results.

These utilities intentionally sit outside the core API:
MindCache methods return structured data, while these functions
turn that data into readable terminal output.
"""

from typing import Any, Dict, List, Optional


_WIDTH = 68


def _print_header(title: str) -> None:
    """Print a consistent section header."""
    print()
    print("╭" + "─" * (_WIDTH - 2) + "╮")
    print(f"│{title:^{_WIDTH - 2}}│")
    print("╰" + "─" * (_WIDTH - 2) + "╯")


def _print_separator() -> None:
    """Print a visual separator."""
    print("  " + "─" * (_WIDTH - 4))


def _print_wrapped(text: str, indent: str = "  ", width: int = 64) -> None:
    """Print long text with terminal-friendly wrapping."""
    import textwrap

    text = str(text).strip()

    if not text:
        return

    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    for line in lines:
        print(f"{indent}{line}")


def pretty_print_memories(
    memories: List[Dict[str, Any]],
) -> None:
    """
    Display memories returned by:

        mc.inspect(view="memories")

    The function only formats data for terminal output.
    It does not modify the returned memory objects.
    """
    _print_header("MindCache Memories")

    if not memories:
        print("\n  No stored memories found.\n")
        return

    print(f"\n  {len(memories)} memor{'y' if len(memories) == 1 else 'ies'}\n")

    for index, memory in enumerate(memories):
        memory_type = str(
            memory.get("type", "memory")
        ).upper()

        topic = str(
            memory.get("topic") or "General"
        )

        content = str(
            memory.get("content") or ""
        ).strip()

        print(f"  [{memory_type}]")
        print(f"  Topic: {topic}")

        if content:
            _print_wrapped(content, indent="  ")

        # Preserve optional metadata when it exists.
        status = memory.get("status")
        if status:
            print(f"  Status: {str(status).upper()}")

        context = memory.get("context")
        if context:
            _print_wrapped(
                f"Context: {context}",
                indent="  ",
            )

        if index < len(memories) - 1:
            print()
            _print_separator()
            print()

    print()


def pretty_print_tree(tree: Any) -> None:
    """
    Display the topic tree returned by:

        mc.inspect(view="tree")

    The expected tree representation is an object exposing
    ``children_map`` and topic nodes with ``id`` and ``name``.
    """
    _print_header("MindCache Topic Tree")

    if not tree:
        print("\n  Tree is empty.\n")
        return

    # Expected runtime tree representation.
    if hasattr(tree, "children_map"):
        children_map = tree.children_map
        roots = children_map.get(None, [])

        if not roots:
            print("\n  └── Root\n")
            return

        print("\n  Root")

        def print_node(
            node: Any,
            prefix: str,
            is_last: bool,
        ) -> None:
            connector = "└── " if is_last else "├── "
            name = getattr(node, "name", str(node))

            print(f"{prefix}{connector}{name}")

            node_id = getattr(node, "id", None)
            children = children_map.get(node_id, [])

            child_prefix = prefix + (
                "    " if is_last else "│   "
            )

            for index, child in enumerate(children):
                print_node(
                    child,
                    child_prefix,
                    index == len(children) - 1,
                )

        for index, root in enumerate(roots):
            print_node(
                root,
                "  ",
                index == len(roots) - 1,
            )

        print()
        return

    # Support a simple nested dictionary representation.
    if isinstance(tree, dict):

        def print_dict(
            value: Dict[str, Any],
            prefix: str = "",
        ) -> None:
            items = list(value.items())

            for index, (key, child) in enumerate(items):
                is_last = index == len(items) - 1
                connector = "└── " if is_last else "├── "

                print(f"{prefix}{connector}{key}")

                if isinstance(child, dict):
                    child_prefix = prefix + (
                        "    " if is_last else "│   "
                    )
                    print_dict(child, child_prefix)

        print()
        print_dict(tree, "  ")
        print()
        return

    # Last-resort fallback.
    print("\n  Unable to format tree structure:")
    _print_wrapped(str(tree), indent="  ")
    print()


def pretty_print_context(
    context: str,
    query: Optional[str] = None,
) -> None:
    """
    Display retrieved context returned by:

        mc.search(...)

    The retrieval context is treated as opaque text. This utility
    deliberately does not modify or reinterpret the retrieval data.
    """
    _print_header("MindCache Search")

    if query:
        print(f'\n  Query: "{query}"')

    print()
    _print_separator()

    if not context or not str(context).strip():
        print("\n  No relevant memories retrieved.\n")
    else:
        print()
        _print_wrapped(
            str(context).strip(),
            indent="  ",
            width=_WIDTH - 4,
        )
        print()

    _print_separator()
    print()
