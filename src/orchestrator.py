"""
orchestrator.py – parallel benchmark runner + local evaluation
==============================================================

Example
-------
    python src/orchestrator.py -j 3 tasks.json \
        src/benchmark/evaluation_examples/examples
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import shutil
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Dict, List

from PIL import Image
from smolagents import ActionStep, LiteLLMModel, LogLevel

from agent import CodeAgent
from sandbox.configs import SandboxVMConfig
from sandbox.virtualmachine import SSHClient  # SSHClient with put/get helpers

# ────────────────────────────────────────────────────────────────────
# 1.  Global model (same as test harness)
# ────────────────────────────────────────────────────────────────────
MODEL = LiteLLMModel(
    model_id="openai/o4-mini-2025-04-16",
    api_key=os.getenv("OPENAI_API_KEY"),
)


# ────────────────────────────────────────────────────────────────────
# 2.  Auto-discover metric functions
# ────────────────────────────────────────────────────────────────────
def _load_metric_registry() -> Dict[str, callable]:
    root = Path(__file__).parent / "benchmark" / "evaluators" / "metrics"
    registry: Dict[str, callable] = {}

    for file in root.rglob("*.py"):
        if file.name == "__init__.py":
            continue
        mod_name = ".".join(
            file.with_suffix("").relative_to(Path(__file__).parent).parts
        )  # e.g. benchmark.evaluators.metrics.table
        module: ModuleType = importlib.import_module(mod_name)
        for name, obj in vars(module).items():
            if callable(obj) and not name.startswith("_"):
                registry[name] = obj

    # always have a fallback
    registry.setdefault("infeasible", lambda *_a, **_kw: float("nan"))
    return registry


METRICS = _load_metric_registry()


# ────────────────────────────────────────────────────────────────────
# 3.  Port allocator (thread-safe)
# ────────────────────────────────────────────────────────────────────
class PortPool:
    def __init__(self, start: int = 20000):
        self._next = start
        self._lock = threading.Lock()

    def get(self) -> int:
        with self._lock:
            p = self._next
            while not self._free(p):
                p += 1
            self._next = p + 1
            return p

    @staticmethod
    def _free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.connect_ex(("127.0.0.1", port)) != 0


PORT_POOL = PortPool()


# ────────────────────────────────────────────────────────────────────
# 4.  Screenshot helpers (unchanged)
# ────────────────────────────────────────────────────────────────────
def _take_initial_screenshot(step: ActionStep, agent: CodeAgent):
    res = agent.sandbox_client.take_screenshot()
    shared = agent.python_executor.vm.cfg.host_container_shared_dir
    try:
        img = Image.open(shared / res["screenshot_path"])
        step.observations_images = [img.copy()]
    except Exception as exc:
        step.observations = f"⚠️ failed to load screenshot: {exc}"


def _observation_callback(step: ActionStep, agent: CodeAgent):
    for prev in agent.memory.steps:
        if isinstance(prev, ActionStep) and prev.step_number <= step.step_number - 2:
            prev.observations_images = None
    res = agent.sandbox_client.take_screenshot()
    try:
        img = Image.open(res["screenshot_path"])
        step.observations_images = [img.copy()]
        step.observations = (
            f"🖼️ Screenshot @ step {step.step_number}\n"
            f"Mouse {res['mouse_position']} | Screen {img.size[0]}×{img.size[1]}"
        )
    except Exception as exc:
        step.observations = f"⚠️ failed to load screenshot: {exc}"


# ────────────────────────────────────────────────────────────────────
# 5.  Agent factory
# ────────────────────────────────────────────────────────────────────
def build_agent(container: str) -> CodeAgent:
    cfg = SandboxVMConfig(
        container_name=container,
        host_vnc_port=PORT_POOL.get(),
        host_ssh_port=PORT_POOL.get(),
        host_sandbox_server_port=PORT_POOL.get(),
        host_sandbox_jupyter_kernel_port=PORT_POOL.get(),
        host_server_dir=Path("sandbox/services/"),
    )
    agent = CodeAgent(
        description=f"Agent {container}",
        tools=[],
        model=MODEL,
        additional_authorized_imports=["pyautogui"],
        step_callbacks=[_observation_callback],
        executor_type="sandbox",
        executor_kwargs={"config": cfg},
        verbosity_level=LogLevel.WARN,
    )
    first = ActionStep(0, "initial state", "📸 initial")
    _take_initial_screenshot(first, agent)
    agent.memory.steps.append(first)
    return agent


# ────────────────────────────────────────────────────────────────────
# 6.  Task wrapper
# ────────────────────────────────────────────────────────────────────
class TaskSpec:
    def __init__(self, tool: str, uid: str, root: Path):
        self.uid, self.tool = uid, tool
        self.folder = root / tool / uid
        meta = json.loads((self.folder / f"{uid}.json").read_text())

        self.prompt = meta["instruction"]
        self.steps = meta.get("action_number", 6)
        self.container = f"sandbox-{uid[:12]}"
        self.init_steps = meta.get("config", [])
        self.evaluator = meta["evaluator"]


# ────────────────────────────────────────────────────────────────────
# 7.  Orchestrator
# ────────────────────────────────────────────────────────────────────
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

    # ------------------------------------------------------------------
    def _run_one(self, spec: TaskSpec):
        agent = build_agent(spec.container)
        ssh = agent.ssh
        cfg = agent._sandbox_executor.vm.cfg

        # a) copy host→guest artefacts
        for step in spec.init_steps:
            if step["type"] == "copyfile_from_host_to_guest":
                src = (spec.folder / step["parameters"]["src"]).resolve()
                dest = step["parameters"]["dest"]
                ssh.put_file(src, dest)
            elif step["type"] == "launch":
                cmd = " ".join(step["parameters"]["command"])
                ssh.exec_command(cmd, block=False)
            # extend other init types as needed

        # b) run the prompt
        try:
            agent.run(spec.prompt, max_steps=spec.steps)
        except Exception as exc:
            print(f"❌ {spec.uid} failed: {exc}")

        # c) evaluation
        self._evaluate(spec, ssh)

        # d) cleanup
        agent.cleanup()
        shutil.rmtree(cfg.host_container_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    def _evaluate(self, spec: TaskSpec, ssh: SSHClient):
        out_dir = Path("results") / spec.uid
        out_dir.mkdir(parents=True, exist_ok=True)

        res_cfg = spec.evaluator["result"]
        exp_cfg = spec.evaluator["expected"]

        # fetch prediction
        pred_path = out_dir / res_cfg["dest"]
        assert res_cfg["type"] == "vm_file"
        ssh.get_file(res_cfg["path"], pred_path)

        # copy gold
        gold_path = out_dir / exp_cfg["dest"]
        assert exp_cfg["type"] == "local_file"
        shutil.copy2(spec.folder / exp_cfg["path"], gold_path)

        # metric
        func_name = spec.evaluator["func"]
        metric = METRICS.get(func_name, METRICS["infeasible"])
        score = metric(pred_path, gold_path)

        (out_dir / "score.json").write_text(json.dumps({"score": score}, indent=2))
        print(f"✅ {spec.uid}  {func_name}={score}")


# ────────────────────────────────────────────────────────────────────
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
