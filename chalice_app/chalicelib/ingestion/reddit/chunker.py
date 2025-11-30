from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable, List, Optional

from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter

from chalicelib.core.logger_config import setup_logger
from chalicelib.models.data_objects import RedditPost

logger = setup_logger(__name__)

try:  # pragma: no cover - optional dependency
    from semchunk import chunkerify as _semchunk_chunkerify
except ImportError:  # pragma: no cover - optional dependency
    _semchunk_chunkerify = None


@dataclass
class Metadata:
    post_id: str
    year: int
    month: int
    type: str
    subreddit_name: str
    chunk_id: int
    timestamp: int
    date: str
    comment_id: Optional[str] = None


class RedditChunker:
    def __init__(
        self,
        chunk_size: int = 1_000,
        chunk_overlap: int = 200,
        *,
        semantic_chunk_size_tokens: Optional[int] = None,
        semantic_overlap_tokens: Optional[int] = None,
        semantic_tokenizer: str = "cl100k_base",
        semantic_chunker: Optional[Callable[[str], List[str]]] = None,
    ):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        if semantic_chunker is not None:
            self._semantic_chunker: Optional[
                Callable[[str], List[str]]
            ] = semantic_chunker
        else:
            self._semantic_chunker = _build_semchunk_chunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                semantic_chunk_size_tokens=semantic_chunk_size_tokens,
                semantic_overlap_tokens=semantic_overlap_tokens,
                semantic_tokenizer=semantic_tokenizer,
            )

        if self._semantic_chunker is None:
            logger.warning(
                "semchunk not available; falling back to RecursiveCharacterTextSplitter."
            )

    def chunk_reddit_post(self, post: RedditPost) -> List[Document]:
        full_text = f"Title: {post.title}\n\n{post.content}"
        chunks = self._chunk_text(full_text)

        # Process comments separately
        documents: List[Document] = []
        for chunk_id, chunk in enumerate(chunks):
            # Add a timestamp marker directly in the document content
            time_marker = f"[From {post.year}-{post.month:02d}] "
            enhanced_chunk = time_marker + chunk

            documents.append(
                Document(
                    page_content=enhanced_chunk,
                    metadata=asdict(
                        Metadata(
                            post_id=post.id,
                            year=post.year,
                            month=post.month,
                            subreddit_name=post.subreddit_name,
                            type="post",
                            chunk_id=chunk_id,
                            timestamp=int(f"{post.year}{post.month:02d}"),
                            date=f"{post.year}-{post.month:02d}",
                        )
                    ),
                )
            )

        for comment in post.comments:
            comment_chunks = self._chunk_text(comment.body)
            for chunk_id, chunk in enumerate(comment_chunks):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata=asdict(
                            Metadata(
                                post_id=post.id,
                                comment_id=comment.id,
                                year=comment.year,
                                month=comment.month,
                                subreddit_name=post.subreddit_name,
                                type="comment",
                                chunk_id=chunk_id,
                                timestamp=int(f"{comment.year}{comment.month:02d}"),
                                date=f"{comment.year}-{comment.month:02d}",
                            )
                        ),
                    )
                )

        return documents

    def process_comments(self, post, comments):
        documents: List[Document] = []
        for comment_idx, comment in enumerate(comments):
            # Skip empty or low-quality comments
            if not comment.body or len(comment.body.strip()) < 10:
                continue

            # Split comment into chunks if needed
            chunks = self._chunk_text(comment.body)

            for chunk_id, chunk in enumerate(chunks):
                # Add a timestamp marker directly in the document content
                time_marker = f"[Comment from {comment.year}-{comment.month:02d}] "
                enhanced_chunk = time_marker + chunk

                documents.append(
                    Document(
                        page_content=enhanced_chunk,
                        metadata=asdict(
                            Metadata(
                                post_id=post.id,
                                comment_id=comment.id,
                                # Keep as integers since Weaviate schema has been fixed
                                year=comment.year,
                                month=comment.month,
                                subreddit_name=post.subreddit_name,
                                type="comment",
                                chunk_id=chunk_id,
                                timestamp=int(f"{comment.year}{comment.month:02d}"),
                                date=f"{comment.year}-{comment.month:02d}",
                            )
                        ),
                    )
                )

        return documents

    def _chunk_text(self, text: str) -> List[str]:
        if not text:
            return []

        if self._semantic_chunker is not None:
            try:
                chunks = self._semantic_chunker(text)
                if chunks:
                    return list(chunks)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Semantic chunker failed (%s); falling back to recursive splitter.",
                    exc,
                )
                self._semantic_chunker = None

        return self.text_splitter.split_text(text)


def _build_semchunk_chunker(
    *,
    chunk_size: int,
    chunk_overlap: int,
    semantic_chunk_size_tokens: Optional[int],
    semantic_overlap_tokens: Optional[int],
    semantic_tokenizer: str,
) -> Optional[Callable[[str], List[str]]]:
    if _semchunk_chunkerify is None:  # pragma: no cover - optional dependency
        return None

    token_chunk_size = semantic_chunk_size_tokens or max(1, round(chunk_size / 4))
    if token_chunk_size <= 0:
        token_chunk_size = 1

    if semantic_overlap_tokens is not None:
        overlap_tokens = min(max(semantic_overlap_tokens, 0), token_chunk_size - 1)
    else:
        overlap_ratio = 0.0
        if chunk_size > 0:
            overlap_ratio = max(0.0, min(1.0, chunk_overlap / chunk_size))
        overlap_tokens = int(round(token_chunk_size * overlap_ratio))
        overlap_tokens = min(overlap_tokens, max(token_chunk_size - 1, 0))

    try:
        chunker = _semchunk_chunkerify(
            semantic_tokenizer,
            chunk_size=token_chunk_size,
            memoize=True,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Unable to initialize semchunk chunker: %s", exc)
        return None

    def _chunk(text: str) -> List[str]:
        overlap = overlap_tokens if overlap_tokens > 0 else None
        result = chunker(text, overlap=overlap)
        return list(result)

    return _chunk
