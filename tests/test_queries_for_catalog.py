import asyncio

from oshisha.llm import count_catalog_llm_slots, queries_for_catalog


def test_count_catalog_llm_slots_skips_known_flavor(monkeypatch):
    monkeypatch.setattr(
        "oshisha.llm._query_skips_llm_normalize",
        lambda q: q.strip().lower() == "малина",
    )
    assert count_catalog_llm_slots(["малина", "что-то сладкое"]) == 1


def test_queries_for_catalog_parallel_no_llm_when_no_slot(monkeypatch):
    calls = []

    async def fake_normalize(q: str) -> str:
        calls.append(q)
        return f"norm-{q}"

    monkeypatch.setattr("oshisha.llm.normalize_query", fake_normalize)
    monkeypatch.setattr(
        "oshisha.llm._query_skips_llm_normalize",
        lambda q: False,
    )

    out = asyncio.run(
        queries_for_catalog(
            ["a", "b"],
            try_llm_slot=lambda: False,
        )
    )
    assert out == ["a", "b"]
    assert calls == []
