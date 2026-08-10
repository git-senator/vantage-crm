"""The parts of listing translation that must work without a model.

Everything here is about refusing bad output rather than producing good output.
A translation service is judged on what it declines to store: a reply with a
feature missing, a reply that is prose, a reply that would overwrite something
a person wrote. Those are the failures that reach a client's screen.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.property import Property
from app.services.ai.base import AIError
from app.services.property_translation import (
    parse_translation,
    source_fingerprint,
    target_locales,
)


def _listing(**overrides: object) -> Property:
    """A listing detached from any session — these tests touch no database."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "title": "Резиденции Oceana у океана",
        "description": "Один из самых эксклюзивных комплексов Бразилии.",
        "features": ["Infinity Pool", "От 182 до 976 м²"],
        "source_locale": "ru",
    }
    defaults.update(overrides)
    listing = Property()
    for key, value in defaults.items():
        setattr(listing, key, value)
    return listing


class TestTargetLocales:
    def test_excludes_the_language_the_listing_is_written_in(self) -> None:
        assert target_locales(_listing(source_locale="ru")) == ["en", "pt-BR"]
        assert target_locales(_listing(source_locale="en")) == ["pt-BR", "ru"]


class TestSourceFingerprint:
    def test_same_text_gives_the_same_hash(self) -> None:
        assert source_fingerprint(_listing()) == source_fingerprint(_listing())

    def test_editing_the_description_changes_it(self) -> None:
        before = source_fingerprint(_listing())
        after = source_fingerprint(_listing(description="Другое описание."))
        assert before != after

    def test_reordering_features_changes_it(self) -> None:
        before = source_fingerprint(_listing())
        after = source_fingerprint(
            _listing(features=["От 182 до 976 м²", "Infinity Pool"])
        )
        assert before != after

    def test_price_and_photos_do_not_affect_it(self) -> None:
        """Only translated text is fingerprinted.

        A price change must not invalidate a good Portuguese description — that
        would spend tokens on every edit and reset human corrections for a
        field the translation never contained.
        """
        cheap = _listing()
        cheap.price = 100
        dear = _listing()
        dear.price = 9_000_000
        dear.cover_attachment_id = uuid.uuid4()
        assert source_fingerprint(cheap) == source_fingerprint(dear)


class TestParseTranslation:
    def test_reads_a_plain_json_object(self) -> None:
        result = parse_translation(
            '{"title": "Oceana Residences", "description": "One of the most '
            'exclusive.", "features": ["Infinity Pool", "From 182 to 976 m²"]}',
            expected_features=2,
        )
        assert result.title == "Oceana Residences"
        assert result.features == ["Infinity Pool", "From 182 to 976 m²"]

    def test_survives_markdown_fences(self) -> None:
        """Models wrap JSON in fences routinely; that is not a failure."""
        result = parse_translation(
            '```json\n{"title": "Oceana", "description": null, "features": []}\n```',
            expected_features=0,
        )
        assert result.title == "Oceana"
        assert result.description is None

    def test_survives_prose_around_the_object(self) -> None:
        result = parse_translation(
            'Here is the translation:\n{"title": "Oceana", "description": "x", '
            '"features": []}\nHope this helps!',
            expected_features=0,
        )
        assert result.title == "Oceana"

    def test_rejects_a_reply_with_no_json(self) -> None:
        with pytest.raises(AIError, match="no JSON"):
            parse_translation("I cannot translate this.", expected_features=0)

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises(AIError, match="not valid JSON"):
            parse_translation('{"title": "x", }', expected_features=0)

    def test_rejects_an_empty_title(self) -> None:
        with pytest.raises(AIError, match="no usable title"):
            parse_translation(
                '{"title": "  ", "description": "x", "features": []}',
                expected_features=0,
            )

    def test_rejects_a_dropped_feature(self) -> None:
        """The count must match exactly.

        A listing whose feature list silently shrinks in Portuguese is showing
        a Brazilian buyer a different property from the one the agent entered.
        """
        with pytest.raises(AIError, match="expected 2"):
            parse_translation(
                '{"title": "x", "description": "y", "features": ["only one"]}',
                expected_features=2,
            )

    def test_rejects_an_invented_feature(self) -> None:
        with pytest.raises(AIError, match="expected 1"):
            parse_translation(
                '{"title": "x", "description": "y", '
                '"features": ["one", "and a bonus"]}',
                expected_features=1,
            )

    def test_rejects_non_string_features(self) -> None:
        with pytest.raises(AIError, match="malformed features"):
            parse_translation(
                '{"title": "x", "description": "y", "features": [42]}',
                expected_features=1,
            )

    def test_truncates_to_the_column_widths(self) -> None:
        """A verbose model must not take the write down with it."""
        result = parse_translation(
            '{"title": "%s", "description": null, "features": ["%s"]}'
            % ("t" * 400, "f" * 200),
            expected_features=1,
        )
        assert len(result.title) == 200
        assert len(result.features[0]) == 60

    def test_empty_description_becomes_null(self) -> None:
        result = parse_translation(
            '{"title": "x", "description": "   ", "features": []}',
            expected_features=0,
        )
        assert result.description is None


class TestRequestedLocale:
    """Reading the language off the standard header.

    Deliberately forgiving: a browser that sends `pt-br;q=0.9` or a long
    Accept-Language chain must still land on Portuguese, and anything
    unrecognised must land on a language we actually ship rather than on an
    empty page.
    """

    def test_exact_match(self) -> None:
        from app.api.v1.properties import requested_locale

        assert requested_locale("pt-BR") == "pt-BR"
        assert requested_locale("ru") == "ru"

    def test_case_is_not_significant(self) -> None:
        from app.api.v1.properties import requested_locale

        assert requested_locale("pt-br") == "pt-BR"
        assert requested_locale("PT-BR") == "pt-BR"

    def test_takes_the_first_language_it_ships(self) -> None:
        from app.api.v1.properties import requested_locale

        assert requested_locale("fr-FR,fr;q=0.9,ru;q=0.8") == "ru"

    def test_quality_values_are_ignored_not_parsed(self) -> None:
        from app.api.v1.properties import requested_locale

        assert requested_locale("ru;q=0.2") == "ru"

    def test_unknown_and_missing_fall_back_to_the_default(self) -> None:
        from app.api.v1.properties import requested_locale

        assert requested_locale("fr,de") == "en"
        assert requested_locale(None) == "en"
        assert requested_locale("") == "en"
