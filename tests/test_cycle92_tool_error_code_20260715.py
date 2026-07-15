from manju.mcp.tools import ToolError

def test_tool_error_code_attr():
    e = ToolError("msg", code="canceled")
    assert e.code == "canceled" or getattr(e, "code", None) == "canceled"
