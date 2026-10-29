"""
Attachment reader: decides how to read each file and returns one normalised result.

    image  -> sidecar JSON | Llama vision (auto-rotation) | tesseract OCR + text parser
    pdf    -> sidecar JSON | pdf tables/text + parser | Llama text | page render -> vision/OCR
    audio  -> sidecar transcript | Whisper (faster-whisper / openai-whisper)

Sidecars (``GT-1187.jpg.json``, ``voice.opus.txt``) are treated as a cached
extraction, not as ground truth: KHATA_IGNORE_SIDECARS=1 forces the real
readers. Results are cached by file hash under <state>/cache/docs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from harness.agents.llama_client import LlamaClient, wrap
from harness.agents.vision_agent import VisionAgent, score_reading
from harness.ingest import media
from harness.ingest.normalize import guess_doc_type, normalize_doc
from harness.ingest.text_parsers import classify_text, parse_text_document

logger = logging.getLogger(__name__)
EXTRACTOR_VERSION = "3"


@dataclass
class DocResult:
    doc_type: str                       # bill | upi_payment | statement | rate_list | transcript | other
    data: dict = field(default_factory=dict)
    method: str = ""
    confidence: float = 0.0
    text: str = ""                      # raw text / transcript
    file_hash: str = ""
    rotation: int = 0
    readable: bool = True
    note: str = ""


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecar(path: Path, suffixes: tuple[str, ...]) -> Optional[Path]:
    for suf in suffixes:
        for cand in (path.with_name(path.name + suf), path.with_suffix(suf)):
            if cand.exists() and cand != path:
                return cand
    return None


def _is_real_image(path: Path) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


class DocumentReader:
    def __init__(self, llama: LlamaClient, cache_dir: Path, ignore_sidecars: bool = False,
                 whisper_model: str = "small"):
        self.llama = llama
        self.vision = VisionAgent(llama)
        self.cache_dir = Path(cache_dir) / "docs"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ignore_sidecars = ignore_sidecars
        self.whisper_model = whisper_model

    # ── cache ────────────────────────────────────────────────
    def _cache_load(self, key: str) -> Optional[DocResult]:
        p = self.cache_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return DocResult(**d)
        except Exception:
            return None

    def _cache_save(self, key: str, res: DocResult) -> None:
        def default(o):
            return o.isoformat() if hasattr(o, "isoformat") else str(o)
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps(res.__dict__, default=default, ensure_ascii=False), encoding="utf-8")

    # ── entry point ──────────────────────────────────────────
    def read(self, path: Path, kind: str, hint: str = "", ref_year: Optional[int] = None) -> DocResult:
        fh = file_hash(path)
        if not self.ignore_sidecars:
            res = self._from_sidecar(path, kind, ref_year)
            if res:
                res.file_hash = fh
                return res
        key = f"{fh[:32]}-{EXTRACTOR_VERSION}-{'llm' if self.llama.available else 'local'}"
        cached = self._cache_load(key)
        if cached:
            return self._revive(cached, ref_year)
        if kind == "image":
            res = self._read_image(path, hint, ref_year)
        elif kind == "pdf":
            res = self._read_pdf(path, hint, ref_year)
        elif kind == "audio":
            res = self._read_audio(path)
        else:
            res = DocResult("other", readable=False, note="unsupported file type")
        res.file_hash = fh
        if res.readable:
            self._cache_save(key, res)
        return self._revive(res, ref_year)

    @staticmethod
    def _revive(res: DocResult, ref_year) -> DocResult:
        """Cached JSON stores dates as strings; normalise again so types are right."""
        if res.doc_type in ("bill", "upi_payment", "statement", "rate_list") and res.data:
            data = dict(res.data)
            if res.doc_type == "bill" and "lines" in data and "items" not in data:
                data["items"] = data["lines"]
            if res.doc_type == "upi_payment" and "date" in data and "payment_date" not in data:
                data["payment_date"] = data["date"]
            res.data = normalize_doc(res.doc_type, data, ref_year)
        return res

    # ── sidecars ─────────────────────────────────────────────
    def _from_sidecar(self, path: Path, kind: str, ref_year) -> Optional[DocResult]:
        if kind == "audio":
            sc = _sidecar(path, (".txt",))
            if sc:
                text = sc.read_text(encoding="utf-8").strip()
                return DocResult("transcript", {"text": text}, method="sidecar_transcript",
                                 confidence=0.9, text=text)
            return None
        sc = _sidecar(path, (".json",))
        if not sc:
            return None
        try:
            raw = json.loads(sc.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Bad sidecar {sc.name}: {e}")
            return None
        doc_type = guess_doc_type(raw)
        if kind == "pdf" and doc_type == "other":
            doc_type = "rate_list"
        data = normalize_doc(doc_type, raw, ref_year)
        conf = data.get("confidence") or raw.get("ocr_confidence") or raw.get("confidence") or 0.9
        return DocResult(doc_type, data, method="sidecar", confidence=float(conf),
                         text=str(raw.get("raw_text", "")))

    # ── images ───────────────────────────────────────────────
    def _read_image(self, path: Path, hint: str, ref_year) -> DocResult:
        if not _is_real_image(path):
            logger.warning(f"{path.name} is not a readable image")
            return DocResult("other", readable=False, note="file is not a readable image")
        rot = media.detect_rotation(path)
        vision = self.vision.read(path, hint=hint, ref_year=ref_year, first_rotation=rot) if self.llama.available else None
        ocr = None
        if vision is None or vision["score"] < 0.75:
            text, conf, used_rot = media.ocr_image(path, rotate=rot)
            if text.strip():
                dtype, raw = parse_text_document(text, path.name, ref_year)
                data = normalize_doc(dtype, raw, ref_year) if dtype != "other" else raw
                s = score_reading(dtype, data, conf)
                ocr = {"doc_type": dtype, "data": data, "score": s, "rotation": used_rot,
                       "confidence": round(min(0.85, 0.3 + 0.5 * s * max(conf, 0.4)), 3), "method": "tesseract_ocr",
                       "text": text}
        best = None
        for cand in (vision, ocr):
            if cand and (best is None or cand["score"] > best["score"]):
                best = cand
        if not best:
            return DocResult("other", readable=False, note="no vision model and no OCR result")
        return DocResult(best["doc_type"], best["data"], method=best["method"], confidence=best["confidence"],
                         text=best.get("text", best["data"].get("raw_text", "") if isinstance(best["data"], dict) else ""),
                         rotation=best.get("rotation", 0))

    # ── pdfs ─────────────────────────────────────────────────
    def _read_pdf(self, path: Path, hint: str, ref_year) -> DocResult:
        text, tables = media.pdf_text_and_tables(path)
        if text.strip():
            dtype, raw = parse_text_document(text, path.name, ref_year, tables=tables)
            if dtype == "other":
                dtype = classify_text(text, path.name)
            data = normalize_doc(dtype, raw, ref_year) if dtype != "other" else raw
            ok = (dtype == "rate_list" and data.get("entries")) or (dtype == "bill" and data.get("lines")) or \
                 (dtype == "statement" and data.get("entries")) or (dtype == "upi_payment" and data.get("amount"))
            if ok and (dtype != "rate_list" or data.get("effective_from") or not self.llama.available):
                method = "pdf_table" if tables else "pdf_text"
                return DocResult(dtype, data, method=method, confidence=0.95 if tables else 0.9, text=text)
            model = self._llm_text_extract(text, dtype)
            if model:
                return model
            if ok:
                return DocResult(dtype, data, method="pdf_text", confidence=0.85, text=text)
        # scanned PDF
        pages = media.render_pdf_pages(path)
        try:
            for page in pages[:1]:
                res = self._read_image(page, hint, ref_year)
                if res.readable:
                    res.method = "pdf_scan_" + res.method
                    return res
        finally:
            for p in pages:
                p.unlink(missing_ok=True)
        return DocResult("other", readable=False, note="PDF has no readable text")

    def _llm_text_extract(self, text: str, dtype: str) -> Optional[DocResult]:
        if not self.llama.available:
            return None
        from harness.agents.vision_agent import PROMPT
        prompt = ("The following is the text layer of a PDF shared in a kirana shop's WhatsApp chat. "
                  "Use the same JSON schema as for images.\n" + PROMPT + "\nDocument text:\n" + wrap(text[:6000]))
        raw = self.llama.chat_json(prompt, max_tokens=3000)
        if not raw:
            return None
        doc_type = raw.get("doc_type") or dtype
        block = raw.get(doc_type) if isinstance(raw.get(doc_type), dict) else raw
        data = normalize_doc(doc_type, block)
        # anti-hallucination: every rate must be printed in the text
        if doc_type == "rate_list":
            data["entries"] = [e for e in data.get("entries", []) if _printed(e["rate"], text)]
            if not data["entries"]:
                return None
        return DocResult(doc_type, data, method="llama_text", confidence=0.85, text=text)

    # ── audio ────────────────────────────────────────────────
    def _read_audio(self, path: Path) -> DocResult:
        res = media.transcribe(path, self.whisper_model)
        if not res["text"]:
            return DocResult("transcript", {"text": ""}, method=res["method"], confidence=0.0, readable=False,
                             note="speech-to-text unavailable or silent audio")
        return DocResult("transcript", {"text": res["text"], "language": res["language"]}, method=res["method"],
                         confidence=res["confidence"], text=res["text"])


def _printed(value: float, text: str) -> bool:
    from harness.textutil import find_numbers, clean_text
    return any(abs(value - n) < 0.01 for n in find_numbers(clean_text(text)))
