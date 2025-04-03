# AI Agent Tools

This repository contains a collection of tools designed for AI agents, organized into specific categories based on their implementation frameworks.

## Repository Structure

```
📦tools
 ┣ 📂hf
 ┣ 📂mcp
 ┗ 📜README.md
```

## Tool Categories

### Hugging Face Tools (`/hf`)

The `hf` directory contains tools built for integration with Hugging Face's ecosystem, specifically designed for use with SmolAgents. These tools enable AI agents to leverage Hugging Face's models and capabilities for various tasks.

SmolAgents are lightweight AI agents that can be deployed in resource-constrained environments while still providing powerful functionality.

### MCP Tools (`/mcp`)

The `mcp` directory contains tools built using Anthropic's MCP (Machine Cognition Protocol) framework. These tools are designed to enhance AI agents with structured reasoning capabilities following Anthropic's specifications.

MCP enables AI systems to break down complex tasks into manageable steps through structured protocols, improving reliability and reasoning.

## Getting Started

Each directory contains specific documentation for the tools within that category. Please refer to the README files within each directory for detailed information on installation, usage, and contribution guidelines.

## Resources and Tools

### Hugging Face Resources

- [SmolAgents Tools](https://huggingface.co/docs/smolagents/tutorials/tools) - Official documentation for SmolAgents tools
- [Hugging Face Transformers](https://huggingface.co/docs/transformers/index) - Main library for working with transformer models
- [Inference API](https://huggingface.co/inference-api) - API for model inference
- [Datasets](https://huggingface.co/docs/datasets/index) - Access and share ML datasets
- [PEFT](https://github.com/huggingface/peft) - Parameter-Efficient Fine-Tuning methods

### MCP Resources

- [Model Context Protocol](https://modelcontextprotocol.io/quickstart/server) - Official documentation and quickstart guide for MCP

- [Python SDK](https://github.com/modelcontextprotocol/python-sdk) - The official Python SDK for Model Context Protocol servers and clients

- [HuggingFace Smolagents MCP Connection](https://huggingface.co/docs/smolagents/v1.13.0/en/reference/tools#smolagents.ToolCollection.from_mcp) - Documentation on how to connect SmolAgents to MCP servers
