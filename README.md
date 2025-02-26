# Spider2-V: Benchmarking Multimodal Agents in Data Science and Engineering Workflows

This repository supports work on **[Spider2-V](https://spider2-v.github.io/)**, a benchmark designed to evaluate and advance multimodal agent automation in data science and engineering workflows. Spider2-V focuses on real-world applications within professional data environments, aiming to pave the way for vision-language model (VLM)-based agents to efficiently automate complex tasks across various tools and stages of the data lifecycle.

## Abstract

Data science and engineering workflows span diverse stages, from data warehousing to orchestration, utilizing tools such as BigQuery, dbt, and Airbyte. As VLMs gain sophistication in multimodal understanding and code generation, the potential for VLM-based agents to automate these workflows has grown. By generating SQL queries, Python code, and GUI operations, such agents can both enhance expert productivity and democratize large-scale data analysis.

**Spider2-V** introduces the first multimodal agent benchmark dedicated to professional data workflows, covering:

- **494 tasks** derived from real-world, enterprise use cases within authentic computer environments.
- **20 enterprise-level applications**, where tasks assess an agent's ability to perform data-centric operations, including code writing and GUI navigation.

The project balances realistic simulation with evaluation simplicity through:

- Automatic configurations for streamlined task setup.
- Task-specific metrics to accurately measure performance.

Comprehensive documentation supplements multimodal agents for these professional data applications, highlighting the challenges agents face in automating full workflows. Empirical findings reveal that existing VLM agents currently automate only 14.0% of workflows successfully. Even with guidance, these agents struggle with knowledge-intensive GUI actions (16.2% success rate) and tasks within cloud-hosted workspaces (10.6% success rate).

## Master Thesis Goals

The goals of this master thesis project are:

1. **Environment Setup:** Configure and deploy an updated benchmarking environment to enable better and flexible testing and evaluation of multimodal agents on the bases of the Spider2-V benchmark.
   1. Easier integrations with LLM and data toosl
   2. Adding a vector DB
2. **Research Contribution:** Once the environment is operational, contribute additional features and capabilities to Spider2-V, advancing the research on automated data science and engineering workflows.
   1. Try to improve the current scores using better implementations of the current framework or using tools/integrations
   2. Improve Feedback loop and Agent Validation steps

## 🛠️ Prerequisites

Before setting up, ensure you have the following dependencies installed:

- **[Python 3.11](https://www.python.org/downloads/release/python-3110/)** - Python!
- **[UV](https://docs.astral.sh/uv/guides/install-python/)** – A fast Python package manager.
- **[GIT LFS](https://git-lfs.com/)** - Download large files using GIT
- **[Direnv](https://direnv.net/)** – Manages environment variables per project.
- **[MacTeX](https://www.tug.org/mactex/)** - For working locally with LaTex

#### Virtual Machine/Containerization:

- **[Docker](https://www.docker.com/)** – Required for containerized deployments.
- **[Podman](https://podman.io/)** - Required for containerized deployments.
- **[VMware Fusion](https://www.vmware.com/go/getfusion)** - A Virtual Machine application

## 🚀 Getting Started

Clone the repository and install dependencies:

```sh
git clone https://github.com/melchiorhering/IS-master-thesis.git
cd IS-master-thesis
```

Copy the existing `.envrc.example` file and rename it to `.envrc`:

```sh
cp .envrc.example .envrc
```

Activate `direnv`:

```sh
direnv allow
```

Install the current dependencies using UV:

```sh
uv lock
```

Upgrade dependencies with UV:

```sh
uv lock --upgrade
```

## 📡 Telemetry and Database Setup 🛢

Both **OpenLit** for telemetry and **Milvus** as a vector database are managed using **Docker Compose**. The required services, including ClickHouse, OpenLit, OpenTelemetry Collector, Milvus, MinIO, and ETCD, are defined in the `docker-compose.yaml` file.

### 🚀 Running OpenLit and Milvus with Docker Compose

1. **Ensure Docker and Docker Compose are installed**:

   - [Install Docker](https://www.docker.com/get-started)
   - [Install Docker Compose](https://docs.docker.com/compose/install/)

2. **Start all required services**:

   ```sh
   docker compose up -d
   ```

This will start:

- **ClickHouse** – The database backend for OpenLit telemetry.
- **OpenLit** – A real-time monitoring and observability tool.
- **OpenTelemetry Collector** – Collects and forwards telemetry data.
- **Milvus** – A high-performance, scalable vector database.
- **MinIO** – Object storage required by Milvus.
- **ETCD** – Metadata store for Milvus.

3. **Access OpenLit dashboard**:

   - OpenLit runs on **http://localhost:3000**.

4. **Milvus API is available at**:
   - `localhost:19530`

## ⚡ AI Agent Frameworks

Depending on the module you use, you may need to install an AI agent framework. The following are supported and can be installed using `uv`:

- **[CrewAI](https://github.com/crewai/crewai)** – Multi-agent AI orchestration.
- **[LangChain](https://github.com/hwchase17/langchain)** – Modular LLM-powered pipelines.
- **[Pydantic-AI](https://github.com/pydantic/pydantic-ai)** – AI-driven data validation and processing.
- **[Smolagents](https://github.com/huggingface/smolagents)** - A smol library to build great agents.

To install a dependency with `uv`, run:

```sh
uv add <package> <package> # etc

# OR

# CrewAI uses a special install script that is not compatible with UV, therefore fallback to "uv pip install ..."
uv pip install crewai crewai-tools # Replace with the framework you need
# Then
uv add crewai crewai-tools # for adding it to pyproject.toml
```
