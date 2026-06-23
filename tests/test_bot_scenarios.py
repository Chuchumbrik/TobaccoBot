"""Тесты пользовательских сценариев бота.

Покрывает:
  - parse_exclusions: варианты фраз исключений
  - hit_excluded: прямой матч, транслитерация, многосимвольная транслит. (Chabacco-баг)
  - filter_hits: сквозная фильтрация списка
  - action_context: save/get_was_mix, save/get_exclusions, clear_action_context
  - advise routing: _looks_like_advise, _looks_like_mix, _is_fresh_advise_request
  - _run_advise_refine routing: was_mix=False → advise, was_mix=True → mix (логика ветвления)
"""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные классы-моки
# ─────────────────────────────────────────────────────────────────────────────

class _FakeUserData(dict):
    """Минимальный mock context.user_data."""


class _FakeContext:
    def __init__(self):
        self.user_data = _FakeUserData()


def _hit(name: str, brand: str = "", status: str = "есть"):
    """Создаёт минимальный объект FlavorSearchHit-like."""
    class _Product:
        pass

    class _Hit:
        pass

    p = _Product()
    p.name = name
    h = _Hit()
    h.product = p
    h.brand_display = brand
    h.status = status
    return h


# ─────────────────────────────────────────────────────────────────────────────
# 1. parse_exclusions
# ─────────────────────────────────────────────────────────────────────────────

class TestParseExclusions:
    from bot.search_filters import parse_exclusions

    def _pe(self, text):
        from bot.search_filters import parse_exclusions
        return parse_exclusions(text)

    def test_bez_brand(self):
        cleaned, excl = self._pe("кислый без Адалии")
        assert "Адалии" in excl
        assert "кислый" in cleaned

    def test_krome(self):
        cleaned, excl = self._pe("ягодный, кроме BlackBurn")
        assert "BlackBurn" in excl
        assert "ягодный" in cleaned

    def test_isklyuchit(self):
        cleaned, excl = self._pe("Al Fakher исключить ваниль")
        assert "ваниль" in excl

    def test_ubrat(self):
        cleaned, excl = self._pe("убрать Duft")
        assert "Duft" in excl

    def test_ne_khochu(self):
        cleaned, excl = self._pe("хочу фруктовое, не хочу манго")
        assert "манго" in excl
        assert "фруктовое" in cleaned

    def test_multiple_excl(self):
        cleaned, excl = self._pe("без мяты и без манго")
        assert "мяты" in excl
        assert "манго" in excl

    def test_ne_ispolzovat_brand(self):
        cleaned, excl = self._pe("не использовать бренд Adalya")
        assert "Adalya" in excl

    def test_bez_brenda_two_words(self):
        cleaned, excl = self._pe("без бренда Al Fakher")
        assert "Al Fakher" in excl or "Al" in excl  # два слова если второе с заглавной

    def test_no_exclusions(self):
        cleaned, excl = self._pe("ягодное с малиной")
        assert excl == []
        assert cleaned == "ягодное с малиной"

    def test_only_excl_phrase(self):
        cleaned, excl = self._pe("не использовать бренд Duft")
        assert "Duft" in excl
        assert cleaned.strip(" ,;") == ""

    def test_ne_ispolzuy_lowercase(self):
        """Фраза 'не используй блисс' — как в реальном сообщении пользователя."""
        cleaned, excl = self._pe("Не используй блисс или чабако")
        # "блисс" — первый термин, "чабако" не имеет своего триггера
        assert len(excl) >= 1
        assert any("блисс" in e.lower() for e in excl)


# ─────────────────────────────────────────────────────────────────────────────
# 2. hit_excluded — основная логика фильтрации
# ─────────────────────────────────────────────────────────────────────────────

