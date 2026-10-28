"""The language layer: registry, catalogs, ICU messages, detection, switching and numbers."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from chatfront.core.i18n import load_catalogs, t
from chatfront.core.icu import Arg, Choice, MessageSyntaxError, Node, format_message, parse
from chatfront.core.langdetect import load_detector, script_of
from chatfront.core.languages import Status, live_pass_rates, load_registry, mutually_readable
from chatfront.core.numbers import find_numbers, format_number, latin_digits, parse_number
from chatfront.core.settings import get_settings

I18N = get_settings().i18n_dir
TIER_1 = ["en", "ur", "ur-Latn", "pa-Arab", "ps", "sd", "hi", "hi-Latn", "bn", "bn-Latn"]
TIER_1 += ["ar", "afb", "arz", "ar-Latn"]
PK = ("ur-Latn", "ur", "en", "pa-Arab", "ps", "sd", "skr")


def _arguments(nodes: tuple[Node, ...]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, Arg):
            names.add(node.name)
        elif isinstance(node, Choice):
            names.add(node.name)
            for branch in node.options.values():
                names |= _arguments(branch)
    return names


class TestRegistry:
    def test_tier_one_is_complete(self) -> None:
        registry = load_registry()
        assert [language.tag for language in registry.by_tier(1)] == TIER_1
        assert len(registry.by_tier(2)) == 28

    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("UR-latn", "ur-Latn"),
            ("ar-EG", "arz"),
            ("ar-SA", "afb"),
            ("ar-JO", "ar"),
            ("en-US", "en"),
            ("pa-PK", "pa-Arab"),
            ("bn_BD", "bn"),
            ("tl", "fil"),
            ("es-MX", "es-419"),
            ("xx", None),
            (None, None),
        ],
    )
    def test_resolve(self, tag: str | None, expected: str | None) -> None:
        found = load_registry().resolve(tag)
        assert (found.tag if found else None) == expected

    def test_fallback_chains_end_in_english_and_ui_languages_have_catalogs(self) -> None:
        registry = load_registry()
        assert [lang.tag for lang in registry.chain("pa-Arab")] == ["pa-Arab", "ur", "en"]
        assert [lang.tag for lang in registry.chain("nope")] == ["en"]
        assert registry.ui_language("hi-Latn").tag == "en"
        assert registry.ui_language("arz").tag == "ar"
        assert registry.ui_language("ta").tag == "en"
        with pytest.raises(KeyError):
            registry.get("klingon")

    def test_right_to_left_and_fonts(self) -> None:
        registry = load_registry()
        rtl = {lang.tag for lang in registry.languages.values() if lang.is_rtl}
        assert {"ur", "pa-Arab", "ps", "sd", "ar", "afb", "arz", "skr"} == rtl
        assert registry.get("ur").font == "nastaliq"
        fonts = json.loads((I18N / "languages.json").read_text(encoding="utf-8"))["fonts"]
        assert fonts["nastaliq"] == "Noto Nastaliq Urdu"
        for language in registry.languages.values():
            assert fonts[language.font] == "Anek Latin" or fonts[language.font].startswith("Noto")

    def test_status_comes_only_from_live_evaluations(self) -> None:
        results = {
            "runs": {
                "ur": {"mock": {"pass_rate": 1.0}, "live": {"pass_rate": 0.95}},
                "ps": {"mock": {"pass_rate": 1.0}},
                "sd": {"live": {"pass_rate": 0.7}},
            }
        }
        rates = live_pass_rates(results)
        assert rates == {"ur": 0.95, "sd": 0.7}
        registry = load_registry()
        live = type(registry)(
            registry.languages, registry.aliases, registry.source, registry.pass_mark, rates
        )
        assert live.status("ur") is Status.SUPPORTED
        assert live.status("ps") is Status.BETA
        assert live.status("sd") is Status.BETA
        assert live.status("ta") is Status.STRETCH

    def test_mutually_readable(self) -> None:
        assert mutually_readable("afb", "ar")
        assert mutually_readable("ur-Latn", "hi-Latn")
        assert not mutually_readable("ur", "ur-Latn")
        assert not mutually_readable("pa-Arab", "ur")


class TestCatalogs:
    def test_every_ui_language_has_every_key_and_the_same_arguments(self) -> None:
        catalogs = load_catalogs()
        registry = load_registry()
        assert set(catalogs) == {tag for tag, lang in registry.languages.items() if lang.ui}
        source = catalogs["en"]
        for tag, catalog in catalogs.items():
            assert list(catalog) == list(source), tag
            for key, text in catalog.items():
                assert _arguments(parse(text)) == _arguments(parse(source[key])), (tag, key)

    def test_every_key_the_code_names_is_in_the_catalogs(self) -> None:
        """A missing key fails a job at run time, so look for one here: each t("...") key,
        and each payment signal, whose words the owner reads."""
        source = load_catalogs()["en"]
        code = Path(__file__).parents[2] / "src" / "chatfront"
        named: set[str] = set()
        for file in code.rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            named |= set(re.findall(r'\bt\(\s*"([a-z_][a-z0-9_.]+)"', text))
            if file.parent.name == "payments":
                found = re.findall(r'Signal\(\s*"([a-z_]+)"', text)
                named |= {f"payment.signal.{signal}" for signal in found}
        assert sorted(named - set(source)) == []

    def test_model_drafted_catalogs_are_marked_for_native_review(self) -> None:
        review = json.loads((I18N / "review.json").read_text(encoding="utf-8"))["catalogs"]
        assert set(review) == set(load_catalogs())
        assert review["en"]["status"] == "source"
        for tag, entry in review.items():
            assert entry["status"] in {"source", "needs-native-review", "reviewed"}
            if entry["status"] == "reviewed":
                assert entry.get("by"), tag
                assert entry.get("date"), tag

    def test_attribution_stays_in_every_catalog(self) -> None:
        for catalog in load_catalogs().values():
            assert catalog["app.attribution"] == "Built with Muse Spark Open"

    def test_translate_with_fallback_and_plurals(self) -> None:
        assert t("chat.title", "en", shop="Zara Lawn") == "Try it: chat with Zara Lawn"
        assert t("chat.send", "ur-Latn") == "Bhejein"
        assert t("chat.send", "ps") == "ولېږئ"
        assert t("chat.send", "hi-Latn") == "Send"  # Hinglish readers get English
        assert t("chat.send", "afb") == "إرسال"  # Gulf readers get Modern Standard Arabic
        assert t("wake.seconds", "en", s=1) == "about 1 second"
        assert t("wake.seconds", "ar", s=2) == "نحو ثانيتين"
        assert t("wake.seconds", "bn", s=12) == "প্রায় ১২ সেকেন্ড"
        with pytest.raises(KeyError):
            t("no.such.key")


class TestIcu:
    def test_shared_cases_match_the_web_app(self) -> None:
        cases = json.loads((I18N / "icu-cases.json").read_text(encoding="utf-8"))["cases"]
        registry = load_registry()
        assert len(cases) >= 30
        for case in cases:
            language = registry.get(case["lang"])
            assert format_message(case["message"], language, case["values"]) == case["expected"]

    @pytest.mark.parametrize(
        "broken",
        ["{n, plural, one {x}}", "{n, ordinal}", "oops }", "{n, plural, other {x}", "{ }"],
    )
    def test_syntax_errors(self, broken: str) -> None:
        with pytest.raises(MessageSyntaxError):
            parse(broken)

    def test_number_arguments_must_be_numbers(self) -> None:
        with pytest.raises(TypeError):
            format_message("{n, number}", load_registry().get("en"), {"n": 1.5})


class TestDetection:
    @pytest.mark.parametrize(
        ("text", "tag"),
        [
            ("How much is this suit?", "en"),
            ("Is this in stock?", "en"),
            ("یہ سوٹ کتنے کا ہے؟", "ur"),
            ("bhai yeh suit kitne ka hai", "ur-Latn"),
            ("price kya hai?", "ur-Latn"),
            ("ایہ سوٹ کنے دا اے؟", "pa-Arab"),
            # Not in the evaluation set: checks the Punjabi words added after it found misses.
            ("تسیں کتھوں آرڈر کردے او؟", "pa-Arab"),
            ("ساڈے کول نواں مال آیا اے", "pa-Arab"),
            ("ایہ کدوں تک مل جاوے گا؟", "pa-Arab"),
            ("آپ کے پاس نیا مال آیا ہے؟", "ur"),
            ("یہ کب تک مل جائے گا؟", "ur"),
            ("مجھے نیلا رنگ چاہیے", "ur"),
            ("دا سوټ په څو دی؟", "ps"),
            ("دوپٹہ 1800 میں دے دیں", "ur"),  # de dein: Urdu, though "de" is also Punjabi
            ("هي سوٽ ڪيتري جو آهي؟", "sd"),
            ("यह सूट कितने का है?", "hi"),
            ("bhaiya ye kitne ka hai, namaste", "hi-Latn"),
            ("এই স্যুটটার দাম কত?", "bn"),
            ("vai eta koto taka? ami 2ta nibo", "bn-Latn"),
            ("كم سعر هذا الطقم؟", "ar"),
            ("ابي اعرف السعر شلون", "afb"),
            ("عايز اعرف السعر بكام", "arz"),
            ("kifak, 3andak hayda bil a7mar?", "ar-Latn"),
            ("Magkano po ito? Available pa ba?", "fil"),
            ("Abeg how much be dis one? E dey?", "pcm"),
            ("Bei gani hii?", "sw"),
            ("இந்த சேலை என்ன விலை?", "ta"),
        ],
    )
    def test_language_and_script(self, text: str, tag: str) -> None:
        found = load_detector().detect(text, likely=PK)
        assert found is not None
        assert found.tag == tag
        assert found.script == load_registry().get(tag).script

    def test_thin_evidence_follows_the_conversation_then_the_country(self) -> None:
        detector = load_detector()
        greeting = detector.detect("Salam!", likely=PK)
        assert greeting is not None
        assert (greeting.tag, greeting.confident) == ("ur-Latn", False)
        kept = detector.detect("ok", previous="en", likely=PK)
        assert kept is not None
        assert kept.tag == "en"
        arabic = detector.detect("سلام", likely=("afb", "ar", "en"))
        assert arabic is not None
        assert arabic.tag == "afb"
        assert detector.detect("👍 4500") is None

    def test_brackets_links_and_mentions_are_ignored(self) -> None:
        found = load_detector().detect("[mock reply] یہ سوٹ کتنے کا ہے؟ https://example.com/a")
        assert found is not None
        assert found.tag == "ur"
        assert script_of("a") == "Latn"
        assert script_of("1") is None

    def test_order_codes_are_not_arabizi(self) -> None:
        found = load_detector().detect("Please cancel my order 2SQVTJ", likely=PK)
        assert found is not None
        assert found.tag == "en"

    @pytest.mark.parametrize(
        ("text", "tag"),
        [
            ("English mein baat karo", "en"),
            ("speak English please", "en"),
            ("in English please", "en"),
            ("Urdu please", "ur"),
            ("Urdu mein likho yar", "ur-Latn"),
            ("urdu script mein likho", "ur"),
            ("اردو میں بات کریں", "ur"),
            ("reply in Bangla", "bn"),
            ("Bangla te kotha bolun", "bn-Latn"),
            ("বাংলায় বলুন", "bn"),
            ("Hindi me bolo", "hi-Latn"),
            ("Punjabi vich gal karo", "pa-Arab"),
            ("عربي لو سمحت", "ar"),
            ("Is this available in English size?", None),
            ("Do you have English novels?", None),
            ("Mujhe English wala design chahiye jo pichle hafte aap ne dikhaya tha", None),
        ],
    )
    def test_switch_requests(self, text: str, tag: str | None) -> None:
        assert load_detector().requested_language(text) == tag


class TestNumbers:
    @pytest.mark.parametrize(
        ("text", "language", "expected"),
        [
            ("4500", None, ["4500"]),
            ("٤٥٠٠", None, ["4500"]),
            ("۴۵۰۰", None, ["4500"]),
            ("৪৫০০", None, ["4500"]),
            ("४५००", None, ["4500"]),
            ("Rs. 4,850.50", None, ["4850.50"]),
            ("1,00,000", None, ["100000"]),
            ("١٬٢٣٤٫٥", None, ["1234.5"]),
            ("1.5k", None, ["1500"]),
            ("2 lakh", None, ["200000"]),
            ("2.5 lac", None, ["250000"]),
            ("3cr", None, ["30000000"]),
            ("ek suit 2 3", None, ["2", "3"]),
            ("do sau", "ur-Latn", ["200"]),
            ("dedh hazaar", "ur-Latn", ["1500"]),
            ("dhai sau", "ur-Latn", ["250"]),
            ("sawa sau", "ur-Latn", ["125"]),
            ("paune do sau", "ur-Latn", ["175"]),
            ("saadhe teen hazaar", "ur-Latn", ["3500"]),
            ("do hazaar aur paanch sau", "ur-Latn", ["2500"]),
            ("mujhe de do", "ur-Latn", []),
            ("dedh lakh", "hi-Latn", ["150000"]),
            ("دو سو", "ur", ["200"]),
            ("ڈیڑھ ہزار", "ur", ["1500"]),
            ("ساڑھے تین ہزار", "ur", ["3500"]),
            ("مجھے دے دو", "ur", []),
            ("ڈیڈھ ہزار", "pa-Arab", ["1500"]),
            ("تہانوں دس دیاں گے", "pa-Arab", []),  # dass: tell
            ("دس ہزار", "pa-Arab", ["10000"]),
            ("ڏيڍ هزار", "sd", ["1500"]),
            ("دوه زره", "ps", ["2000"]),
            ("डेढ़ हज़ार", "hi", ["1500"]),
            ("ढाई सौ", "hi", ["250"]),
            ("দেড় হাজার", "bn", ["1500"]),
            ("আড়াই শো", "bn", ["250"]),
            ("der hajar", "bn-Latn", ["1500"]),
            ("ألف ونص", "ar", ["1500"]),
            ("خمسة آلاف", "afb", ["5000"]),
            ("ألفين وخمسمية", "arz", ["2500"]),
            ("alf w nos", "ar-Latn", ["1500"]),
            ("two thousand five hundred", "en", ["2500"]),
            ("I want this one", "en", []),
            ("a thousand", "en", ["1000"]),
        ],
    )
    def test_reads_every_form(self, text: str, language: str | None, expected: list[str]) -> None:
        assert find_numbers(text, language) == [Decimal(value) for value in expected]

    def test_comma_decimals(self) -> None:
        assert parse_number("1.234,50", decimal=",") == Decimal("1234.50")
        assert parse_number("Rp 1.234.567", "id") == Decimal(1234567)
        assert parse_number("nothing here") is None

    def test_digits(self) -> None:
        assert latin_digits("٤٥٠٠ ۴۵ ৪৫ ४५") == "4500 45 45 45"
        registry = load_registry()
        assert format_number(1234567, registry.get("bn")) == "১২,৩৪,৫৬৭"
        assert format_number(1234567, registry.get("ar")) == "١٬٢٣٤٬٥٦٧"
        assert format_number(1234567, registry.get("hi")) == "12,34,567"
        assert format_number(1234567, registry.get("ur")) == "1,234,567"


def test_hint_files_name_only_known_languages() -> None:
    registry = load_registry()
    hints = json.loads(Path(I18N / "detect.json").read_text(encoding="utf-8"))
    known = set(registry.languages) | {"hindustani-Latn"}
    assert set(hints["words"]) <= known
    assert set(hints["names"]) <= set(registry.languages)
    numbers = json.loads(Path(I18N / "numbers.json").read_text(encoding="utf-8"))
    assert set(numbers["languages"]) <= set(registry.languages)
    assert {tag for tag in TIER_1} <= set(numbers["languages"])
