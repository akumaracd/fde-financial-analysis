"""
test_exceptions.py — tests for backend/mcp_server/exceptions.py
"""
import pytest
from backend.mcp_server.exceptions import (
    MCPToolError,
    SearchError,
    ScrapeError,
    FileWriteError,
)


class TestExceptionHierarchy:
    def test_search_error_is_mcp_tool_error(self):
        assert issubclass(SearchError, MCPToolError)

    def test_scrape_error_is_mcp_tool_error(self):
        assert issubclass(ScrapeError, MCPToolError)

    def test_file_write_error_is_mcp_tool_error(self):
        assert issubclass(FileWriteError, MCPToolError)

    def test_mcp_tool_error_is_exception(self):
        assert issubclass(MCPToolError, Exception)

    def test_catch_search_error_as_mcp_tool_error(self):
        with pytest.raises(MCPToolError):
            raise SearchError("search failed")

    def test_catch_scrape_error_as_mcp_tool_error(self):
        with pytest.raises(MCPToolError):
            raise ScrapeError("scrape failed")

    def test_catch_file_write_error_as_mcp_tool_error(self):
        with pytest.raises(MCPToolError):
            raise FileWriteError("write failed")

    def test_error_message_preserved(self):
        msg = "specific error detail"
        with pytest.raises(SearchError, match=msg):
            raise SearchError(msg)

    def test_errors_are_distinct_types(self):
        assert SearchError is not ScrapeError
        assert ScrapeError is not FileWriteError
        assert SearchError is not FileWriteError
