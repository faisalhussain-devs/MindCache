"""
Tests for the pure static helper methods of SafeAI.

These methods handle post-processing of LLM output and schema transformation.
None of them make network calls — they are pure string/dict transformations.
"""

import pytest
from mindcache.Memory_extract.safe_ai import SafeAI



def test_clean_json_strips_backtick_fences():
    raw = "```json\n{\"key\": \"value\"}\n```"
    result = SafeAI.clean_json(raw)
    assert result == '{"key": "value"}'


def test_clean_json_strips_plain_backtick_fence():
    raw = "```\n{\"key\": 1}\n```"
    result = SafeAI.clean_json(raw)
    assert result == '{"key": 1}'


def test_clean_json_passes_through_raw_json():
    raw = '{"key": "already clean"}'
    result = SafeAI.clean_json(raw)
    assert result == raw


def test_sanitize_output_collapses_newline_loop():
    """3+ consecutive newlines should collapse to a single space."""
    text = 'before\n\n\n\n\nafter'
    result = SafeAI._sanitize_output(text)
    assert "\n\n\n" not in result
    assert "before" in result and "after" in result


def test_sanitize_output_collapses_tab_loop():
    """3+ consecutive tabs should collapse to a single space."""
    text = "start\t\t\t\t\tend"
    result = SafeAI._sanitize_output(text)
    assert "\t\t\t" not in result
    assert "start" in result and "end" in result


def test_sanitize_output_leaves_normal_text_unchanged():
    text = "This is a normal sentence with one\nnewline."
    result = SafeAI._sanitize_output(text)
    assert result == text


def test_clean_schema_resolves_ref_pointers():
    """$ref entries should be inlined from $defs."""
    schema = {
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {"name": {"type": "string"}}
            }
        },
        "type": "object",
        "properties": {
            "item": {"$ref": "#/$defs/Item"}
        }
    }
    result = SafeAI._clean_schema(schema)
    # $ref should be resolved
    assert "$ref" not in str(result)
    # The inlined definition should appear
    assert result["properties"]["item"]["type"] == "object"


def test_clean_schema_removes_forbidden_keys():
    """additionalProperties, $defs, and title must be stripped."""
    schema = {
        "$defs": {"Foo": {"type": "string"}},
        "title": "MyModel",
        "additionalProperties": False,
        "type": "object",
        "properties": {}
    }
    result = SafeAI._clean_schema(schema)
    assert "additionalProperties" not in result
    assert "$defs" not in result
    assert "title" not in result
    assert result["type"] == "object"


def test_extract_text_from_valid_gemini_response():
    data = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Hello"}, {"text": " World"}]
                }
            }
        ]
    }
    result = SafeAI._extract_text(data)
    assert result == "Hello World"


def test_extract_text_returns_none_for_empty_candidates():
    result = SafeAI._extract_text({"candidates": []})
    assert result is None


def test_extract_text_returns_none_for_missing_candidates():
    result = SafeAI._extract_text({})
    assert result is None
