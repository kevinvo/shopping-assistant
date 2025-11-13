from __future__ import annotations

from typing import List

import pytest

import chalicelib.ingestion.reddit.chunker as chunker_module
from chalicelib.ingestion.reddit.chunker import RedditChunker
from chalicelib.models.data_objects import RedditComment, RedditPost


def _build_post(include_comment: bool = True) -> RedditPost:
    comments = (
        [
            RedditComment(
                id="c1",
                score=5,
                body="Comment body text.",
                year=2024,
                month=5,
            )
        ]
        if include_comment
        else []
    )

    return RedditPost(
        id="p1",
        title="Sample Title",
        original_title="Sample Title",
        score=42,
        url="https://example.com",
        content="Main body of the post with enough text to span multiple chunks.",
        comments=comments,
        year=2024,
        month=5,
        subreddit_name="shopping",
    )


def test_semantic_chunker_default_behavior(monkeypatch: pytest.MonkeyPatch):
    def fake_builder(**kwargs):
        return lambda text: ["semantic-default"]

    monkeypatch.setattr(chunker_module, "_build_semchunk_chunker", fake_builder)

    chunker = RedditChunker(chunk_size=50, chunk_overlap=10)
    post = _build_post()

    documents = chunker.chunk_reddit_post(post)

    assert documents[0].page_content == "[From 2024-05] semantic-default"
    assert documents[0].metadata["type"] == "post"
    assert documents[-1].page_content == "semantic-default"
    assert documents[-1].metadata["comment_id"] == "c1"


def test_semantic_chunker_injected():
    calls: List[str] = []

    def fake_semantic_chunker(text: str) -> List[str]:
        calls.append(text)
        if text.startswith("Title:"):
            return ["semantic-post"]
        return ["semantic-comment"]

    chunker = RedditChunker(
        chunk_size=100,
        chunk_overlap=0,
        semantic_chunker=fake_semantic_chunker,
    )

    post = _build_post()
    documents = chunker.chunk_reddit_post(post)

    assert calls[0].startswith("Title: Sample Title")
    assert documents[0].page_content == "[From 2024-05] semantic-post"
    assert documents[1].page_content == "semantic-comment"
    assert documents[1].metadata["comment_id"] == "c1"


def test_semantic_chunker_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        chunker_module, "_build_semchunk_chunker", lambda **kwargs: None
    )

    chunker = RedditChunker(chunk_size=50, chunk_overlap=0)
    post = _build_post(include_comment=False)

    documents = chunker.chunk_reddit_post(post)
    assert documents[0].page_content.startswith("[From 2024-05] Title: Sample Title")
