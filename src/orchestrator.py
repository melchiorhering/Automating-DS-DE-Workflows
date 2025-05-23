# orchestrator.py – parallel benchmark runner + local evaluation

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from smolagents import ActionStep, LiteLLMModel

from agent.build import _observation_callback, _take_initial_screenshot
from agent.sandbox_agent import CodeAgent
from agent.utils.port_pool import PORT_MANAGER
from benchmark.helpers import (
    CONFIG_DISPATCH,
    EVAL_DISPATCH,
    upload_and_execute_script,
)
from sandbox.configs import SandboxVMConfig
from sandbox.virtualmachine import SSHClient

MODEL = LiteLLMModel(
    model_id="openai/o4-mini-2025-04-16",
    api_key=os.getenv("OPENAI_API_KEY"),
)


# METRICS = load_metric_registry()
def build_agent(container_name: str) -> CodeAgent:
    ports = PORT_MANAGER.get_ports(container_name)
    cfg = SandboxVMConfig(
        container_name=container_name,
        host_ssh_port=ports["ssh"],
        host_vnc_port=ports["vnc"],
        host_sandbox_server_port=ports["sandbox_server"],
        host_sandbox_jupyter_kernel_port=ports["jupyter"],
        host_services_dir=Path("sandbox/services/"),
    )
    agent = CodeAgent(
        description=f"Agent {container_name}",
        tools=[],
        model=MODEL,
        additional_authorized_imports=["pyautogui"],
        step_callbacks=[_observation_callback],
        executor_type="sandbox",
        executor_kwargs={"config": cfg},
    )
    first = ActionStep(0, "initial state", "📸 initial")
    _take_initial_screenshot(first, agent)
    agent.memory.steps.append(first)
    return agent


class TaskSpec:
    def __init__(self, tool: str, uid: str, root: Path):
        self.uid, self.tool = uid, tool
        self.folder = root / tool / uid
        meta = json.loads((self.folder / f"{uid}.json").read_text())
        self.prompt = meta["instruction"]
        self.steps = meta.get("action_number", 6)
        self.container = f"sandbox-{uid[:12]}"
        self.config = meta.get("config", [])
        self.evaluation = meta["evaluation"]
        self.success = False


class Orchestrator:
    def __init__(self, max_conc: int, mapping: Dict, examples_root: Path):
        self.tasks: List[TaskSpec] = [TaskSpec(t, u, examples_root) for t, lst in mapping.items() for u in lst]
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
        logger = agent.logger
        ssh = agent.ssh
        cfg = agent.python_executor.vm.cfg

        setup_script = spec.folder / "setup.sh"
        if setup_script.is_file():
            logger.log(f"🛠 Running setup.sh for {spec.uid}")
            upload_and_execute_script(ssh, cfg, setup_script)

        for step in spec.config:
            func = CONFIG_DISPATCH.get(step["func"])
            if not func:
                logger.log(f"⚠️ Unknown step function: {step['func']}")
                continue
            try:
                kwargs = step.get("arguments", {})
                func(ssh, **kwargs)
            except Exception as e:
                logger.log(f"⚠️ Step {step['func']} in {spec.uid} failed: {e}")

        try:
            agent.run(spec.prompt, max_steps=spec.steps)
        except Exception as exc:
            logger.log_error(
                f"❌ {spec.uid} failed during execution: {exc}",
            )

        self._evaluate(spec, ssh)

        agent.cleanup()
        shutil.rmtree(cfg.host_container_dir, ignore_errors=True)

    def _evaluate(self, spec: TaskSpec, ssh: SSHClient):
        out_dir = Path("results") / spec.uid
        out_dir.mkdir(parents=True, exist_ok=True)

        eval_spec = spec.evaluation
        func = EVAL_DISPATCH.get(eval_spec["func"])
        if not func:
            print(f"⚠️ Unknown evaluation function: {eval_spec['func']}")
            return

        args = eval_spec.get("arguments", {})
        try:
            score = func(ssh=ssh, folder=spec.folder, **args)
            (out_dir / "score.json").write_text(json.dumps({"score": score}, indent=2))
            print(f"✅ {spec.uid}  {eval_spec['func']}={score}")
        except Exception as e:
            print(f"❌ Evaluation for {spec.uid} failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_file", type=Path)
    ap.add_argument("examples_root", type=Path)
    ap.add_argument("-j", "--concurrency", type=int, default=2)
    args = ap.parse_args()

    mapping = json.loads(args.task_file.read_text())
    orch = Orchestrator(args.concurrency, mapping, args.examples_root.resolve())
    asyncio.run(orch.run_all())


if __name__ == "__main__":
    main()
