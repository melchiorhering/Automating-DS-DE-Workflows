## Extending Spider2‑V: An All‑Python, Future‑Proof Benchmark Framework

Building on top of the core Spider2‑V benchmark, our new framework is now **100 % Python**—from sandbox orchestration to in‑VM agent logic—bringing:

1. **Dockerized KVM sandbox, driven by Python**

   - A lightweight orchestration layer (`docker` + QEMU/KVM bindings) spins up dozens of guest VMs in parallel inside Docker containers.
   - Upgrading or swapping OS images is a one‑line config change—no more manual VMware snapshots.
   - Ths makes it possible to use multiple agents each with their own VM to interact with, speeding up the benchmark testing.

2. **Guest‑agent server (local today, modular‑package roadmap)**

   - _Current state_: the Python server that exposes in‑VM APIs (filesystem, process control, GUI capture, etc.) lives alongside the benchmark codebase and is copied into each guest at runtime.
   - _Road‑map_: break this server out into a pip‑installable package so it can be versioned on PyPI/Git; at that point you’ll develop locally with pure‑Python mocks and ship updates via `pip install --upgrade` or SFTP sync—no VM rebuilds required.

3. **`smolagents`: a Python‑native agent framework**

   - Planner, retriever, and executor are pure Python.
   - Define new agent behaviours by subclassing `smolagents.Agent` and registering tools—no polyglot stack.
   - Swap underlying LLMs (OpenAI, HuggingFace Transformers, vLLM, etc.) via a pluggable adapter interface.

4. **Tool‑first design via a Pythonic Model–Context Protocol (MCP)**

   - VM operations are exposed as typed callables—`run_command()`, `screenshot()`, `query_db()`, …—importable by any agent.
   - The Retrieval‑Augmented‑Generation (RAG) pipeline is wrapped as just another tool, so agents can “call” corporate‑doc search exactly like any other action.
   - This abstraction keeps the stack modular, introspectable, and trivially extensible.

5. **Pythonic dependency & environment management with [`uv`](https://github.com/astral-sh/uv)**

   - Every component—sandbox, guest server, agents—is version‑pinned in a single `pyproject.toml`.
   - `uv` handles **lock‑file creation, fast resolver, and virtual‑environment provisioning** in one command:
     ```bash
     uv run <file>
     ```
   - Reproducible builds across dev machines and CI runners, with cold‑start installs up to 10× faster than legacy `pip`.

6. **Python‑first CI/CD & metrics**

   - End‑to‑end regression runs in **pytest**; GitHub Actions can spin up real VMs for each PR.
   - Success rates, latencies, and error taxonomies are logged as JSON—ready for Prometheus/Grafana dashboards.

### Why it matters

- **Future‑proof & OS‑agnostic** – point a Python config at a new QEMU image and go.
- **Developer‑friendly** – one language, one tool‑chain, zero context‑switching.
- **Highly modular** – drop‑in new data sources, UI‑automation hooks, or retrieval pipelines as plain Python tools.
- **Blazingly scalable** – dozens of concurrent VMs on a laptop, hundreds in the cloud.
- **Reproducible & fast** – `uv` guarantees deterministic, cache‑friendly installs both locally and in CI.

### Easier baseline improvement

Because agents, tools, and tasks are all first‑class Python objects, researchers can:

- **Prototype new agent steps** (advanced planning, self‑critique, synthetic “rubber‑duck” debugging) in minutes.
- **Swap or compose tools** to test ablations—see exactly which capability lifts Spider2‑V scores.
- **Contribute back** clean, isolated PRs that raise the benchmark baseline without touching core orchestration.

This all‑Python, `uv`‑powered re‑architecture keeps the benchmark realistic for enterprise workflows while making it **far more manageable, extensible, and experimental‑agent‑friendly**.
