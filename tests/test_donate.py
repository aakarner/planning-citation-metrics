"""The donate line appears only once a destination is set."""

from pipeline import build_site


def test_no_button_without_a_url(monkeypatch):
    monkeypatch.setitem(build_site.COPY["site"], "donate_url", "")
    assert build_site.donate_block() == ""


def test_button_links_to_the_configured_url(monkeypatch):
    monkeypatch.setitem(build_site.COPY["site"], "donate_url", "https://example.org/give?a=1&b=2")
    html = build_site.donate_block()
    assert 'href="https://example.org/give?a=1&amp;b=2"' in html
    assert "Chip in" in html and "$100" in html
