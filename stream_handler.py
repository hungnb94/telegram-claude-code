class StreamHandler:
    def __init__(self, lines_per_chunk: int = 30):
        self.lines_per_chunk = lines_per_chunk
        self.buffer: list[str] = []

    def add_line(self, line: str) -> list[str]:
        self.buffer.append(line)
        if len(self.buffer) >= self.lines_per_chunk:
            result = self.buffer
            self.buffer = []
            return result
        return []

    def flush(self) -> list[str]:
        result = self.buffer
        self.buffer = []
        return result