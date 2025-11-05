from langchain.text_splitter import RecursiveCharacterTextSplitter
from chalicelib.models.lambda_constants import RedditPost
from typing import List, Optional
from langchain.schema import Document
from dataclasses import dataclass, asdict
from chalicelib.core.logger_config import setup_logger

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
    def __init__(self, chunk_size=1_000, chunk_overlap=200):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    def chunk_reddit_post(self, post: RedditPost) -> List[Document]:
        full_text = f"Title: {post.title}\n\n{post.content}"
        chunks = self.text_splitter.split_text(full_text)

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
            comment_chunks = self.text_splitter.split_text(comment.body)
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
