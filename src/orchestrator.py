# orchestrator.py – parallel benchmark runner + local evaluation

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

import yaml
from smolagents import LiteLLMModel, LogLevel

from agent.build import observation_screenshot_callback, take_initial_screenshot
from agent.sandbox_agent import SandboxCodeAgent
from agent.utils.port_pool import PORT_MANAGER
from benchmark.helpers import (
    CONFIG_DISPATCH,
    EVAL_DISPATCH,
    upload_and_execute_script,
)
from sandbox.configs import SandboxVMConfig

# GLOBALS
MODEL = LiteLLMModel(model_id="openai/o4-mini", api_key=os.getenv("OPENAI_API_KEY"))
PROMPT_TEMPLATES = yaml.safe_load(importlib.resources.files("agent.prompts").joinpath("code_agent.yaml").read_text())


# AGENT GENERATOR
def build_agent(container_name: str) -> SandboxCodeAgent:
    ports = PORT_MANAGER.get_ports(container_name)
    config = SandboxVMConfig(
        container_name=container_name,
        host_ssh_port=ports["ssh"],
        host_vnc_port=ports["vnc"],
        host_sandbox_server_port=ports["sandbox_server"],
        host_sandbox_jupyter_kernel_port=ports["jupyter"],
        host_services_dir=Path("sandbox/services/"),
    )
    agent = SandboxCodeAgent(
        description="This agent runs in a sandboxed environment and can execute code.",
        tools=[],
        model=MODEL,
        # add_base_tools=True,
        additional_authorized_imports=["pyautogui"],
        step_callbacks=[observation_screenshot_callback],
        executor_type="sandbox",
        executor_kwargs={
            "config": config,
        },
        prompt_templates=PROMPT_TEMPLATES,
        verbosity_level=LogLevel.INFO,
    )
    # Take the initial screenshot and add to the agents memory
    take_initial_screenshot(
        agent,
    )

    return agent


class TaskSpec:
    def __init__(self, tool: str, uid: str, root: Path, results_root: Path):
        self.uid, self.tool = uid, tool
        self.tool_dir = root / tool
        self.folder = root / tool / uid
        self.result = results_root / uid  # <- result dir is now dynamic and clear

        # Load Meta Data
        meta = json.loads((self.folder / f"{uid}.json").read_text())
        self.prompt = meta["instruction"]
        self.steps = meta.get("action_number", 6)
        self.container = f"sandbox-{uid[:12]}"
        self.config = meta.get("config", [])
        self.evaluation = meta["evaluation"]
        self.success = False


class Orchestrator:
    def __init__(self, max_conc: int, mapping: Dict, examples_root: Path, results_root: Path = Path("results")):
        self.tasks: List[TaskSpec] = [
            TaskSpec(t, u, examples_root, results_root) for t, lst in mapping.items() for u in lst
        ]
        self.sem = asyncio.Semaphore(max_conc)
        self.pool = ThreadPoolExecutor(max_workers=max_conc)

    async def run_all(self):
        loop = asyncio.get_running_loop()

        async def _runner(task: TaskSpec):
            async with self.sem:
                await loop.run_in_executor(self.pool, self._run_one, task)

        await asyncio.gather(*(_runner(t) for t in self.tasks))
        self.pool.shutdown(wait=True)

    def _run_one(self, spec: TaskSpec):
        agent = build_agent(spec.container)

        try:
            setup_script = spec.tool_dir / "setup.sh"
            if setup_script.is_file():
                agent.logger.log(f"🛠 Running setup.sh for {spec.uid}", level=LogLevel.INFO)
                upload_and_execute_script(agent, setup_script)
            else:
                agent.logger.log(f"⚠️ No setup.sh found at {setup_script}", level=LogLevel.ERROR)

            # 📤 CONFIG STEPS
            for step in spec.config:
                func = CONFIG_DISPATCH.get(step["func"])
                if not func:
                    agent.logger.log(f"⚠️ Unknown step function: {step['func']}", level=LogLevel.ERROR)
                    continue

                try:
                    kwargs = step.get("arguments", {})
                    func(task=spec, agent=agent, **kwargs)
                except Exception as e:
                    agent.logger.log(f"⚠️ Step {step['func']} in {spec.uid} failed: {e}", level=LogLevel.ERROR)

            # 🧠 Agent run
            try:
                result = agent.run(spec.prompt, max_steps=spec.steps, stream=False)
                agent.logger.log(result)
            except Exception as exc:
                agent.logger.log(f"❌ {spec.uid} failed during execution: {exc}", level=LogLevel.ERROR)

            self._evaluate(spec, agent)

            time.sleep(280)

        except Exception as fatal:
            agent.logger.log(f"🔥 Unhandled error during task {spec.uid}: {fatal}", level=LogLevel.ERROR)
        finally:
            agent.logger.log.log("🧹 Cleaning up sandbox environment...", level=LogLevel.DEBUG)
            try:
                agent.cleanup()
            except Exception as cleanup_err:
                agent.logger.log(f"⚠️ Error during cleanup: {cleanup_err}", level=LogLevel.ERROR)

    def _evaluate(self, spec: TaskSpec, agent: SandboxCodeAgent):
        spec.result.mkdir(parents=True, exist_ok=True)
        eval_spec = spec.evaluation
        func = EVAL_DISPATCH.get(eval_spec["func"])
        if not func:
            print(f"⚠️ Unknown evaluation function: {eval_spec['func']}")
            return

        try:
            func(task=spec, agent=agent, **eval_spec.get("arguments", {}))
        except Exception as e:
            print(f"❌ Evaluation for {spec.uid} failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_file", type=Path)
    ap.add_argument("examples_root", type=Path)
    ap.add_argument("-j", "--concurrency", type=int, default=2)
    args = ap.parse_args()

    # Load Task File
    mapping = json.loads(args.task_file.read_text())
    orch = Orchestrator(args.concurrency, mapping, args.examples_root.resolve())
    asyncio.run(orch.run_all())


if __name__ == "__main__":
    main()
