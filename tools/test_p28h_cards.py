from tools import p28h_cards as c


def _row(i, **kw):
    r = {"mint": f"m{i}", "block": "b", "status": "ok", "host_class": "ipfs_cid", "s0c_json_ok": True,
         "name": f"n{i}", "symbol": "S", "description": "d" * 700, "twitter": "https://x.com/abc", "telegram": "",
         "website": "https://www.site.io/a/b"}
    r.update(kw)
    return r


def test_cards_are_label_blind_and_capped():
    rows = [_row(i) for i in range(80)] + [_row(99, status="http_404"), _row(98, s0c_json_ok=False)]
    cards, mp = c.build(rows, 50)
    assert len(cards) == 50 and len(mp) == 50
    assert set(cards[0]) == {"id", "name", "symbol", "description", "twitter", "telegram", "website"}
    assert len(cards[0]["description"]) == 600
    assert cards[0]["twitter"] == "x.com /<1 segment>" and cards[0]["website"] == "site.io /<2 segments>"
    assert "m99" not in mp.values() and "m98" not in mp.values()
    assert "block" not in str(cards) and "m1" not in str(cards)
