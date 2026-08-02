"""
Tests for InputDenoiser — the pre-processing engine that compresses code blocks
and error logs before they are sent to the LLM for memory extraction.

All tests are pure string-in / string-out with no external dependencies.
"""

import pytest
from mindcache.Memory_extract.input_denoiser import InputDenoiser


@pytest.fixture(scope="module")
def denoiser():
    return InputDenoiser()


def test_plain_conversation_returned_as_is(denoiser):
    """A clean chat transcript with no code or logs returns verbatim."""
    text = (
        "User: Can you explain recursion to me?\n"
        "Assistant: Sure! Recursion is when a function calls itself."
    )
    result = denoiser.compress(text)
    # Plain text block — must not get any [CODE SEGMENT] / [LOG SEGMENT] headers
    assert "[CODE SEGMENT]" not in result
    assert "[LOG SEGMENT]" not in result
    assert "recursion" in result


def test_empty_string_does_not_crash(denoiser):
    result = denoiser.compress("")
    assert result == ""


def test_python_function_is_skeletonized(denoiser):
    """Function body logic is stripped; def/return/imports are preserved."""
    code = (
        "def calculate_fibonacci(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n - 1):\n"
        "        a, b = b, a + b\n"
        "    return b\n"
    )
    result = denoiser.compress(code)
    # The def line must survive
    assert "def calculate_fibonacci" in result
    # A return statement inside the body must survive
    assert "return" in result
    # Pure loop body variables should be stripped
    assert "a, b = b, a + b" not in result


def test_imports_and_decorators_always_kept(denoiser):
    """import statements and @decorators are never dropped regardless of nesting."""
    code = (
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "@staticmethod\n"
        "def helper():\n"
        "    x = 1 + 2\n"
        "    return x\n"
    )
    result = denoiser.compress(code)
    assert "import os" in result
    assert "from pathlib import Path" in result
    assert "@staticmethod" in result


def test_c_block_comment_preserved(denoiser):
    """C-style /* ... */ block comments are kept, not silently dropped."""
    code = (
        "/* This is a C-style block comment\n"
        "   describing the module */\n"
        "void process() {\n"
        "    int x = compute();\n"
        "    return;\n"
        "}\n"
    )
    result = denoiser.compress(code)
    assert "This is a C-style block comment" in result


def test_mixed_text_and_code_gets_segment_headers(denoiser):
    """When code appears alongside plain text, the code section gets a header."""
    mixed = (
        "Here is my function:\n"
        "\n"
        "def greet(name):\n"
        "    print(f'Hello {name}')\n"
        "    return None\n"
    )
    result = denoiser.compress(mixed)
    assert "[CODE SEGMENT]" in result
    assert "def greet" in result


def test_repeated_log_lines_deduplicated(denoiser):
    """Identical stack trace lines are collapsed into one + repetition note."""
    repeated_log = "\n".join([
        "2024-01-01 10:00:00 ERROR Connection refused to db-host:5432",
        "2024-01-01 10:00:01 ERROR Connection refused to db-host:5432",
        "2024-01-01 10:00:02 ERROR Connection refused to db-host:5432",
        "2024-01-01 10:00:03 ERROR Connection refused to db-host:5432",
    ])
    result = denoiser.compress(repeated_log)
    # Should mention repetition
    assert "repeated" in result.lower()
    # But the original error text should still appear once
    assert "Connection refused" in result


def test_uuid_and_ip_fuzzy_deduplicated_in_logs(denoiser):
    """Logs with varying UUIDs and IPs share structural fuzzy hashes and are deduplicated."""
    log = (
        "2024-01-01 10:00:00 INFO Connection from 192.168.1.100\n"
        "2024-01-01 10:00:01 INFO Connection from 192.168.1.101\n"
        "2024-01-01 10:00:02 INFO Connection from 192.168.1.102\n"
    )
    result = denoiser.compress(log)
    assert "repeated" in result.lower()
