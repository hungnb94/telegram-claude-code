import pytest
from poc_dev_flow_agent.stream_handler import StreamHandler


@pytest.mark.unit
class TestStreamHandler:
    def test_add_line_accumulates_buffer(self):
        h = StreamHandler(lines_per_chunk=3)
        lines = h.add_line("line1")
        assert lines == []
        lines = h.add_line("line2")
        assert lines == []
        lines = h.add_line("line3")
        assert len(lines) == 3

    def test_flush_returns_remaining(self):
        h = StreamHandler(lines_per_chunk=3)
        h.add_line("line1")
        h.add_line("line2")
        remaining = h.flush()
        assert len(remaining) == 2
        assert h.buffer == []

    def test_flush_empty_buffer(self):
        h = StreamHandler(lines_per_chunk=3)
        remaining = h.flush()
        assert remaining == []

    def test_add_line_exact_chunk_size(self):
        h = StreamHandler(lines_per_chunk=2)
        lines = h.add_line("line1")
        assert lines == []
        lines = h.add_line("line2")
        assert len(lines) == 2