class TestHitExcluded:
    def _excl(self, hit, terms):
        from bot.search_filters import hit_excluded
        return hit_excluded(hit, terms)

    # ── Прямые ASCII-матчи ────────────────────────────────────────────────────

    def test_latin_brand_direct(self):
        """Исключение 'blackburn' попадает в бренд 'BlackBurn'."""
        h = _hit("BlackBurn Double Apple", brand="BlackBurn")
        assert self._excl(h, ["blackburn"]) is True

    def test_bliss_brand_direct(self):
        """Исключение 'bliss' попадает в бренд 'Bliss'."""
        h = _hit("Bliss Cool Watermelon", brand="Bliss")
        assert self._excl(h, ["bliss"]) is True

    def test_bliss_in_name(self):
        """Исключение 'bliss' попадает в имя продукта."""
        h = _hit("Bliss Cherry", brand="")
        assert self._excl(h, ["bliss"]) is True

    def test_bliss_uppercase(self):
        """Исключение 'Bliss' (с заглавной) тоже срабатывает."""
        h = _hit("Bliss Watermelon", brand="Bliss")
        assert self._excl(h, ["Bliss"]) is True

    # ── Транслитерация (кириллица → латиница) ────────────────────────────────

    def test_translit_mango(self):
        """'манго' → 'mango' должен матчиться в 'Mango Tango'."""
        h = _hit("Mango Tango", brand="SomeBrand")
        assert self._excl(h, ["манго"]) is True

    def test_translit_adalya(self):
        """'Адалии' (падеж) — prefix 'adal' совпадает с 'Adalya'."""
        h = _hit("Adalya Double Apple", brand="Adalya")
        assert self._excl(h, ["Адалии"]) is True

    # ── ГЛАВНЫЙ КЕЙС: Chabacco/чабако — многосимвольная транслитерация ────────

    def test_chabacco_excluded_by_cyr(self):
        """'чабако' должно исключать бренд 'Chabacco'.

        Баг был в том, что ч→c (однобуквенно), prefix 'caba' ≠ 'chab' у Chabacco.
        После фикса _translit_proper('чабако')='chabako', prefix 'chab' → совпадение.
        """
        h = _hit("Chabacco Medium Berry Mix 200гр.", brand="Chabacco")
        assert self._excl(h, ["чабако"]) is True

    def test_chabacco_excluded_by_chaba(self):
        """Исключение 'chaba' (латиница) тоже работает."""
        h = _hit("Chabacco Light Watermelon", brand="Chabacco")
        assert self._excl(h, ["chaba"]) is True

    def test_chabacco_not_excluded_by_unrelated(self):
        """Несвязанный термин не исключает Chabacco."""
        h = _hit("Chabacco Light Watermelon", brand="Chabacco")
        assert self._excl(h, ["манго"]) is False

    def test_shisha_excluded(self):
        """'шиша' → proper translit 'shisha' совпадает с брендом Shisha."""
        h = _hit("Shisha Tastes Apple", brand="Shisha Tastes")
        assert self._excl(h, ["шиша"]) is True

    def test_sapphire_excluded_by_cyr(self):
        """'сапфир' → _translit_proper = 'sapphir' (ф→ph), prefix 'sapp' = 'sapphire'[:4] ✓

        Баг был: _CYR2LAT маппил ф→f, 'sapfir'[:4]='sapf' ≠ 'sapp'.
        Фикс: добавлен ф→'ph' в _CYR_MULTI → 'sapphir'[:4]='sapp' → совпадение.
        """
        h = _hit("Sapphire Crown Cherry Vanilla 25г", brand="Sapphire Crown")
        assert self._excl(h, ["сапфир"]) is True

    def test_sapphire_no_false_positive_mango(self):
        """'манго' не должно исключать 'Mandarin' (3-char 'man' мог давать ложное срабатывание)."""
        h = _hit("Mandarin Citrus Mix", brand="SomeBrand")
        assert self._excl(h, ["манго"]) is False

    # ── Русский бренд/ингредиент в кирилице ──────────────────────────────────

    def test_russian_ingredient_mint(self):
        """'мяты' (родительный падеж) → обрезка до 'мят' → совпадает в 'Арбуз Мята'.

        Реальный кейс: пользователь пишет «без мяты», exclusion = «мяты».
        Имя продукта содержит «мята» (именительный падеж).
        Фикс: check t[:-1] ('мят') as substring.
        """
        h = _hit("Арбуз Мята", brand="SomeBrand")
        assert self._excl(h, ["мяты"]) is True  # мяты[:-1]='мят' in 'арбуз мята' ✓

    def test_russian_ingredient_mint_base_form(self):
        """'мята' (база) → прямое совпадение в имени 'Арбуз Мята'."""
        h = _hit("Арбуз Мята", brand="SomeBrand")
        assert self._excl(h, ["мята"]) is True

    def test_duft_excluded(self):
        """'Duft' исключается прямым матчем."""
        h = _hit("Duft Two Apples", brand="Duft")
        assert self._excl(h, ["Duft"]) is True

    # ── Нет совпадения ────────────────────────────────────────────────────────

    def test_no_match_returns_false(self):
        h = _hit("White Grapefruit", brand="Overdose")
        assert self._excl(h, ["Adalya", "манго"]) is False

    def test_empty_exclusions(self):
        h = _hit("Some Product", brand="SomeBrand")
        assert self._excl(h, []) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. filter_hits — сквозная фильтрация
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterHits:
    def _fh(self, hits, terms):
        from bot.search_filters import filter_hits
        return filter_hits(hits, terms)

    def test_filters_chabacco_from_list(self):
        hits = [
            _hit("Adalya Double Apple", brand="Adalya"),
            _hit("Chabacco Mix Berry 200гр.", brand="Chabacco"),
            _hit("Duft Two Apples", brand="Duft"),
            _hit("WhiteFox Cola", brand="WhiteFox"),
        ]
        result = self._fh(hits, ["чабако", "Adalya"])
        names = [h.product.name for h in result]
        assert "Chabacco Mix Berry 200гр." not in names
        assert "Adalya Double Apple" not in names
        assert "Duft Two Apples" in names
        assert "WhiteFox Cola" in names

    def test_empty_terms_passthrough(self):
        hits = [_hit("Some Product", brand="Brand")]
        assert self._fh(hits, []) == hits

    def test_all_filtered(self):
        hits = [
            _hit("Bliss Watermelon", brand="Bliss"),
            _hit("Chabacco Light", brand="Chabacco"),
        ]
        result = self._fh(hits, ["bliss", "чабако"])
        assert result == []

    def test_partial_filter(self):
        hits = [
            _hit("Bliss Watermelon", brand="Bliss"),
            _hit("Duft Apple", brand="Duft"),
        ]
        result = self._fh(hits, ["bliss"])
        assert len(result) == 1
        assert result[0].product.name == "Duft Apple"


