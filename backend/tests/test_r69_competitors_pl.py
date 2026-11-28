"""Competitor tracker in Polish: seed text is swapped for its translation; names, quotes, links and anything a person typed stay as they are."""
from ri import competitors as ct


def test_polish_tracker_translates_seed_text_and_keeps_names_quotes_and_counts(conn, monkeypatch):
    ct.load_seed(conn)
    en = ct.tracker(conn)
    c = next(x for x in en["competitors"] if x["tier"] == 1 and x.get("hero", {}).get("value") and x.get("oneLiner"))
    tmpl = next(b for b in conn.execute("select content from competitor_market where name='copy'").fetchone()["content"]["briefing"] if "{" in b["text"])
    pl = {c["oneLiner"]: "Polski opis firmy.", c["name"]: "NIE TŁUMACZ", c["hero"]["value"]: "NIE TŁUMACZ",
          tmpl["text"]: "Szablon: " + " ".join(f"{{{k}}}" for k in __import__("re").findall(r"\{(\w+)\}", tmpl["text"]))}
    monkeypatch.setattr(ct, "_PL", pl)
    out = ct.tracker(conn, lang="pl")
    c2 = next(x for x in out["competitors"] if x["key"] == c["key"])
    assert c2["oneLiner"] == "Polski opis firmy."
    assert c2["name"] == c["name"] and c2["hero"]["value"] == c["hero"]["value"]          # names and verbatim quotes never change
    b = next(x for x in out["briefing"] if x["text"].startswith("Szablon:"))
    assert " z " in b["text"] and " of " not in b["text"]                                   # counts filled in after translation, in Polish
    assert "Zbieżność" in c2["tierWhy"]
    assert ct.tracker(conn, lang="en")["competitors"][0]["oneLiner"] == en["competitors"][0]["oneLiner"]   # English unchanged


def test_dictionary_file_covers_only_strings_and_keeps_placeholders():
    import json
    import re

    import pytest
    if not ct.PL_PATH.exists():
        pytest.skip("Polish dictionary not added yet")
    d = json.loads(ct.PL_PATH.read_text(encoding="utf-8"))
    assert len(d) > 1000
    for en, pl in d.items():
        assert isinstance(pl, str) and pl.strip()
        assert set(re.findall(r"\{\w+\}", en)) == set(re.findall(r"\{\w+\}", pl)), en
