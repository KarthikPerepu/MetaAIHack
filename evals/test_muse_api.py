"""Meta Model API wiring, checked against a local fake server (the test suite never calls the real API).

* Muse Voice Transcribe: the request follows Meta's cookbook (POST /asr/transcribe, Bearer auth,
  multipart with a JSON "request" part and a WAV "audio" part) and WhatsApp .opus is converted.
* Muse Spark: reasoning_effort is sent, and unsupported options are dropped instead of failing.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from harness.config import LlamaConfig
from harness.ingest import media

ROOT = Path(__file__).resolve().parent.parent
OPUS = ROOT / "data" / "exam" / "voice_note_kashi.opus"


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                              "ctype": self.headers.get("Content-Type"), "body": body})
        out = json.dumps({"sessionId": "s1", "transcript": "Laxmi Stores ji 50 bag atta par 2 bag free scheme hai.",
                          "audioDurationMs": 3000}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_meta():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Handler.seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_opus_converts_to_16k_wav():
    wav = media.to_wav_16k(OPUS)
    assert wav and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and len(wav) > 10000


def test_muse_voice_transcribe_request_shape(fake_meta, monkeypatch):
    cfg = LlamaConfig(api_key="test-key", base_url=fake_meta)
    monkeypatch.setattr(LlamaConfig, "is_meta_model_api", property(lambda self: True))
    res = media.transcribe(OPUS, cfg=cfg)
    assert res["text"].startswith("Laxmi Stores ji 50 bag") and res["method"] == "meta:muse-voice-transcribe-1.0"
    req = _Handler.seen[0]
    assert req["path"] == "/v1/asr/transcribe" and req["auth"] == "Bearer test-key"
    assert req["ctype"].startswith("multipart/form-data; boundary=")
    body = req["body"]
    assert b'name="request"' in body and b'"model": "muse-voice-transcribe-1.0"' in body
    assert b'"audioEncoding": "WAV"' in body and b'name="audio"; filename="voice.wav"' in body and b"RIFF" in body


def test_voice_falls_back_when_not_on_meta_api():
    cfg = LlamaConfig(api_key="k", base_url="https://api.llama.com/compat/v1")
    assert media.muse_transcribe(OPUS, cfg) is None
    assert media.muse_transcribe(OPUS, LlamaConfig(api_key="", base_url="https://api.meta.ai/v1")) is None


def test_muse_spark_params_and_graceful_fallbacks():
    from harness.agents.llama_client import LlamaClient

    calls = []

    class Comp:
        def create(self, **kw):
            calls.append({k: v for k, v in kw.items() if k != "messages"})
            if "reasoning_effort" in kw:
                raise Exception("Unknown parameter: reasoning_effort")

            class R:
                class C:
                    class M:
                        content = '{"ok": true}'
                    message = M()
                choices = [C()]
            return R()

    class SDK:
        class chat:
            completions = Comp()

    c = LlamaClient(LlamaConfig(api_key="k"))
    c._client = SDK()
    assert c.chat_json("x") == {"ok": True}
    assert calls[0]["model"] == "muse-spark-1.3" and calls[0]["reasoning_effort"] == "low"
    assert "max_completion_tokens" in calls[0] and "reasoning_effort" not in calls[1]
