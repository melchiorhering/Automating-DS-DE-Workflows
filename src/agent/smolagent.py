# import os

# from langchain.text_splitter import RecursiveCharacterTextSplitter
# from langchain_community.document_loaders import GithubFileLoader
# from langchain_milvus import Milvus
# from openinference.instrumentation.smolagents import SmolagentsInstrumentor
# from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import SimpleSpanProcessor
# from sentence_transformers import SentenceTransformer

# from smolagents import CodeAgent, DuckDuckGoSearchTool, HfApiModel, ToolCallingAgent

# # TELEMETRY
# # ========================
# endpoint = os.getenv("PHOENIX_TRACE_ENDPOINT", "http://0.0.0.0:6006/v1/traces")
# trace_provider = TracerProvider()
# trace_provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint)))
# SmolagentsInstrumentor().instrument(tracer_provider=trace_provider)


# # SETUP
# # ========================
# # Specify Other Models (e.g. Ollama)
# # model = LiteLLMModel(
# #     model_id="ollama_chat/llama3.2",  # This model is a bit weak for agentic behaviours though
# #     api_base="http://localhost:11434",  # replace with 127.0.0.1:11434 or remote open-ai compatible server if necessary
# #     api_key="YOUR_API_KEY",  # replace with API key if necessary
# #     num_ctx=8192,  # ollama default is 2048 which will fail horribly. 8192 works for easy tasks, more is better. Check https://huggingface.co/spaces/NyxKrage/LLM-Model-VRAM-Calculator to calculate how much VRAM this will need for the selected model.
# # )

# # Standard HF Model
# hf_model = HfApiModel(
#     model_id="Qwen/Qwen2.5-Coder-32B-Instruct",
# )

# # CogAgent Model; Specialized for GUI Automation
# cogagent = HfApiModel(model_id="THUDM/cogagent-9b-20241220", flatten_messages_as_text=False)
# # Or like this
# # High Level API
# # pipe = pipeline("image-text-to-text", model="THUDM/cogagent-9b-20241220", trust_remote_code=True)
# # Low Level API
# # cogagent = AutoModel.from_pretrained("THUDM/cogagent-9b-20241220", trust_remote_code=True)

# # AGENTS
# # ========================
# # CodeAgent: Code agent generates code (e.g. Python) to answer questions
# agent = CodeAgent(
#     name="CodeAgent",
#     tools=[DuckDuckGoSearchTool()],
#     model=hf_model,
#     planning_interval=3,  # This is where you activate planning!
#     add_base_tools=True,
# )
# # agent.run("How many seconds would it take for a leopard at full speed to run through Pont des Arts?")

# # Tool Agent: Tool agent uses tools to answer questions (more JSON-like output)
# tool_agent = ToolCallingAgent(
#     name="ToolAgent",
#     tools=[DuckDuckGoSearchTool()],
#     model=hf_model,
#     planning_interval=3,  # This is where you activate planning!
#     add_base_tools=True,
# )
# # agent.run("How many seconds would it take for a leopard at full speed to run through Pont des Arts?")

# # AGENT VISION

# agent.write_inner_memory_from_logs()

# # TOOLS
# # ========================

# # search_tool = Tool.from_langchain(load_tools(["serpapi"])[0])


# # RAG
# # ========================
# # Embedding Transformer
# embedding_model = SentenceTransformer("Alibaba-NLP/gte-Qwen2-7B-instruct", trust_remote_code=True)
# # In case you want to reduce the maximum length:
# embedding_model.max_seq_length = 8192

# # Milvus Vector Store
# URI = os.getenv("MILVUS_URI", f"{os.getenv('VOLUME_DIRECTORY')}/vector-db/milvus.db")
# vector_store = Milvus(
#     embedding_function=embedding_model,
#     connection_args={"uri": URI},
#     # Set index_params if needed
#     index_params={"index_type": "FLAT", "metric_type": "L2"},
# )

# # LOAD SOURCE DOCUMENTS
# # Text Splitter
# text_splitter = RecursiveCharacterTextSplitter(
#     chunk_size=500,
#     chunk_overlap=50,
#     add_start_index=True,
#     strip_whitespace=True,
#     separators=["\n\n", "\n", ".", " ", ""],
# )
# pyautogui_docs_raw = GithubFileLoader(
#     repo="asweigart/pyautogui",
#     access_token=os.getenv("GITHUB_TOKEN"),
#     branch="main",
#     file_filter=lambda x: x.endswith(".md" | ".txt" | "rst"),
# )
# pyautogui_docs_processed = pyautogui_docs_raw.load_and_split(text_splitter)


# # STORE IN MILVUS
# vector_store_saved = Milvus.from_documents(
#     pyautogui_docs_processed,
#     embedding_model,
#     collection_name="pyautogui_docs",
#     connection_args={"uri": URI},
# )
