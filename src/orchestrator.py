# orchestrator.py – parallel benchmark runner + local evaluation

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from smolagents import ActionStep, LiteLLMModel, LogLevel

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

    return agent


class TaskSpec:
    def __init__(self, tool: str, uid: str, root: Path):
        self.uid, self.tool = uid, tool
        self.tool_dir = root / tool
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

        try:
            setup_script = spec.tool_dir / "setup.sh"
            if setup_script.is_file():
                logger.log(f"🛠 Running setup.sh for {spec.uid}", level=LogLevel.INFO)
                upload_and_execute_script(ssh, cfg, setup_script, logger=logger)
            else:
                logger.log(f"⚠️ No setup.sh found at {setup_script}", level=LogLevel.WARNING)

            # 📸 Initial screenshot
            first = ActionStep(0, "initial state", "📸 initial")
            _take_initial_screenshot(first, agent)
            agent.memory.steps.append(first)

            # 📤 Run configuration steps (e.g., upload files)
            for step in spec.config:
                func = CONFIG_DISPATCH.get(step["func"])
                if not func:
                    logger.log(f"⚠️ Unknown step function: {step['func']}", level=LogLevel.WARNING)
                    continue

                try:
                    kwargs = step.get("arguments", {})
                    if "local_path" in kwargs:
                        # Resolve path relative to the task folder
                        relative = Path(kwargs["local_path"])
                        kwargs["local_path"] = (spec.folder / relative).resolve()
                    func(ssh, logger=logger, **kwargs)
                except Exception as e:
                    logger.log(f"⚠️ Step {step['func']} in {spec.uid} failed: {e}", level=LogLevel.ERROR)

            # 🧠 Agent execution
            try:
                agent.run(spec.prompt, max_steps=spec.steps)
            except Exception as exc:
                logger.log_error(f"❌ {spec.uid} failed during execution: {exc}")

            # 🧪 Evaluation
            self._evaluate(spec, ssh)

            # Wait briefly before cleanup (useful for debugging or GUI interaction)
            time.sleep(280)

        except Exception as fatal:
            logger.log_error(f"🔥 Unhandled error during task {spec.uid}: {fatal}")
        finally:
            logger.log("🧹 Cleaning up sandbox environment...", level=LogLevel.DEBUG)
            try:
                agent.cleanup()
                # Optionally remove VM container dir
                # shutil.rmtree(cfg.host_container_shared_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logger.log_error(f"⚠️ Error during cleanup: {cleanup_err}")

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
            args["folder"] = spec.folder  # required by compare_csv

            # Resolve gold.csv relative to task folder
            if "local_expected" in args:
                args["local_expected"] = str((spec.folder / args["local_expected"]).resolve())

            score = func(ssh=ssh, **args)
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
