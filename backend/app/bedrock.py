"""Thin wrappers around AWS Bedrock: embeddings, rerank and Claude (Converse API)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

from .config import settings

_local = threading.local()
_cfg = Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=180, max_pool_connections=32)


def _session() -> boto3.Session:
    return boto3.Session(profile_name=settings.aws_profile or None)


def _runtime():
    if not hasattr(_local, "rt"):
        _local.rt = _session().client("bedrock-runtime", region_name=settings.aws_region, config=_cfg)
    return _local.rt


def _agent_runtime():
    if not hasattr(_local, "ar"):
        _local.ar = _session().client("bedrock-agent-runtime", region_name=settings.rerank_region, config=_cfg)
    return _local.ar


def embed(texts: list[str], input_type: str = "search_document") -> list[list[float]]:
    """Cohere Embed v4 (multilingual, Arabic + English). Batches of 96 run in parallel."""
    batches = [texts[i:i + 96] for i in range(0, len(texts), 96)]

    def run(batch: list[str]) -> list[list[float]]:
        body = {"texts": [t[:6000] or " " for t in batch], "input_type": input_type,
                "embedding_types": ["float"], "truncate": "END"}
        if "v4" in settings.embed_model:
            body["output_dimension"] = settings.embed_dim
        r = _runtime().invoke_model(modelId=settings.embed_model, body=json.dumps(body))
        e = json.loads(r["body"].read())["embeddings"]
        return e["float"] if isinstance(e, dict) else e

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [v for part in pool.map(run, batches) for v in part]


def rerank(query: str, docs: list[str]) -> list[float] | None:
    """Cohere Rerank 3.5 relevance in [0,1] for each doc (same order). None if unavailable."""
    if not docs:
        return []
    try:
        r = _agent_runtime().rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query[:2000]}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": d[:4000] or " "}}}
                     for d in docs],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                "numberOfResults": len(docs),
                "modelConfiguration": {"modelArn": f"arn:aws:bedrock:{settings.rerank_region}::foundation-model/{settings.rerank_model}"},
            }},
        )
    except Exception:
        return None
    scores = [0.0] * len(docs)
    for item in r["results"]:
        scores[item["index"]] = float(item["relevanceScore"])
    return scores


def converse_tool(model: str, system: str, messages: list[dict], tool: dict, max_tokens: int = 4000) -> dict:
    """Force Claude to answer through a single JSON-schema tool and return the tool input."""
    r = _runtime().converse(
        modelId=model,
        system=[{"text": system}],
        messages=messages,
        toolConfig={"tools": [{"toolSpec": tool}], "toolChoice": {"tool": {"name": tool["name"]}}},
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    for block in r["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise RuntimeError("model did not return structured output")


def image_block(png: bytes) -> dict:
    return {"image": {"format": "png", "source": {"bytes": png}}}
