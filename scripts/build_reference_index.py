#!/usr/bin/env python3
"""Build the FieldOS reference search index.

Reads markdown + CSV reference sources, chunks them, and writes embeddings to
`data/reference_index.jsonl`. When an embedding model or API key is not
available, copies the deterministic stub index instead (tests/fixtures/...).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEST_FIXTURES = ROOT / "tests" / "fixtures"

COMPANY_WIKI = DATA_DIR / "company_wiki.md"
CRM_SAMPLE = DATA_DIR / "crm_sample.csv"
SALES_PLAYBOOK_MD = DATA_DIR / "sales_playbook.md"
SALES_PLAYBOOK_JSON = DATA_DIR / "playbooks.json"
PRICING_JSON = DATA_DIR / "pricing.json"

INDEX_PATH = DATA_DIR / "reference_index.jsonl"
META_PATH = DATA_DIR / "reference_index.meta.json"
STUB_INDEX = TEST_FIXTURES / "reference_index_stub.jsonl"

DEFAULT_EMBED_MODEL = os.getenv("FIELDOS_CHAT_EMBED_MODEL", "text-embedding-3-small")

TOKEN_CHUNK_SIZE = 200
TOKEN_OVERLAP = 20


def _load_openai_client():
    """Try to reuse the shared OpenAI client; return None if unavailable."""
    try:
        from ai_parser import _get_openai_client  # type: ignore
    except ImportError:  # pragma: no cover - defensive
        _get_openai_client = None  # type: ignore

    if _get_openai_client is None:
        # Fall back to creating a client directly (OpenAI>=1.0)
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:  # pragma: no cover
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return OpenAI()

    try:
        client = _get_openai_client()
    except RuntimeError:
        return None
    return client


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _tokenizer():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover - fallback
        return None


ENCODER = _tokenizer()


def _split_tokens(tokens: Sequence[int], chunk_size: int, overlap: int) -> List[Sequence[int]]:
    if not tokens:
        return []
    if len(tokens) <= chunk_size:
        return [tokens]
    chunks: List[Sequence[int]] = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + chunk_size)
        chunks.append(tokens[start:end])
        if end == len(tokens):
            break
        start = max(0, end - overlap)
    return chunks


def _chunk_text(text: str) -> List[str]:
    cleaned = _normalise(text)
    if not cleaned:
        return []
    if ENCODER:
        tokens = ENCODER.encode(cleaned)
        segments = []
        for token_chunk in _split_tokens(tokens, TOKEN_CHUNK_SIZE, TOKEN_OVERLAP):
            segments.append(ENCODER.decode(list(token_chunk)))
        return segments
    # Fallback: approximate tokens with words
    words = cleaned.split()
    approx_chunk = int(TOKEN_CHUNK_SIZE / 0.75)
    approx_overlap = int(TOKEN_OVERLAP / 0.75)
    if len(words) <= approx_chunk:
        return [" ".join(words)]
    segments = []
    start = 0
    while start < len(words):
        end = min(len(words), start + approx_chunk)
        segments.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(0, end - approx_overlap)
    return segments


DEFAULT_VALUE_PROPS = [
    "Highlight service reliability and quick turnaround",
    "Focus on ROI and maintenance savings",
]


def _collect_service_value_props(playbook_data: Dict, pricing_data: Dict) -> Dict[str, List[str]]:
    value_map: Dict[str, List[str]] = {}
    for service, entries in playbook_data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            props = entry.get("value_props")
            if isinstance(props, list) and props:
                bucket = value_map.setdefault(service, [])
                for prop in props:
                    if prop not in bucket:
                        bucket.append(prop)
    for service, payload in pricing_data.items():
        props = payload.get("value_props")
        if isinstance(props, list) and props:
            bucket = value_map.setdefault(service, [])
            for prop in props:
                if prop not in bucket:
                    bucket.append(prop)
    return value_map


def _collect_service_discounts(pricing_data: Dict) -> Dict[str, str]:
    discount_map: Dict[str, str] = {}
    for service, payload in pricing_data.items():
        discount = payload.get("discount")
        if isinstance(discount, str) and discount:
            discount_map[service] = discount
    return discount_map


@dataclass
class Chunk:
    id: str
    source: str
    title: str
    content: str
    url: str
    tags: List[str] = field(default_factory=list)
    value_props: List[str] = field(default_factory=list)
    discount: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _chunk_markdown(path: Path, source: str, base_url: str) -> Iterable[Chunk]:
    if not path.exists():
        return []
    current_title = path.stem.replace("_", " ").title()
    buffer: List[str] = []
    chunks: List[Chunk] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("### "):
                if buffer:
                    chunks.extend(_flush_markdown_buffer(buffer, current_title, source, base_url))
                    buffer.clear()
                current_title = line[4:].strip()
            elif line.startswith("## "):
                if buffer:
                    chunks.extend(_flush_markdown_buffer(buffer, current_title, source, base_url))
                    buffer.clear()
                current_title = line[3:].strip()
            elif line.startswith("#"):
                continue  # ignore h1
            else:
                buffer.append(line)
    if buffer:
        chunks.extend(_flush_markdown_buffer(buffer, current_title, source, base_url))
    return chunks


def _flush_markdown_buffer(buffer: List[str], title: str, source: str, base_url: str) -> List[Chunk]:
    text = "\n".join(buffer)
    segments = _chunk_text(text)
    results: List[Chunk] = []
    for idx, segment in enumerate(segments):
        chunk_id = f"{source}_{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')}_{idx}"
        results.append(
            Chunk(
                id=chunk_id,
                source=source,
                title=title,
                content=segment,
                url=f"{base_url}",
                metadata={"category": "general"},
            )
        )
    return results


def _chunk_crm_rows(path: Path) -> Iterable[Chunk]:
    if not path.exists():
        return []
    chunks: List[Chunk] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            customer_id = _normalise(row.get("Customer_ID", "") or "")
            name = _normalise(row.get("Customer_Name", "") or "Customer")
            customer_type = _normalise(row.get("Customer_Type", "") or "")
            service = _normalise(row.get("Service_Interest", "") or "")
            contact = _normalise(row.get("Primary_Contact", "") or "primary contact")
            phone = _normalise(row.get("Contact_Phone", "") or "")
            email = _normalise(row.get("Contact_Email", "") or "")
            stage = _normalise(row.get("Stage", "") or "")
            rep = _normalise(row.get("Assigned_Rep", "") or "")
            region = _normalise(row.get("Region", "") or "")
            summary = _normalise(row.get("Summary", "") or row.get("Notes", "") or "")
            if summary:
                summary = summary[:200]

            parts = [
                f"{name} ({customer_type}) — {service}." if service else f"{name} ({customer_type}).",
                f"Primary contact {contact}{f' ({phone})' if phone else ''}.",
                f"Email {email}." if email else "",
                f"Stage {stage}, rep {rep}, region {region}.",
                f"Customer ID {customer_id}.",
                summary,
            ]
            content = _normalise(" ".join(part for part in parts if part))
            if not content:
                continue
            chunk_id = f"crm_{customer_id or name.lower().replace(' ', '_')}"
            metadata = {
                "category": "general",
                "record_type": "account",
                "customer_id": customer_id,
                "service": service,
                "stage": stage,
                "assigned_rep": rep,
            }
            chunks.append(
                Chunk(
                    id=chunk_id,
                    source="crm",
                    title=f"{name} Summary",
                    content=content,
                    url="#crm-snapshot",
                    metadata=metadata,
                )
            )
    return chunks


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _derive_value_props(service: str, snippet: str, tags: List[str], *, curated: Optional[Dict[str, List[str]]] = None) -> List[str]:
    props: List[str] = []
    if curated and service in curated:
        props.extend(curated[service])
    snippet_lower = snippet.lower()
    if "curb" in snippet_lower and "Boost curb appeal with fresh mulch" not in props:
        props.append("Boost curb appeal with fresh mulch")
    if "weed" in snippet_lower and "Suppress weeds and retain soil moisture" not in props:
        props.append("Suppress weeds and retain soil moisture")
    if "winter" in snippet_lower and "Protect installations in winter conditions" not in props:
        props.append("Protect installations in winter conditions")
    if not props:
        props.extend(DEFAULT_VALUE_PROPS)
    seen = set()
    unique_props: List[str] = []
    for prop in props:
        if prop not in seen:
            seen.add(prop)
            unique_props.append(prop)
    props = unique_props[:3] if unique_props else DEFAULT_VALUE_PROPS[:3]
    return props


def _extract_discount(
    snippet: str,
    service: Optional[str] = None,
    *,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    match = re.search(r"(\d+%)", snippet)
    if match:
        percent = match.group(1)
        return f"{percent} promo"
    if overrides and service:
        return overrides.get(service)
    return None


def _chunk_playbook_entries(playbook_data: Dict, pricing_data: Dict, value_props_map: Dict[str, List[str]], discount_map: Dict[str, str]) -> Iterable[Chunk]:
    chunks: List[Chunk] = []
    for service, entries in playbook_data.items():
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            snippet_text = _normalise(entry.get("snippet", ""))
            if not snippet_text:
                continue
            tags = entry.get("tags") or []
            pricing_meta = pricing_data.get(service) or pricing_data.get("default", {})
            value_props_source = entry.get("value_props") or value_props_map.get(service) or pricing_meta.get("value_props")
            value_props = value_props_source or _derive_value_props(service, snippet_text, tags, curated=value_props_map)
            discount = entry.get("discount") or discount_map.get(service) or pricing_meta.get("discount") or _extract_discount(snippet_text, service, overrides=discount_map)
            metadata: Dict[str, object] = {
                "category": "value_prop",
                "service": service,
            }
            if pricing_meta:
                metadata["base_price"] = pricing_meta.get("base_price")
                metadata["upsells"] = pricing_meta.get("upsells", [])
                metadata["pricing_unit"] = pricing_meta.get("unit", "USD")
                if pricing_meta.get("last_updated"):
                    metadata["last_updated"] = pricing_meta["last_updated"]
            if entry.get("last_updated"):
                metadata["last_updated"] = entry["last_updated"]
            chunk_id = f"playbook_{_slugify(service)}_{idx}"
            chunks.append(
                Chunk(
                    id=chunk_id,
                    source="playbook",
                    title=entry.get("title") or f"{service} Playbook",
                    content=snippet_text,
                    url="#sales-playbook",
                    tags=list(tags),
                    value_props=value_props,
                    discount=discount,
                    metadata=metadata,
                )
            )
    return chunks


def _chunk_pricing(pricing_data: Dict) -> Iterable[Chunk]:
    chunks: List[Chunk] = []
    for service, payload in pricing_data.items():
        if not isinstance(payload, dict):
            continue
        base_price = payload.get("base_price")
        upsells = payload.get("upsells") or []
        unit = payload.get("unit", "USD")
        lines = []
        if base_price is not None:
            lines.append(f"Base price: {base_price} {unit}")
        if upsells:
            upsell_descriptions = [
                f"{item.get('name')} (${item.get('price')})" for item in upsells if isinstance(item, dict)
            ]
            if upsell_descriptions:
                lines.append("Upsells: " + "; ".join(upsell_descriptions))
        content = " ".join(lines) if lines else "Pricing details not provided."
        value_props = payload.get("value_props") if isinstance(payload.get("value_props"), list) else []
        metadata: Dict[str, object] = {
            "category": "pricing",
            "service": service,
            "base_price": base_price,
            "upsells": upsells,
            "pricing_unit": unit,
            "value_props": value_props,
        }
        if payload.get("last_updated"):
            metadata["last_updated"] = payload["last_updated"]
        discount = payload.get("discount")
        chunks.append(
            Chunk(
                id=f"pricing_{_slugify(service)}",
                source="pricing",
                title=f"{service} Pricing",
                content=content,
                url="#company-wiki",
                tags=["pricing"],
                value_props=list(value_props),
                discount=discount,
                metadata=metadata,
            )
        )
    return chunks


def _gather_chunks() -> List[Chunk]:
    chunks: List[Chunk] = []
    pricing_data = _load_json(PRICING_JSON)
    playbook_data = _load_json(SALES_PLAYBOOK_JSON)
    value_props_map = _collect_service_value_props(playbook_data, pricing_data)
    discount_map = _collect_service_discounts(pricing_data)
    chunks.extend(_chunk_markdown(COMPANY_WIKI, "wiki", "#company-wiki"))
    chunks.extend(_chunk_crm_rows(CRM_SAMPLE))
    if SALES_PLAYBOOK_MD.exists():
        chunks.extend(_chunk_markdown(SALES_PLAYBOOK_MD, "playbook", "#sales-playbook"))
    chunks.extend(_chunk_playbook_entries(playbook_data, pricing_data, value_props_map, discount_map))
    chunks.extend(_chunk_pricing(pricing_data))
    return chunks


def _embed_chunks(chunks: List[Chunk], model: str) -> Optional[List[List[float]]]:
    client = _load_openai_client()
    if client is None:
        return None
    texts = [chunk.content for chunk in chunks]
    embeddings: List[List[float]] = []
    # Batch requests to avoid payload limits (max 2048 tokens per chunk).
    batch_size = 64
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        for item in response.data:
            embeddings.append(list(item.embedding))  # type: ignore[attr-defined]
    return embeddings


def _write_index(chunks: List[Chunk], vectors: List[List[float]]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as handle:
        for chunk, vector in zip(chunks, vectors):
            record = {
                "id": chunk.id,
                "vector": vector,
                "source": chunk.source,
                "title": chunk.title,
                "content": chunk.content,
                "url": chunk.url,
                "tags": chunk.tags,
                "value_props": chunk.value_props,
                "discount": chunk.discount,
                "metadata": chunk.metadata,
            }
            handle.write(json.dumps(record) + "\n")
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_EMBED_MODEL,
        "doc_count": len(chunks),
    }
    with META_PATH.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)


def _copy_stub_index() -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    stub_text = STUB_INDEX.read_text(encoding="utf-8")
    INDEX_PATH.write_text(stub_text, encoding="utf-8")
    doc_count = len([line for line in stub_text.splitlines() if line.strip()])
    META_PATH.write_text(
        json.dumps(
            {
                "built_at": datetime.now(timezone.utc).isoformat(),
                "model": "stub",
                "doc_count": doc_count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"No API key detected; using stub index at {INDEX_PATH}.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build FieldOS reference search index.")
    parser.add_argument("--model", default=DEFAULT_EMBED_MODEL, help="Embedding model (default text-embedding-3-small)")
    parser.add_argument("--use-stub", action="store_true", help="Force using the deterministic stub index.")
    args = parser.parse_args(argv)

    if args.use_stub or os.getenv("FIELDOS_CHAT_USE_STUB", "").lower() == "true":
        _copy_stub_index()
        return 0

    chunks = _gather_chunks()
    if not chunks:
        print("No reference chunks found; did you populate data/ sources?", file=sys.stderr)
        _copy_stub_index()
        return 0

    embeddings = _embed_chunks(chunks, args.model)
    if embeddings is None:
        _copy_stub_index()
        return 0

    _write_index(chunks, embeddings)
    print(f"Wrote {len(chunks)} chunks to {INDEX_PATH} using model {args.model}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
