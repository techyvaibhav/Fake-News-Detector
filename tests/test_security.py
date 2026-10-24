import asyncio
import httpx, pytest
from app import fetch, pipeline as pl

HTML = "<html><head><title>GST rollout</title></head><body><article><p>" + "GST was launched on 1 July 2017 across India. " * 20 + "</p></article></body></html>"
PUB = "http://93.184.216.34/a"  # public IP literal (no DNS needed)

def use(monkeypatch, handler): monkeypatch.setattr(fetch, "TRANSPORT", httpx.MockTransport(handler))
def ok_handler(req):
    if req.url.path == "/robots.txt": return httpx.Response(404)
    return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

def test_safe_url_blocks_private_and_odd_schemes():
    for u in ["http://127.0.0.1/", "http://localhost/", "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data",
              "http://192.168.1.1/", "file:///etc/passwd", "ftp://example.com/", "http://[::1]/", "javascript:alert(1)", "", None]:
        assert not fetch.safe_url(u), u
    assert fetch.safe_url(PUB)

def test_fetch_ok_and_extract(monkeypatch):
    use(monkeypatch, ok_handler)
    got = asyncio.run(fetch.fetch(PUB)); assert got and "GST" in got[1]
    pytest.importorskip("trafilatura")
    art = fetch.extract(got[1], got[0]); assert "1 July 2017" in art["text"] and art["host"] == "93.184.216.34"

def test_redirect_to_private_address_is_blocked(monkeypatch):
    def h(req):
        if req.url.path == "/robots.txt": return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    use(monkeypatch, h); assert asyncio.run(fetch.fetch(PUB)) is None

def test_robots_txt_respected(monkeypatch):
    use(monkeypatch, lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /") if req.url.path == "/robots.txt" else httpx.Response(200, text=HTML))
    assert asyncio.run(fetch.fetch(PUB)) is None

def test_non_html_and_errors_return_none(monkeypatch):
    use(monkeypatch, lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    assert asyncio.run(fetch.fetch(PUB)) is None
    use(monkeypatch, lambda req: httpx.Response(500)); assert asyncio.run(fetch.fetch(PUB)) is None

def test_build_input_helpful_errors_and_merge(monkeypatch):
    use(monkeypatch, lambda req: httpx.Response(500))
    _, _, err = asyncio.run(pl.build_input(None, PUB)); assert "Could not read that link" in err
    _, _, err = asyncio.run(pl.build_input("  ", None)); assert "Empty input" in err
    txt, meta, err = asyncio.run(pl.build_input("gst launched 2001", PUB)); assert err is None and txt == "gst launched 2001" and meta is None  # text still works if link fails
    pytest.importorskip("trafilatura"); use(monkeypatch, ok_handler)
    txt, meta, err = asyncio.run(pl.build_input("is this true?", PUB))
    assert err is None and "[Article from 93.184.216.34" in txt and "is this true?" in txt and meta["host"] == "93.184.216.34" and len(txt) <= 2000
