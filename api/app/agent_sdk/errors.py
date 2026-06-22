class AgentSdkError(RuntimeError):
    pass


class AgentNotConfiguredError(AgentSdkError):
    pass


class AgentProviderError(AgentSdkError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AgentGuardrailError(AgentSdkError):
    pass


class SkillExecutionError(AgentSdkError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: str = "skill_execution_failed",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class SkillOutputError(AgentSdkError):
    pass


class AgentRunCancelled(AgentSdkError):
    pass
