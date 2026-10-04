"""Fake local Gemini server: resets the TCP connection N times (-> httpx.ReadError), then answers."""
import asyncio, json, socket, struct, threading
import pytest
from app import llm

def serve(fail_n, log):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5)
    def loop():
        k = 0
        while True:
            try: c, _ = s.accept()
            except OSError: return
            req = c.recv(65536).decode(errors="ignore")
            log.append(req.split("\r\n")[0])  # request line (has ?key=...)
            k += 1
            if k <= fail_n:  # RST instead of response
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)); c.close(); continue
            b = json.dumps({"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}).encode()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(b) + b)
            c.close()
    threading.Thread(target=loop, daemon=True).start()
    return s

@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY")
    async def nosleep(_): pass
    monkeypatch.setattr(llm.asyncio, "sleep", nosleep)

def run(monkeypatch, fail_n):
    log = []; s = serve(fail_n, log)
    monkeypatch.setenv("GEMINI_BASE", f"http://127.0.0.1:{s.getsockname()[1]}")
    try: return asyncio.run(llm.ask("x")), log
    finally: s.close()

def test_retry_then_ok(monkeypatch):
    r, log = run(monkeypatch, 2)  # 2 resets then success
    assert r == {"ok": True} and len(log) == 3
    assert "key=SECRETKEY" in log[0] and "x-goog-api-key" not in log[0]  # key in query

def test_gives_up_after_3_without_leaking_key(monkeypatch):
    with pytest.raises(RuntimeError) as e: run(monkeypatch, 99)
    assert "3 tries" in str(e.value) and "SECRETKEY" not in str(e.value)
