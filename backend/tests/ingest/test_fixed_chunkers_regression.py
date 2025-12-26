import pytest

from backend.ingest.chunking.char_chunker import chunk_text
from backend.ingest.chunking.token_chunker import chunk_text_by_tokens


def test_char_chunker_regression():
    text = "abcdefghij"
    chunks = chunk_text(text, size=4, overlap=1)
    assert chunks == ["abcd", "defg", "ghij"]


def test_token_chunker_regression():
    text = "one two three four five six seven eight"
    chunks = chunk_text_by_tokens(text, max_tokens=3, overlap=0.5)
    assert chunks == [
        "one two three",
        "three four five",
        "five six seven",
        "seven eight",
    ]
