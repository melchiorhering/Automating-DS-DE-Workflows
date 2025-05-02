from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from smolagents import Tool


class QdrantQueryTool(Tool):
    name = "qdrant_query"
    description = "Uses semantic search to retrieve movies from a Qdrant collection."
    inputs = {
        "query": {
            "type": "string",
            "description": "The query to perform. This should be semantically close to your target documents.",
        }
    }
    output_type = "string"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.collection_name = "smolagents"
        self.client = QdrantClient()

        if not self.client.collection_exists(self.collection_name):
            self.client.recover_snapshot(
                collection_name=self.collection_name,
                location="https://snapshots.qdrant.io/imdb-1000-jina.snapshot",
            )
        self.embedder = TextEmbedding(model_name="jinaai/jina-embeddings-v2-base-en")

    def forward(self, query: str) -> str:
        points = self.client.query_points(
            self.collection_name, query=next(self.embedder.query_embed(query)), limit=5
        ).points
        docs = "Retrieved documents:\n" + "".join(
            [
                f"== Document {str(i)} ==\n"
                + f"MOVIE TITLE: {point.payload['movie_name']}\n"
                + f"MOVIE SUMMARY: {point.payload['description']}\n"
                for i, point in enumerate(points)
            ]
        )

        return docs
