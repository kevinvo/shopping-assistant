from dataclasses import dataclass, asdict
from importlib import import_module
from typing import TYPE_CHECKING, List, Optional, Type

from langchain.schema import Document
from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

if TYPE_CHECKING:
    from langchain_experimental.text_splitter import SemanticChunker  # type: ignore[import]
    from langchain_openai import OpenAIEmbeddings  # type: ignore[import]

from chalicelib.core.config import config
from chalicelib.core.logger_config import setup_logger
from chalicelib.models.data_objects import RedditPost

logger = setup_logger(__name__)


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
        embeddings: Optional[Embeddings] = None,
        breakpoint_threshold_type: str = "percentile",
        breakpoint_threshold_amount: float = 95.0,
    ):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be zero or positive")

        min_chunk_size = max(1, chunk_size - chunk_overlap)

        self.embeddings = embeddings or _create_default_embeddings()
        semantic_chunker_cls = _load_semantic_chunker()
        self.text_splitter = semantic_chunker_cls(
            embeddings=self.embeddings,
            min_chunk_size=min_chunk_size,
            max_chunk_size=chunk_size,
            breakpoint_threshold_type=breakpoint_threshold_type,
            breakpoint_threshold_amount=breakpoint_threshold_amount,
        )

    def chunk_reddit_post(self, post: RedditPost) -> List[Document]:
        full_text = f"Title: {post.title}\n\n{post.content}"
        chunks = self._split_with_fallback(full_text)

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
            comment_chunks = self._split_with_fallback(comment.body)
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

    def _split_with_fallback(self, text: str) -> List[str]:
        if not text:
            return []

        chunks = self.text_splitter.split_text(text)
        if chunks:
            return chunks

        # Ensure short documents are still indexed even if the semantic splitter
        # filters them out by returning the original text.
        return [text.strip()] if text.strip() else []


def _load_semantic_chunker() -> "Type[SemanticChunker]":
    try:
        module = import_module("langchain_experimental.text_splitter")
        semantic_chunker_cls: "Type[SemanticChunker]" = getattr(
            module, "SemanticChunker"
        )
        return semantic_chunker_cls
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "SemanticChunker requires the langchain-experimental package. "
            "Install it with `pip install langchain-experimental`."
        ) from exc


def _create_default_embeddings() -> Embeddings:
    try:
        module = import_module("langchain_openai")
        openai_embeddings_cls: Type["OpenAIEmbeddings"] = getattr(
            module, "OpenAIEmbeddings"
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "OpenAIEmbeddings requires the langchain-openai package. "
            "Install it with `pip install langchain-openai`."
        ) from exc

    return openai_embeddings_cls(
        api_key=SecretStr(config.openai_api_key), model="text-embedding-3-small"
    )

    def process_comments(self, post, comments):
        documents: List[Document] = []
        for comment_idx, comment in enumerate(comments):
            # Skip empty or low-quality comments
            if not comment.body or len(comment.body.strip()) < 10:
                continue

            # Split comment into chunks if needed
            chunks = self.text_splitter.split_text(comment.body)

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