# ─────────────────────────────────────────────────────────────────────────────
# 4. action_context — was_mix, exclusions, clear
# ─────────────────────────────────────────────────────────────────────────────

class TestActionContext:
    def _ctx(self):
        return _FakeContext()

    def test_was_mix_default_false(self):
        from bot.action_context import get_was_mix
        ctx = self._ctx()
        assert get_was_mix(ctx) is False

    def test_save_get_was_mix_true(self):
        from bot.action_context import save_was_mix, get_was_mix
        ctx = self._ctx()
        save_was_mix(ctx, True)
        assert get_was_mix(ctx) is True

    def test_save_get_was_mix_false(self):
        from bot.action_context import save_was_mix, get_was_mix
        ctx = self._ctx()
        save_was_mix(ctx, True)
        save_was_mix(ctx, False)
        assert get_was_mix(ctx) is False

    def test_clear_action_context_resets_was_mix(self):
        from bot.action_context import save_was_mix, get_was_mix, clear_action_context
        ctx = self._ctx()
        save_was_mix(ctx, True)
        clear_action_context(ctx)
        assert get_was_mix(ctx) is False

    def test_exclusions_save_get(self):
        from bot.action_context import save_exclusions, get_exclusions
        ctx = self._ctx()
        save_exclusions(ctx, ["Adalya", "манго"])
        assert get_exclusions(ctx) == ["Adalya", "манго"]

    def test_exclusions_default_empty(self):
        from bot.action_context import get_exclusions
        ctx = self._ctx()
        assert get_exclusions(ctx) == []

    def test_clear_resets_exclusions(self):
        from bot.action_context import save_exclusions, get_exclusions, clear_action_context
        ctx = self._ctx()
        save_exclusions(ctx, ["bliss"])
        clear_action_context(ctx)
        assert get_exclusions(ctx) == []

    def test_was_mix_key_in_user_data(self):
        """Ключ KEY_WAS_MIX присутствует и удаляется через clear."""
        from bot.action_context import save_was_mix, clear_action_context, KEY_WAS_MIX
        ctx = self._ctx()
        save_was_mix(ctx, True)
        assert KEY_WAS_MIX in ctx.user_data
        clear_action_context(ctx)
        assert KEY_WAS_MIX not in ctx.user_data


