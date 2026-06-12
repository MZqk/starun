from collections.abc import Iterable

from app.agent.contracts import Tool


class UnknownToolError(LookupError):
    pass


class DuplicateToolError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}
        for tool in tools:
            key = (tool.name, tool.version)
            if key in self._tools:
                raise DuplicateToolError(f"duplicate tool registration: {tool.name}@{tool.version}")
            self._tools[key] = tool

    def resolve(self, name: str, version: str) -> Tool:
        try:
            return self._tools[(name, version)]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}@{version}") from exc

    @property
    def registered(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._tools)
