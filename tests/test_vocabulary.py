"""Тесты словарей брендов/вкусов."""

from __future__ import annotations

import json
from pathlib import Path

from oshisha.vocabulary import Vocabulary, _merge_vocab_dicts, normalize_text


def test_normalize_text_yo():
    assert normalize_text("Ёлка") == "елка"


def test_merge_vocab_manual_overrides_display():
    generated = {"x": {"display": "Gen", "aliases": ["a"]}}
    manual = {"x": {"display": "Manual", "aliases": ["b"]}}
    merged = _merge_vocab_dicts(generated, manual)
    assert merged["x"]["display"] == "Manual"
    assert "a" in merged["x"]["aliases"]
    assert "b" in merged["x"]["aliases"]


def test_match_brand_alias():
    vocab_dir = Path(__file__).resolve().parents[1] / "data" / "vocab"
    vocab = Vocabulary.load(vocab_dir)
    key, rest = vocab.match_brand("бб малина")
    assert key == "blackburn"
    assert vocab.brands["blackburn"].display == "BlackBurn"
    assert "малина" in rest