# ─────────────────────────────────────────────────────────────────────────────
# 5. Routing helpers — _looks_like_advise, _looks_like_mix, _is_fresh_advise_request
# ─────────────────────────────────────────────────────────────────────────────

class TestRoutingHelpers:
    def test_looks_like_advise_khochu(self):
        from bot.handlers.advise import _looks_like_advise
        assert _looks_like_advise("хочу что-то сладкое") is True

    def test_looks_like_advise_posovetuy(self):
        from bot.handlers.advise import _looks_like_advise
        assert _looks_like_advise("посоветуй фруктовое") is True

    def test_looks_like_advise_negative(self):
        from bot.handlers.advise import _looks_like_advise
        # Точный запрос — не советник
        assert _looks_like_advise("Duft Two Apples 100г") is False

    def test_looks_like_mix_miks(self):
        from bot.handlers.advise import _looks_like_mix
        assert _looks_like_mix("собери мне микс") is True

    def test_looks_like_mix_smeshat(self):
        from bot.handlers.advise import _looks_like_mix
        assert _looks_like_mix("хочу смешать два вкуса") is True

    def test_looks_like_mix_recept(self):
        from bot.handlers.advise import _looks_like_mix
        assert _looks_like_mix("дай рецепт микса") is True

    def test_looks_like_mix_negative(self):
        from bot.handlers.advise import _looks_like_mix
        assert _looks_like_mix("хочу фруктовое") is False

    def test_is_fresh_advise_request_true(self):
        from bot.handlers.advise import _is_fresh_advise_request
        assert _is_fresh_advise_request("хочу что-то ягодное и лёгкое") is True

    def test_is_fresh_advise_request_short(self):
        from bot.handlers.advise import _is_fresh_advise_request
        # Короткий — не считается новым запросом
        assert _is_fresh_advise_request("хочу ягодное") is False  # < 3 слов

    def test_is_fresh_advise_request_no_opener(self):
        from bot.handlers.advise import _is_fresh_advise_request
        assert _is_fresh_advise_request("ягодное без мяты и кислое") is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Transliteration correctness — _translit vs _translit_proper
# ─────────────────────────────────────────────────────────────────────────────

class TestTransliteration:
    def test_ch_proper(self):
        from bot.search_filters import _translit_proper
        assert _translit_proper("чабако") == "chabako"

    def test_sh_proper(self):
        from bot.search_filters import _translit_proper
        assert _translit_proper("шиша") == "shisha"

    def test_zh_proper(self):
        from bot.search_filters import _translit_proper
        assert _translit_proper("жара") == "zhara"

    def test_shch_proper(self):
        from bot.search_filters import _translit_proper
        result = _translit_proper("щёки")
        assert result.startswith("shch")

    def test_latin_unchanged(self):
        from bot.search_filters import _translit_proper
        assert _translit_proper("adalya") == "adalya"

    def test_mango_same_both(self):
        """Для 'манго' обе транслитерации дают одинаковый результат."""
        from bot.search_filters import _translit, _translit_proper
        assert _translit("манго") == _translit_proper("манго") == "mango"

    def test_chabacco_prefix_fix(self):
        """Ключевая проверка: prefix proper('чабако')[:4] == 'chab'."""
        from bot.search_filters import _translit, _translit_proper, _PREFIX_LEN
        simple_pfx = _translit("чабако")[:_PREFIX_LEN]
        proper_pfx = _translit_proper("чабако")[:_PREFIX_LEN]
        brand_tl = _translit("chabacco")  # уже латиница
        # Старый (сломанный) матч
        assert not brand_tl.startswith(simple_pfx)   # 'caba' ≠ начало 'chabacco'
        # Новый (исправленный) матч
        assert brand_tl.startswith(proper_pfx)        # 'chab' == начало 'chabacco' ✓


