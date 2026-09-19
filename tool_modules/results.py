"""String-compatible results during the transition from v0.10 handlers."""


class ToolResult(str):
    def __new__(cls, content, success=True, error_type=None):
        result = super().__new__(cls, content)
        result.content = str(content)
        result.success = success
        result.error_type = error_type
        return result

    @classmethod
    def ok(cls, content):
        return cls(content)

    @classmethod
    def fail(cls, content, error_type):
        return cls(content, False, error_type)

    @classmethod
    def from_legacy(cls, content):
        """Only legacy handlers need prefix inference; native handlers are explicit."""
        prefixes = ("Unknown tool:", "Invalid arguments:", "Tool failure:",
                    "Access denied:", "File error:", "Invalid path:", "Web search unavailable:")
        for prefix in prefixes:
            if isinstance(content, str) and content.startswith(prefix):
                return cls.fail(content, prefix[:-1])
        return cls.ok(content)
