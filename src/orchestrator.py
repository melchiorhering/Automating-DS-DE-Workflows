from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from PIL import Image
from smolagents import CodeAgent, tool

from sandbox import AgentVMConfig, AgentVMManager, VMServerClient

cfg = AgentVMConfig(
    container_name="test-agent-vm",
    host_server_dir=Path("sandbox/server/"),  # This should point to the correct directory
    host_sandbox_server_port=8765,  # When running with multiple VMs, this should be different for each VM
    host_vnc_port=8006,  # When running with multiple VMs, this should be different for each VM
    host_ssh_port=2222,  # When running with multiple VMs, this should be different for each VM
)
#

# Global singleton client instance
client = VMServerClient(f"http://{cfg.host_sandbox_server_host}:{cfg.host_sandbox_server_port}")

# Initialize the model
# model_id = "meta-llama/Llama-3.3-70B-Instruct"  # You can change this to your preferred model
# model = InferenceClientModel(model_id=model_id)


@tool
def take_screenshot_tool():
    """Take a screenshot using the FastAPI sandbox server."""
    result = client.take_screenshot()
    if result.get("status") == "error":
        return f"❌ Failed: {result['message']}"
    return f"📸 Saved screenshot at: {result['screenshot_path']}"


@tool
def list_packages_tool():
    """List installed packages inside the VM."""
    pkgs = client.list_packages()
    return "\n".join(f"{p['name']}=={p['version']}" for p in pkgs if isinstance(p, dict))


# Tools
@tool
def some_tool():
    """A tool could be anything from a simple function to a complex API call to for example our FastAPI server running in the sandbox."""
    return "🔧 Placeholder tool executed."


def save_screenshot_callback(memory_step, agent):
    """Example callback using the global FastAPI sandbox client."""
    result = client.take_screenshot()
    if "screenshot_path" in result:
        path = os.path.join("/tmp/sandbox-server", result["screenshot_path"])
        try:
            image = Image.open(path)
            memory_step.observations_images = [image.copy()]
            memory_step.observations = f"🖼️ Screenshot taken at {result['mouse_position']}"
        except Exception as e:
            memory_step.observations = f"⚠️ Failed to load screenshot: {e}"


# Create the agent
def create_agent() -> CodeAgent:
    return CodeAgent(
        tools=[some_tool, take_screenshot_tool, list_packages_tool],
        model=model,
        step_callbacks=[save_screenshot_callback],
        max_steps=20,
        verbosity_level=2,
    )


class AgentOrchestrator:
    def __init__(
        self,
        benchmark_dir: Path,
        agent_vm_config: AgentVMConfig,
        concurrent_vms: int,
        agent_factory: callable,  # function to create a new agent instance
        logger: Optional[logging.Logger] = None,
    ):
        self.benchmark_dir = benchmark_dir.resolve()
        self.agent_vm_config = agent_vm_config
        self.concurrent_vms = concurrent_vms
        self.agent_factory = agent_factory
        self.logger = logger or logging.getLogger("AgentOrchestrator")
        self.logger.setLevel(logging.INFO)

    def _gather_tasks(self) -> List[dict]:
        all_tasks = []
        for json_file in self.benchmark_dir.glob("test_*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                task_map = json.load(f)
                for category, ids in task_map.items():
                    for task_id in ids:
                        task_dir = self.benchmark_dir / "examples" / category / task_id
                        with open(task_dir / f"{task_id}.json", "r", encoding="utf-8") as tf:
                            config = json.load(tf)
                            config["_path"] = task_dir
                            all_tasks.append(config)
        return all_tasks

    async def _run_task(self, task_config: dict):
        task_id = task_config["id"]
        cfg = AgentVMConfig(
            **{**self.agent_vm_config.__dict__},
            container_name=f"agent-vm-{task_id[:8]}",
            host_server_dir=task_config["_path"],
        )

        try:
            async with AgentVMManager(cfg, logger=self.logger) as vm:
                agent = self.agent_factory()
                agent.vm = vm
                agent.task_config = task_config
                await agent.run_task()
                self.logger.info("✅ Task completed: %s", task_id)
        except Exception as e:
            self.logger.error("❌ Task failed: %s", e, exc_info=True)

    async def run_all(self):
        all_tasks = self._gather_tasks()
        sem = asyncio.Semaphore(self.concurrent_vms)

        async def wrapped(task_config):
            async with sem:
                await self._run_task(task_config)

        await asyncio.gather(*(wrapped(t) for t in all_tasks))

    async def run_one(self, task_id: str):
        all_tasks = self._gather_tasks()
        task = next((t for t in all_tasks if t["id"] == task_id), None)
        if task:
            await self._run_task(task)

    async def run_non_account(self):
        all_tasks = self._gather_tasks()
        non_account_tasks = [t for t in all_tasks if "account" not in t.get("tags", [])]
        sem = asyncio.Semaphore(self.concurrent_vms)

        async def wrapped(task_config):
            async with sem:
                await self._run_task(task_config)

        await asyncio.gather(*(wrapped(t) for t in non_account_tasks))