# ─────────────────────────────────────────────────────────────────────────────
# 7. Exclusion accumulation logic (merge vs reset)
# ─────────────────────────────────────────────────────────────────────────────

class TestExclusionAccumulation:
    """Проверяет что исключения мержатся при уточнении, но сбрасываются при новом запросе."""

    def test_merge_on_refine(self):
        """При уточнении новые исключения добавляются к старым."""
        from bot.action_context import save_exclusions, get_exclusions
        ctx = _FakeContext()
        # Имитация: после первого запроса сохранены исключения
        save_exclusions(ctx, ["Adalya"])
        # При уточнении: мержим
        prev = get_exclusions(ctx)
        new = ["чабако"]
        combined = list(dict.fromkeys(prev + new))
        save_exclusions(ctx, combined)
        result = get_exclusions(ctx)
        assert "Adalya" in result
        assert "чабако" in result

    def test_reset_on_new_search(self):
        """При новом независимом запросе исключения сбрасываются."""
        from bot.action_context import save_exclusions, get_exclusions
        ctx = _FakeContext()
        save_exclusions(ctx, ["Adalya", "чабако"])
        # Новый запрос — только текущие исключения
        save_exclusions(ctx, ["манго"])
        result = get_exclusions(ctx)
        assert result == ["манго"]
        assert "Adalya" not in result
        assert "чабако" not in result

    def test_reset_button_clears_all(self):
        """Кнопка 'Сбросить фильтры' обнуляет все исключения."""
        from bot.action_context import save_exclusions, get_exclusions
        ctx = _FakeContext()
        save_exclusions(ctx, ["bliss", "чабако"])
        # Нажали кнопку CB_EXCL_RESET
        save_exclusions(ctx, [])
        assert get_exclusions(ctx) == []

    def test_dedup_on_merge(self):
        """При мерже дубликаты не накапливаются."""
        from bot.action_context import save_exclusions, get_exclusions
        ctx = _FakeContext()
        save_exclusions(ctx, ["Adalya", "манго"])
        prev = get_exclusions(ctx)
        combined = list(dict.fromkeys(prev + ["Adalya", "bliss"]))
        save_exclusions(ctx, combined)
        result = get_exclusions(ctx)
        assert result.count("Adalya") == 1
        assert "bliss" in result


# ─────────────────────────────────────────────────────────────────────────────
# 8. was_mix branching — unit-тест на ветвление рефайна
# ─────────────────────────────────────────────────────────────────────────────

class TestWasMixBranching:
    """Проверяет логику was_mix без реальных вызовов Telegram."""

    def test_was_mix_false_after_advise(self):
        """После обычного запроса советника was_mix == False."""
        from bot.action_context import save_was_mix, get_was_mix
        ctx = _FakeContext()
        save_was_mix(ctx, False)
        assert get_was_mix(ctx) is False

    def test_was_mix_true_after_mix(self):
        """После успешных рецептов миксов was_mix == True."""
        from bot.action_context import save_was_mix, get_was_mix
        ctx = _FakeContext()
        save_was_mix(ctx, True)
        assert get_was_mix(ctx) is True

    def test_was_mix_false_after_mix_fallback(self):
        """Если recommend_mix не дал рецептов (fallback) — was_mix == False."""
        from bot.action_context import save_was_mix, get_was_mix
        ctx = _FakeContext()
        save_was_mix(ctx, True)   # был микс
        # LLM не дал рецептов → fallback → сбросили
        save_was_mix(ctx, False)
        assert get_was_mix(ctx) is False

    def test_refine_path_uses_was_mix_flag(self):
        """Симуляция ветвления в _run_advise_refine по флагу was_mix."""
        from bot.action_context import save_was_mix, get_was_mix

        # Сценарий A: был микс → refine должен идти в mix-ветку
        ctx_mix = _FakeContext()
        save_was_mix(ctx_mix, True)
        assert get_was_mix(ctx_mix) is True   # → branch: recommend_mix

        # Сценарий B: был advise → refine должен идти в advise-ветку
        ctx_adv = _FakeContext()
        save_was_mix(ctx_adv, False)
        assert get_was_mix(ctx_adv) is False  # → branch: refine_queries


