from smolagents.agents import CodeAgent as BaseCodeAgent
from smolagents.monitoring import LogLevel

from sandbox import SandboxClient, SSHClient

from .executor import SandboxExecutor


class SandboxCodeAgent(BaseCodeAgent):
    """Extends the original CodeAgent with sandbox VM support."""

    def __init__(self, *args, executor_type="local", executor_kwargs=None, **kwargs):
        self.executor_type = executor_type
        self.executor_kwargs = executor_kwargs or {}
        super().__init__(*args, executor_type=executor_type, executor_kwargs=executor_kwargs, **kwargs)

        # Inject SSH and sandbox client if sandbox executor
        if self.executor_type == "sandbox":
            self.ssh: SSHClient = self.python_executor.vm.ssh
            self.sandbox_client: SandboxClient = self.python_executor.vm.sandbox_client

    def create_python_executor(self):
        if self.executor_type == "sandbox":
            executor = SandboxExecutor(
                additional_imports=self.additional_authorized_imports,
                logger=self.logger,
                **self.executor_kwargs,
            )
            return executor
        # fallback to original method
        return super().create_python_executor()

    def cleanup(self):
        """Clean up sandbox or other remote resources if needed."""
        try:
            if hasattr(self, "python_executor") and hasattr(self.python_executor, "cleanup"):
                self.logger.log("🧹 Calling cleanup on python executor...", level=LogLevel.INFO)
                self.python_executor.cleanup()
        except Exception as e:
            self.logger.log_error(f"⚠️ CodeAgent cleanup failed: {e}")
