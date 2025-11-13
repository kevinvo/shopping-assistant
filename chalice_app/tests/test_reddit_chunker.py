from __future__ import annotations

from typing import List

import pytest
from langchain_core.embeddings import Embeddings

from chalicelib.ingestion.reddit import chunker as reddit_chunker
from chalicelib.models.data_objects import RedditComment, RedditPost


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> List[float]:
        return [0.0]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> List[float]:
        return self.embed_query(text)


def _build_post(*, include_comment: bool = True) -> RedditPost:
    comments = (
        [
            RedditComment(
                id="c1",
                score=1,
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
        content="Main body of the post.",
        comments=comments,
        year=2024,
        month=5,
        subreddit_name="ShoppingDeals",
    )


def test_chunk_reddit_post_semantic_split(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSemanticChunker:
        def __init__(self, *args, **kwargs):
            pass

        def split_text(self, text: str) -> List[str]:
            if text.startswith("Title:"):
                return ["Post chunk 1", "Post chunk 2"]
            return ["Comment chunk"]

    monkeypatch.setattr(
        reddit_chunker, "_load_semantic_chunker", lambda: FakeSemanticChunker
    )

    chunker = reddit_chunker.RedditChunker(embeddings=FakeEmbeddings())
    post = _build_post()

    documents = chunker.chunk_reddit_post(post)

    assert len(documents) == 3

    post_doc = documents[0]
    assert post_doc.page_content.startswith("[From 2024-05] ")
    assert post_doc.metadata["type"] == "post"
    assert post_doc.metadata["chunk_id"] == 0
    assert post_doc.metadata["timestamp"] == 202405

    comment_doc = documents[-1]
    assert comment_doc.page_content == "Comment chunk"
    assert comment_doc.metadata["type"] == "comment"
    assert comment_doc.metadata["comment_id"] == "c1"
    assert comment_doc.metadata["chunk_id"] == 0


def test_chunk_reddit_post_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptySemanticChunker:
        def __init__(self, *args, **kwargs):
            pass

        def split_text(self, text: str) -> List[str]:
            return []

    monkeypatch.setattr(
        reddit_chunker, "_load_semantic_chunker", lambda: EmptySemanticChunker
    )

    chunker = reddit_chunker.RedditChunker(embeddings=FakeEmbeddings())
    post = _build_post()

    documents = chunker.chunk_reddit_post(post)

    assert len(documents) == 2  # post + comment fallback

    post_doc = documents[0]
    assert post_doc.metadata["type"] == "post"
    assert post_doc.metadata["chunk_id"] == 0
    assert post_doc.page_content.startswith("[From 2024-05] Title: Sample Title")

    comment_doc = documents[1]
    assert comment_doc.page_content == "Comment body text."
    assert comment_doc.metadata["type"] == "comment"
    assert comment_doc.metadata["comment_id"] == "c1"