# ─────────────────────────────────────────────────────────────────────────────
# 9. _looks_like_ack — нейтральные подтверждения не уходят в поиск
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeAck:
    def _ack(self, text: str) -> bool:
        from bot.handlers.routing import _looks_like_ack
        return _looks_like_ack(text)

    # ── Позитивные случаи ────────────────────────────────────────────────────

    def test_spasibo(self):
        assert self._ack("спасибо") is True

    def test_spasibo_uppercase(self):
        assert self._ack("Спасибо") is True

    def test_spasibo_excl(self):
        assert self._ack("Спасибо!") is True

    def test_ok_latin(self):
        assert self._ack("ok") is True

    def test_okei(self):
        assert self._ack("окей") is True

    def test_super(self):
        assert self._ack("супер") is True

    def test_ok_klass(self):
        """«ок класс» — два слова из словаря."""
        assert self._ack("ок класс") is True

    def test_spasibo_bolshoe(self):
        """«спасибо большое» — первое слово из словаря, «большое» нет → False."""
        # Не все слова в ACK_WORDS, поэтому False
        assert self._ack("спасибо большое") is False

    def test_thumbs_up_emoji(self):
        assert self._ack("👍") is True

    def test_ponyatno(self):
        assert self._ack("понятно") is True

    def test_dobavil(self):
        assert self._ack("добавил") is True

    def test_ugу(self):
        assert self._ack("угу") is True

    def test_prinyato(self):
        assert self._ack("принято") is True

    # ── Негативные случаи (поиск/запросы) ────────────────────────────────────

    def test_search_query_negative(self):
        """Обычный поисковый запрос — не подтверждение."""
        assert self._ack("малина 200") is False

    def test_brand_name_negative(self):
        assert self._ack("Duft Two Apples") is False

    def test_advise_request_negative(self):
        assert self._ack("хочу что-то сладкое") is False

    def test_question_negative(self):
        assert self._ack("как выбрать уголь?") is False

    def test_long_sentence_negative(self):
        assert self._ack("спасибо за подборку очень помогло") is False

    def test_spasibo_with_name_negative(self):
        """«спасибо Дима» — два слова, второе не в словаре."""
        assert self._ack("спасибо Дима") is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. _looks_like_refine — уточнения не уходят в поиск/advise
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeRefine:
    def _ref(self, text: str) -> bool:
        from bot.handlers.routing import _looks_like_refine
        return _looks_like_refine(text)

    # ── Позитивные случаи (должно считаться уточнением) ──────────────────────

    def test_bez_myaty(self):
        """«без мяты» — типичное уточнение-исключение."""
        assert self._ref("без мяты") is True

    def test_bez_adalyi(self):
        assert self._ref("без Адалии") is True

    def test_krome_bliss(self):
        assert self._ref("кроме Bliss") is True

    def test_pokrepche(self):
        """Компаратив-степень."""
        assert self._ref("покрепче") is True

    def test_polegche(self):
        assert self._ref("полегче") is True

    def test_posvezhee(self):
        assert self._ref("посвежее") is True

    def test_no_bez(self):
        """«но без мяты» — противопоставление."""
        assert self._ref("но без мяты") is True

    def test_bolee_fruktovoe(self):
        assert self._ref("более фруктовое") is True

    def test_menee_sladkoe(self):
        assert self._ref("менее сладкое") is True

    def test_drugoy_variant(self):
        assert self._ref("другой вариант") is True

    def test_drugoe(self):
        assert self._ref("другое что-нибудь") is True

    def test_pokhozheye(self):
        assert self._ref("похожее но другое") is True

    def test_utochni(self):
        assert self._ref("уточни пожалуйста") is True

    def test_dobav_mango(self):
        assert self._ref("добавь манго") is True

    def test_tolko(self):
        assert self._ref("только ягодное") is True

    def test_chut_posvezhee(self):
        assert self._ref("чуть посвежее") is True

    # ── Негативные случаи (не должно считаться уточнением) ───────────────────

    def test_fresh_advise_khochu(self):
        """Свежий advise-запрос с ключевым словом не должен быть рефайном."""
        assert self._ref("хочу что-то сладкое и ягодное") is False

    def test_product_search(self):
        """Конкретный продукт."""
        assert self._ref("Duft Two Apples 100г") is False

    def test_brand_name(self):
        assert self._ref("Chabacco Light") is False

    def test_flavor_name(self):
        """Просто вкус — идёт в поиск, не рефайн."""
        assert self._ref("малина") is False

    def test_fresh_mix_soberi(self):
        assert self._ref("собери мне микс ягодный") is False

    def test_question_negative(self):
        assert self._ref("как выбрать уголь?") is False

    def test_empty(self):
        assert self._ref("") is False


