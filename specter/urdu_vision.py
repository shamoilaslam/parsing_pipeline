"""Budgeted Urdu crop transcription with provider fallback.

This stage never reparses the PDF.  It consumes the deterministic Specter JSON,
renders only blocks marked ``contains_rtl``, and writes accepted logical Urdu
back into those same blocks while preserving the native text and crop bbox.

Provider order in ``auto`` mode:
    Gemini -> OpenRouter free vision router -> optional LlamaParse fast tier

The queue and quota files are local JSON so an interrupted or rate-limited run
can be resumed without resending completed crops.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import fitz

from specter.specter_parser import SpecterParser, _is_rtl


DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_LLAMA_BASE = "https://api.cloud.llamaindex.ai"


class ProviderLimit(RuntimeError):
    """The provider rejected the request because of quota/rate availability."""


class ProviderError(RuntimeError):
    """The provider returned a non-quota error or malformed output."""


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load simple KEY=value entries without printing or overwriting variables."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {403, 404, 408, 429, 500, 502, 503, 504, 529}:
            raise ProviderLimit(f"provider status {exc.code}: {body[:180]}") from exc
        raise ProviderError(f"provider status {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise ProviderLimit(f"provider unavailable: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"provider returned non-JSON: {body[:300]}") from exc


def _get_json(url: str, headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {403, 404, 408, 429, 500, 502, 503, 504, 529}:
            raise ProviderLimit(f"provider status {exc.code}: {body[:180]}") from exc
        raise ProviderError(f"provider status {exc.code}: {body[:300]}") from exc
    except (URLError, json.JSONDecodeError) as exc:
        raise ProviderLimit(f"provider unavailable: {exc}") from exc


def _response_text(payload: dict[str, Any]) -> str:
    parts = []
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
    if parts:
        return "\n".join(parts)
    choices = payload.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


def _parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"vision response was not valid JSON: {cleaned[:300]}") from exc
    if isinstance(value, list):
        value = {"items": value}
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ProviderError("vision response must contain an items array")
    return value


def _validate_items(payload: dict[str, Any], tasks: list[dict[str, Any]], provider: str, model: str) -> list[dict[str, Any]]:
    expected = {task["id"]: task for task in tasks}
    seen: set[str] = set()
    results = []
    for item in payload["items"]:
        if not isinstance(item, dict) or item.get("id") not in expected:
            raise ProviderError("vision response contained an unknown crop id")
        task_id = item["id"]
        if task_id in seen:
            raise ProviderError(f"vision response duplicated crop id {task_id}")
        seen.add(task_id)
        text = str(item.get("text", "")).strip()
        if not text or not any(_is_rtl(char) for char in text):
            raise ProviderError(f"vision response had no Urdu text for {task_id}")
        confidence = item.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.75
        results.append({
            "id": task_id,
            "text": text,
            "confidence": max(0.0, min(1.0, confidence)),
            "provider": provider,
            "model": model,
            "status": "accepted",
        })
    if seen != set(expected):
        missing = sorted(set(expected) - seen)
        raise ProviderError(f"vision response omitted crop ids: {missing}")
    return results


@dataclass
class Quota:
    name: str
    state_path: Path
    max_requests: int
    min_interval_seconds: float

    def reserve(self) -> None:
        now = time.time()
        today = datetime.now(timezone.utc).date().isoformat()
        state = {"date": today, "count": 0, "last_request": 0.0}
        if self.state_path.exists():
            try:
                state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        if state.get("date") != today:
            state = {"date": today, "count": 0, "last_request": 0.0}
        if int(state.get("count", 0)) >= self.max_requests:
            raise ProviderLimit(f"{self.name} local daily budget exhausted")
        wait = self.min_interval_seconds - (now - float(state.get("last_request", 0.0)))
        if wait > 0:
            time.sleep(wait)
        state["count"] = int(state.get("count", 0)) + 1
        state["last_request"] = time.time()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _prompt(tasks: list[dict[str, Any]]) -> str:
    ids = ", ".join(task["id"] for task in tasks)
    return (
        "Transcribe the Urdu text visible in the supplied crop images. "
        "Do not translate, summarize, transliterate, normalize, or add commentary. "
        "Preserve Urdu spelling, diacritics, punctuation, digits, paragraph numbering, "
        "and line breaks where visible. Return JSON only with this exact shape: "
        '{"items":[{"id":"same crop id","text":"exact Urdu transcription","confidence":0.0}]}. '
        f"The crop ids, in image order, are: {ids}. Return every id exactly once."
    )


class GeminiVision:
    name = "gemini"

    def __init__(self, quota_dir: Path):
        self.key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_URDU_MODEL", DEFAULT_GEMINI_MODEL)
        self.quota = Quota("gemini", quota_dir / "gemini_quota.json", int(os.getenv("GEMINI_MAX_REQUESTS_PER_DAY", "20")), float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "2.0")))

    def complete(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.key:
            raise ProviderLimit("GEMINI_API_KEY is not configured")
        self.quota.reserve()
        parts: list[dict[str, Any]] = [{"text": _prompt(tasks)}]
        for task in tasks:
            parts.append({"text": f"\nCROP_ID={task['id']}\n"})
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(Path(task["image"]).read_bytes()).decode("ascii")}})
        payload = _json_request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.key}",
            {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 4096}},
            {},
        )
        return _validate_items(_parse_model_json(_response_text(payload)), tasks, self.name, self.model)


class OpenRouterVision:
    name = "openrouter"

    def __init__(self, quota_dir: Path):
        self.key = os.getenv("OPENROUTER_API_KEY")
        self.model = os.getenv("OPENROUTER_URDU_MODEL", DEFAULT_OPENROUTER_MODEL)
        if self.model != "openrouter/free" and not self.model.endswith(":free"):
            raise ValueError("OpenRouter Urdu model must be openrouter/free or an explicit :free model")
        self.quota = Quota("openrouter", quota_dir / "openrouter_quota.json", int(os.getenv("OPENROUTER_MAX_REQUESTS_PER_DAY", "40")), float(os.getenv("OPENROUTER_MIN_INTERVAL_SECONDS", "3.2")))

    def complete(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.key:
            raise ProviderLimit("OPENROUTER_API_KEY is not configured")
        self.quota.reserve()
        content: list[dict[str, Any]] = [{"type": "text", "text": _prompt(tasks)}]
        for task in tasks:
            encoded = base64.b64encode(Path(task["image"]).read_bytes()).decode("ascii")
            content.extend([{"type": "text", "text": f"CROP_ID={task['id']}"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}])
        payload = _json_request(
            "https://openrouter.ai/api/v1/chat/completions",
            {"model": self.model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 4096, "response_format": {"type": "json_object"}},
            {"Authorization": f"Bearer {self.key}", "HTTP-Referer": "https://github.com/specter-parser", "X-Title": "Specter Urdu crop transcription"},
        )
        return _validate_items(_parse_model_json(_response_text(payload)), tasks, self.name, self.model)


def _multipart(file_path: Path, configuration: dict[str, Any]) -> tuple[bytes, str]:
    boundary = "----Specter" + uuid.uuid4().hex
    chunks = []
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_path.name}\"\r\nContent-Type: image/png\r\n\r\n".encode() + file_path.read_bytes() + b"\r\n")
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"configuration\"\r\n\r\n{json.dumps(configuration)}\r\n".encode())
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class LlamaParseVision:
    name = "llamaparse"

    def __init__(self, quota_dir: Path):
        self.key = os.getenv("LLAMA_CLOUD_API_KEY") or os.getenv("LLAMAPARSE_API_KEY")
        self.base = os.getenv("LLAMAPARSE_BASE_URL", DEFAULT_LLAMA_BASE).rstrip("/")
        self.quota = Quota("llamaparse", quota_dir / "llamaparse_quota.json", int(os.getenv("LLAMAPARSE_MAX_REQUESTS_PER_DAY", "2")), float(os.getenv("LLAMAPARSE_MIN_INTERVAL_SECONDS", "5.0")))

    def complete(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.key:
            raise ProviderLimit("LLAMA_CLOUD_API_KEY is not configured")
        results = []
        for task in tasks:
            self.quota.reserve()
            body, content_type = _multipart(Path(task["image"]), {"tier": "fast", "version": "latest"})
            request = Request(f"{self.base}/api/v2/parse/upload", data=body, headers={"Authorization": f"Bearer {self.key}", "Content-Type": content_type}, method="POST")
            try:
                with urlopen(request, timeout=120) as response:
                    job = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code in {403, 408, 429, 500, 502, 503, 504, 529}:
                    raise ProviderLimit(f"llamaparse status {exc.code}") from exc
                raise ProviderError(f"llamaparse status {exc.code}") from exc
            job_id = job.get("id")
            if not job_id:
                raise ProviderError("llamaparse upload did not return a job id")
            deadline = time.time() + 300
            result = {}
            while time.time() < deadline:
                result = _get_json(f"{self.base}/api/v2/parse/{job_id}?{urlencode({'expand': 'markdown'})}", {"Authorization": f"Bearer {self.key}"})
                status = str(result.get("job", {}).get("status", result.get("status", ""))).upper()
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(5)
            text = _extract_llama_text(result)
            if not text or not any(_is_rtl(char) for char in text):
                raise ProviderError(f"llamaparse returned no Urdu text for {task['id']}")
            results.append({"id": task["id"], "text": text.strip(), "confidence": 0.70, "provider": self.name, "model": "fast", "status": "accepted"})
        return results


def _extract_llama_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text for item in value if (text := _extract_llama_text(item)))
    if isinstance(value, dict):
        for key in ("markdown_full", "markdown", "text_full", "text"):
            if key in value:
                text = _extract_llama_text(value[key])
                if text:
                    return text
        for child in value.values():
            text = _extract_llama_text(child)
            if text and any(_is_rtl(char) for char in text):
                return text
    return ""


def prepare_queue(pdf_path: Path, prediction_path: Path, output_dir: Path, scale: float = 4.0) -> Path:
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    stem = pdf_path.stem
    queue_dir = output_dir / "vision_queue"
    image_dir = output_dir / "assets" / stem / "vision"
    queue_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    tasks = []
    doc = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            for block in prediction.get("pages", [])[page_index].get("blocks", []):
                if not block.get("contains_rtl"):
                    continue
                path = image_dir / f"page_{page_index + 1:03d}_{block['id']}.png"
                if not path.exists():
                    clip = fitz.Rect(*block["bbox"]).irect & page.rect.irect
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(clip), alpha=False)
                    pix.save(str(path))
                tasks.append({"id": block["id"], "page": page_index + 1, "bbox": block["bbox"], "image": str(path), "status": "pending"})
    finally:
        doc.close()
    path = queue_dir / f"{stem}.json"
    previous: dict[str, Any] = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_status = {task.get("id"): task.get("status") for task in previous.get("tasks", [])}
    for task in tasks:
        if previous_status.get(task["id"]) == "accepted":
            task["status"] = "accepted"
    manifest = {"version": 1, "pdf": str(pdf_path), "prediction": str(prediction_path), "created_at": previous.get("created_at", datetime.now(timezone.utc).isoformat()), "tasks": tasks}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _apply_results(prediction_path: Path, output_dir: Path, manifest: dict[str, Any], results: list[dict[str, Any]], replace_text: bool = True) -> Path:
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in results}
    accepted = 0
    for page in prediction.get("pages", []):
        for block in page.get("blocks", []):
            result = by_id.get(block["id"])
            if not result:
                continue
            block.setdefault("rtl", {})["native_text"] = block.get("text", "")
            image_path = next((task["image"] for task in manifest["tasks"] if task["id"] == block["id"]), None)
            try:
                image_path = str(Path(image_path).relative_to(output_dir)).replace("\\", "/") if image_path else None
            except ValueError:
                pass
            block["rtl"]["vision"] = {**result, "image": image_path}
            block["confidence"]["score"] = round(min(float(block["confidence"].get("score", 1.0)), float(result["confidence"])), 3)
            block["confidence"].setdefault("reasons", []).append(f"{result['provider']}_urdu_vision")
            if replace_text:
                block["text"] = result["text"]
                block["markdown"] = result["text"]
            accepted += 1
    for page in prediction.get("pages", []):
        page["markdown"] = SpecterParser._page_markdown(page.get("blocks", []))
        cursor = 0
        for block in page.get("blocks", []):
            value = block.get("markdown", "")
            start = page["markdown"].find(value, cursor) if value else cursor
            start = cursor if start < 0 else start
            block["start_index"], block["end_index"] = start, start + len(value)
            cursor = block["end_index"]
    prediction["markdown"] = "\n\n---\n\n".join(page["markdown"] for page in prediction.get("pages", []) if page.get("markdown"))
    prediction.setdefault("document", {}).setdefault("stats", {})["vision_blocks"] = accepted
    prediction["document"]["urdu_vision"] = {"accepted_blocks": accepted, "provider_order": ["gemini", "openrouter_free", "llamaparse_fast"]}
    prediction_path.write_text(json.dumps(prediction, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / f"{prediction_path.stem}.md").write_text(prediction["markdown"], encoding="utf-8")
    return prediction_path


def run(pdf_path: Path, prediction_path: Path, output_dir: Path, mode: str = "auto", batch_size: int = 10, allow_llamaparse: bool = False, replace_text: bool = True, fresh: bool = False) -> dict[str, Any]:
    load_dotenv()
    manifest_path = prepare_queue(pdf_path, prediction_path, output_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if fresh:
        for task in manifest["tasks"]:
            task["status"] = "pending"
    results = []
    quota_dir = manifest_path.parent
    providers = []
    provider_errors = []
    if mode in {"auto", "gemini"}:
        providers.append(GeminiVision(quota_dir))
    if mode in {"auto", "openrouter"}:
        providers.append(OpenRouterVision(quota_dir))
    if mode == "auto" and allow_llamaparse:
        providers.append(LlamaParseVision(quota_dir))
    pending = [task for task in manifest["tasks"] if task.get("status") != "accepted"]
    for provider in providers:
        if not pending:
            break
        next_pending = []
        for start in range(0, len(pending), max(1, batch_size if provider.name != "llamaparse" else 1)):
            batch = pending[start:start + (batch_size if provider.name != "llamaparse" else 1)]
            try:
                batch_results = provider.complete(batch)
            except ProviderLimit as exc:
                provider_errors.append({"provider": provider.name, "error": str(exc)})
                next_pending.extend(batch)
                next_pending.extend(pending[start + len(batch):])
                break
            results.extend(batch_results)
            accepted_ids = {item["id"] for item in batch_results}
            for task in batch:
                task["status"] = "accepted" if task["id"] in accepted_ids else "pending"
        pending = next_pending
    _apply_results(prediction_path, output_dir, manifest, results, replace_text=replace_text)
    manifest["tasks"] = [{**task, "status": "accepted" if task["id"] in {item["id"] for item in results} else task.get("status", "pending")} for task in manifest["tasks"]]
    manifest["last_run"] = datetime.now(timezone.utc).isoformat()
    manifest["results"] = results
    manifest["provider_errors"] = provider_errors
    manifest["unresolved"] = [task["id"] for task in manifest["tasks"] if task["status"] != "accepted"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": str(manifest_path), "accepted": len(results), "unresolved": manifest["unresolved"], "providers_attempted": [provider.name for provider in providers], "provider_errors": provider_errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe only Urdu PDF crops through budgeted vision providers.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("prediction", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/digital"))
    parser.add_argument("--mode", choices=["auto", "gemini", "openrouter"], default="auto")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--allow-llamaparse", action="store_true", help="Permit the final fast-tier LlamaParse fallback for unresolved crops.")
    parser.add_argument("--keep-native", action="store_true", help="Store vision text under rtl.vision without replacing block.text.")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.pdf, args.prediction, args.output_dir, args.mode, args.batch_size, args.allow_llamaparse, not args.keep_native, args.fresh), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
