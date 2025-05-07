from typing import Any, Callable, Dict, List, Optional, Union

from smolagents import (
    AgentLogger,
    ChatMessage,
    CodeAgent,
    DockerExecutor,
    E2BExecutor,
    LocalPythonExecutor,
    LogLevel,
    PromptTemplates,
    PythonExecutor,
    Tool,
)

from agent import SandboxExecutor


class SandboxCodeAgent(CodeAgent):
    """
    CodeAgent extension that runs code inside a sandboxed QEMU-based VM.
    Adds:
    - Custom SandboxExecutor support
    - SSH + FastAPI access
    - Reconnection to running VMs
    - Auto cleanup on final step or interrupt
    """

    def __init__(
        self,
        tools: List[Tool],
        model: Callable[[List[Dict[str, str]]], ChatMessage],
        prompt_templates: Optional[PromptTemplates] = None,
        grammar: Optional[Dict[str, str]] = None,
        additional_authorized_imports: Optional[List[str]] = None,
        planning_interval: Optional[int] = None,
        executor_type: str | None = "sandbox",
        executor_kwargs: Optional[Dict[str, Any]] = None,
        auto_cleanup: bool = True,
        max_print_outputs_length: Optional[int] = None,
        logger: Optional[AgentLogger] = None,
        **kwargs,
    ):
        # Core attributes
        self.logger = logger or AgentLogger(level=LogLevel.INFO)
        self.auto_cleanup = auto_cleanup
        self._executor_cleaned_up = False
        self.additional_authorized_imports = additional_authorized_imports or []
        self.executor_type = executor_type
        self.executor_kwargs = executor_kwargs or {}

        # Prepare final step cleanup if enabled
        if auto_cleanup:
            kwargs.setdefault("step_callbacks", []).append(self._final_step_cleanup)

        # Create the sandbox executor directly (don't let base create it)
        self._sandbox_executor: SandboxExecutor = self.create_python_executor()

        # Expose internal components
        self.ssh = self._sandbox_executor.vm.ssh
        self.sandbox_client = self._sandbox_executor.vm.sandbox_client

        # Inject executor directly into base class
        super().__init__(
            tools=tools,
            model=model,
            prompt_templates=prompt_templates,
            grammar=grammar,
            additional_authorized_imports=self.additional_authorized_imports,
            planning_interval=planning_interval,
            executor_type=None,  # disables duplicate executor creation
            executor_kwargs={"executor": self._sandbox_executor},
            max_print_outputs_length=max_print_outputs_length,
            **kwargs,
        )

    def create_python_executor(
        self,
    ) -> Union[PythonExecutor, E2BExecutor, DockerExecutor, LocalPythonExecutor, SandboxExecutor]:
        if self.executor_type != "sandbox":
            return super().create_python_executor()

        config = self.executor_kwargs.get("config")
        if not config:
            raise ValueError("Missing required `config` in executor_kwargs for sandbox executor.")

        return SandboxExecutor(
            additional_imports=self.additional_authorized_imports,
            logger=self.logger,
            config=config,
            preserve_on_exit=self.executor_kwargs.get("preserve_on_exit", False),
            reconnect=self.executor_kwargs.get("reconnect", False),
            **self.executor_kwargs.get("extra_args", {}),
        )

    def run(self, *args, **kwargs):
        try:
            return super().run(*args, **kwargs)
        except KeyboardInterrupt:
            self.logger.log_error("⚠️ KeyboardInterrupt detected — cleaning up executor.")
            raise
        finally:
            if self.auto_cleanup and not self._executor_cleaned_up:
                self.cleanup()

    def _final_step_cleanup(self, step, agent=None):
        if getattr(step, "is_final", False):  # assumes your steps could implement this trait
            self.cleanup()

    def cleanup(self):
        self.logger.log_rule("🧹 Cleanup Process")
        if not self._executor_cleaned_up and self._sandbox_executor:
            try:
                self.logger.log("🧹 Cleaning up sandbox executor...", level=LogLevel.INFO)
                self._sandbox_executor.cleanup()
            except Exception as e:
                self.logger.log_error(f"⚠️ Cleanup failed: {e}")
            self._executor_cleaned_up = True