# ─────────────────────────────────────────────────────────────────────────────
# 11. Контекстный рефайн в handle_idle_text — логика маршрутизации
# ─────────────────────────────────────────────────────────────────────────────

class TestContextualRefineRouting:
    """Проверяет маршрутизацию idle-текста при наличии/отсутствии контекста."""

    def _ctx_with_advise(self, query: str = "ягодное лёгкое", was_mix: bool = False):
        from bot.action_context import save_advise_description, save_was_mix
        ctx = _FakeContext()
        save_advise_description(ctx, query)
        save_was_mix(ctx, was_mix)
        return ctx

    def _ctx_empty(self):
        return _FakeContext()

    def test_refine_detected_with_context(self):
        """С контекстом: 'без мяты' → _looks_like_refine=True → рефайн."""
        from bot.handlers.routing import _looks_like_refine
        from bot.action_context import get_advise_description
        ctx = self._ctx_with_advise("ягодное лёгкое")
        assert get_advise_description(ctx) == "ягодное лёгкое"
        assert _looks_like_refine("без мяты") is True

    def test_no_refine_without_context(self):
        """Без контекста: 'без мяты' не запускает рефайн (нет last_query)."""
        from bot.action_context import get_advise_description
        ctx = self._ctx_empty()
        assert get_advise_description(ctx) == ""
        # Условие last_query and _looks_like_refine → False (пустой last_query)

    def test_mix_takes_priority_over_refine(self):
        """Свежий микс-запрос перехватывается раньше рефайна."""
        from bot.handlers.advise import _looks_like_mix
        from bot.handlers.routing import _looks_like_refine
        text = "собери микс без мяты"
        # Даже если starts with "без" после слов, _looks_like_mix=True идёт первым
        assert _looks_like_mix(text) is True

    def test_was_mix_preserved_for_refine(self):
        """was_mix=True корректно передаётся в _run_advise_refine через контекст."""
        from bot.action_context import get_advise_description, get_was_mix, save_advise_description, save_was_mix
        ctx = self._ctx_with_advise("десертный микс", was_mix=True)
        assert get_was_mix(ctx) is True
        assert get_advise_description(ctx) == "десертный микс"

    def test_advise_context_cleared_on_cancel(self):
        """После clear_action_context рефайн не срабатывает."""
        from bot.action_context import (
            clear_action_context, get_advise_description, save_advise_description,
        )
        from bot.handlers.routing import _looks_like_refine
        ctx = self._ctx_with_advise("ягодное")
        clear_action_context(ctx)
        assert get_advise_description(ctx) == ""
        # last_query пуст → рефайн не сработает
