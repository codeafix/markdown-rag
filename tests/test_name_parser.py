"""Tests for app/name_parser.py"""
import pytest
from unittest.mock import patch, MagicMock
from name_parser import extract_name_terms, extract_entities_from_text


# ── extract_name_terms ────────────────────────────────────────────────────────

def test_quoted_single_name():
    assert extract_name_terms('"John Smith"') == ["John Smith"]


def test_quoted_names_preferred_over_heuristic():
    # Quoted names take priority; the heuristic result is discarded
    result = extract_name_terms('"Alice Brown" and Bob Jones')
    assert result == ["Alice Brown"]


def test_multi_word_capitalized():
    # The heuristic fallback uses individual token regex, not NAME_MULTI,
    # so "John Smith" is returned as two separate tokens.
    result = extract_name_terms("notes about John Smith from last week")
    assert "John" in result
    assert "Smith" in result


def test_single_capitalized_word():
    result = extract_name_terms("notes about Alice")
    assert "Alice" in result


def test_stop_words_excluded():
    stop_queries = [
        "What happened today?",
        "When did the meeting occur?",
        "Where is the document?",
        "How are things?",
        "The Daily Notes",
        "Meeting notes from last week",
    ]
    for q in stop_queries:
        result = extract_name_terms(q)
        # None of the stop words should appear
        for word in ("What", "When", "Where", "How", "The", "Daily", "Meeting", "Notes", "Last"):
            assert word not in result, f"Stop word '{word}' found in result for query: {q!r}"


def test_no_names():
    result = extract_name_terms("what happened recently with the project?")
    assert result == []


def test_deduplication():
    # Individual tokens are deduplicated
    result = extract_name_terms("John Smith met John Smith again")
    assert result.count("John") == 1
    assert result.count("Smith") == 1


def test_single_quoted_name_no_space():
    result = extract_name_terms("'John Smith' mentioned")
    assert "John Smith" in result


def test_multiple_quoted_names():
    result = extract_name_terms('"John Smith" and "Alice Brown"')
    assert "John Smith" in result
    assert "Alice Brown" in result


def test_hyphenated_path_not_a_name():
    # Lower-case words should not be picked up
    result = extract_name_terms("see notes/work-log for details")
    assert result == []


# ── extract_entities_from_text ────────────────────────────────────────────────

def test_extract_entities_no_spacy():
    """When spaCy fails to load, returns empty list."""
    with patch("name_parser._get_nlp", return_value=None):
        result = extract_entities_from_text("John Smith works at Anthropic in London.")
    assert result == []


def test_extract_entities_person():
    """Mock spaCy to return a PERSON entity."""
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_ent = MagicMock()
    mock_ent.label_ = "PERSON"
    mock_ent.text = "Alice Brown"
    mock_doc.ents = [mock_ent]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("Alice Brown attended the meeting.")
    assert "person:Alice Brown" in result


def test_extract_entities_org():
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_ent = MagicMock()
    mock_ent.label_ = "ORG"
    mock_ent.text = "Anthropic"
    mock_doc.ents = [mock_ent]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("Anthropic built Claude.")
    assert "org:Anthropic" in result


def test_extract_entities_gpe():
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_ent = MagicMock()
    mock_ent.label_ = "GPE"
    mock_ent.text = "London"
    mock_doc.ents = [mock_ent]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("I visited London last week.")
    assert "place:London" in result


def test_extract_entities_work_of_art():
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_ent = MagicMock()
    mock_ent.label_ = "WORK_OF_ART"
    mock_ent.text = "Alien Clay"
    mock_doc.ents = [mock_ent]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("I read Alien Clay.")
    assert "work:Alien Clay" in result


def test_extract_entities_ignored_label():
    """Entities with labels not in the allow-list are dropped."""
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_ent = MagicMock()
    mock_ent.label_ = "DATE"
    mock_ent.text = "yesterday"
    mock_doc.ents = [mock_ent]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("yesterday was good")
    assert result == []


def test_extract_entities_deduplication():
    """Duplicate entities (case-insensitive) are only included once."""
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    ent1 = MagicMock()
    ent1.label_ = "PERSON"
    ent1.text = "Alice"
    ent2 = MagicMock()
    ent2.label_ = "PERSON"
    ent2.text = "alice"  # same, different case
    mock_doc.ents = [ent1, ent2]
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("Alice met alice")
    # Only one entry for alice
    assert len([r for r in result if "alice" in r.lower()]) == 1


def test_extract_entities_empty_text():
    mock_nlp = MagicMock()
    mock_doc = MagicMock()
    mock_doc.ents = []
    mock_nlp.return_value = mock_doc

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("")
    assert result == []


def test_extract_entities_nlp_raises():
    """If nlp() call itself raises, returns empty list."""
    mock_nlp = MagicMock()
    mock_nlp.side_effect = Exception("NLP crash")

    with patch("name_parser._get_nlp", return_value=mock_nlp):
        result = extract_entities_from_text("some text")
    assert result == []
