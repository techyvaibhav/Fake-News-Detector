"""Fake local Gemini server: resets TCP N times (-> httpx errors), then answers with `code`."""
import asyncio, json, socket, struct, threading
import pytest
from app import llm

def serve(fail_n, log, code=200):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5)
    def loop():
        k = 0
        while True:
            try: c, _ = s.accept()
            except OSError: return
            log.append(c.recv(65536).decode(errors="ignore").split("\r\n")[0]); k += 1
            if k <= fail_n:
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)); c.close(); continue
            b = json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode()
            c.sendall(b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (code, len(b)) + b)
            c.close()
    threading.Thread(target=loop, daemon=True).start()
    return s

@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY")
    async def nosleep(_): pass
    monkeypatch.setattr(llm.asyncio, "sleep", nosleep)

def run(mp, fail_n, code=200):
    log = []; s = serve(fail_n, log, code)
    mp.setenv("GEMINI_BASE", f"http://127.0.0.1:{s.getsockname()[1]}")
    try: return asyncio.run(llm._post({})), log
    finally: s.close()

def test_retry_then_ok(monkeypatch):
    r, log = run(monkeypatch, 2)
    assert "candidates" in r and len(log) == 3
    assert "key=SECRETKEY" in log[0]  # key in query

def test_gives_up_without_leaking_key(monkeypatch):
    with pytest.raises(llm.LLMError) as e: run(monkeypatch, 99)
    assert "3 tries" in str(e.value) and "SECRETKEY" not in str(e.value)

def test_429_is_quota_error_and_not_retried(monkeypatch):
    log = []; s = serve(0, log, 429); monkeypatch.setenv("GEMINI_BASE", f"http://127.0.0.1:{s.getsockname()[1]}")
    with pytest.raises(llm.LLMError) as e: asyncio.run(llm._post({}))
    assert e.value.kind == "quota" and len(log) == 1  # exactly one request, no retry
    s.close()

def test_missing_key_is_nokey_not_crash(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(llm.LLMError) as e: asyncio.run(llm._post({}))
    assert e.value.kind == "nokey"

def test_why_parses_gemini_quota_error():
    from tests import mocks
    msg, retry = llm.why(mocks.q429())
    assert "RESOURCE_EXHAUSTED" in msg and "limit: 0" in msg and "PerDayPerProjectPerModel" in msg and retry == 30
