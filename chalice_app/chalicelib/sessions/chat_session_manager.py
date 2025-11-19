import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple, Optional, Any
from langsmith import traceable, get_current_run_tree
from chalicelib.indexers.indexer_factory import IndexerFactory
from chalicelib.core.performance_timer import measure_execution_time
from chalicelib.models.data_objects import (
    ChatMessage,
    SearchResult,
    RetrievalMetricsDocument,
)
from chalicelib.llm import LLMFactory, LLMProvider, BM25Reranker
from chalicelib.llm.reranker import RerankerInput
from chalicelib.core.config import config
from chalicelib.services.langsmith import log_customer_query

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Set LangSmith environment variables
os.environ["LANGSMITH_API_KEY"] = config.langsmith_api_key
os.environ["LANGSMITH_API_URL"] = config.langsmith_api_url
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "pr-respectful-icicle-91"

PERSONA = """You are a knowledgeable shopping assistant who helps people discover interesting and useful products. Your role is to:
1. Understand the user's needs, preferences, and constraints
2. Analyze the provided Reddit discussions and recommendations
3. Make personalized product suggestions based on real user experiences
4. Explain why you think certain products would be good choices
5. Be honest about pros and cons of products
6. Ask clarifying questions when needed to make better recommendations

IMPORTANT: Only provide recommendations based on the Reddit discussions and data provided to you. If you don't have enough information about a specific product, topic, or category from the provided data, clearly state "I don't have enough information about this topic from the available data" rather than making up or guessing information.

Keep responses concise and focused on helping users make informed shopping decisions. When discussing products, highlight key features, use cases, and what makes them worth considering."""


class Chat:
    def __init__(self):
        self.indexer = IndexerFactory.create_indexer()
        llm_provider = LLMProvider.DEEPSEEK
        self.llm = LLMFactory.create_llm(provider=llm_provider)
        self.reranker = BM25Reranker()

    @measure_execution_time
    @traceable(name="chat_session")
    def process_chat(
        self,
        query: str,
        session_id: str,
        chat_history: List[Dict[str, str]],
        socket_id: str,
        request_id: Optional[str] = None,
    ) -> Tuple[str, List[ChatMessage], Dict[str, Any]]:

        # Convert dict chat history to ChatMessage objects if needed
        chat_messages = [
            ChatMessage.from_dict(msg) if not isinstance(msg, ChatMessage) else msg
            for msg in chat_history
        ]

        try:
            # Add session metadata to the current trace
            run_id = None
            try:
                current_run = get_current_run_tree()
                if current_run:
                    run_id = str(current_run.id)
                    logger.info(f"Captured run_id: {run_id}")

                    # RunTree object doesn't have a 'run' attribute - use the object directly
                    current_run.update(
                        metadata={
                            "session_id": session_id,
                            "socket_id": socket_id,
                            "request_id": request_id,
                            "query_length": len(query),
                            "chat_history_length": len(chat_history),
                        }
                    )
            except Exception as meta_error:
                logger.warning(f"Failed to add session metadata: {meta_error}")

            rewritten_prompt = self._rewrite_prompt(
                query=query, chat_messages=chat_messages
            )

            # Log both for debugging/transparency
            logger.info(f"Original query: {query}")
            logger.info(f"Rewritten query: {rewritten_prompt}")

            hype_response_query = self._hype_prompt(query=rewritten_prompt)
            logger.info(f"Hype Response Query: {hype_response_query}")

            # Execute both searches in parallel for better performance
            with ThreadPoolExecutor(max_workers=2) as executor:
                future1 = executor.submit(self._perform_search, rewritten_prompt)
                future2 = executor.submit(self._perform_search, hype_response_query)

                search_results = future1.result()
                search_results_from_hype = future2.result()

            # Combine and deduplicate search results from both queries
            combined_results = self._combine_search_results(
                results1=search_results, results2=search_results_from_hype
            )

            # Store pre-rerank results for retrieval metrics
            pre_rerank_results = self._prepare_results_for_metrics(
                results=combined_results
            )

            # Rerank combined results once using LLM for better relevance
            reranked_results = self._rerank_results(
                query=rewritten_prompt, results=combined_results, limit=15
            )

            # Get relevance scores from reranker for retrieval metrics
            reranker_scores = self.reranker.get_relevance_scores()

            search_result_context = self._build_context(search_results=reranked_results)

            updated_chat_history = self._prepare_chat_history(
                chat_history=chat_messages,
                search_result_context=search_result_context,
                query=query,
                rewritten_query=rewritten_prompt,
            )

            # Generate response
            response = self._generate_response(chat_history=updated_chat_history)

            # Prepare evaluation metadata
            eval_metadata = {
                "chat_history_length": len(chat_history),
                "rewritten_query": rewritten_prompt,
                "hyde_query": hype_response_query,
                "num_combined_results": len(combined_results),
                "num_reranked_results": len(reranked_results),
                "top_results": [
                    r.text for r in reranked_results[:3]
                ],  # Top 3 for evaluation
                "search_context": search_result_context[
                    :1000
                ],  # Truncate for evaluation
                "run_id": run_id,  # Pass run_id to evaluator
                # Retrieval metrics data (convert to dict for JSON serialization)
                "pre_rerank_results": [doc.to_dict() for doc in pre_rerank_results],
                "reranker_scores": [score.to_dict() for score in reranker_scores],
            }

            # Log query and response to LangSmith dataset
            try:
                log_customer_query(
                    query=query,
                    session_id=session_id,
                    response=response,
                    metadata={
                        "socket_id": socket_id,
                        "chat_history_length": len(chat_history),
                        "rewritten_query": rewritten_prompt,
                        "hyde_query": hype_response_query,
                        "num_combined_results": len(combined_results),
                        "num_reranked_results": len(reranked_results),
                        "num_rewritten_results": len(search_results),
                        "num_hyde_results": len(search_results_from_hype),
                    },
                )
            except Exception as log_error:
                # Don't fail the request if logging fails
                logger.warning(f"Failed to log query to dataset: {log_error}")

            return response, updated_chat_history, eval_metadata

        except Exception as e:
            logger.error(f"Error in chat processing: {str(e)}", exc_info=True)
            raise

    @measure_execution_time
    def _rewrite_prompt(self, query: str, chat_messages: List[ChatMessage]) -> str:
        return self.llm.rewrite_prompt(
            last_message_content=query, message_history=chat_messages
        )

    @measure_execution_time
    def _hype_prompt(self, query: str) -> str:
        return self.llm.generate_hyde(query=query)

    @measure_execution_time
    def _perform_search(self, query: str) -> List[SearchResult]:
        results = self.indexer.hybrid_search(query=query, limit=15, alpha=0.5)
        return [
            SearchResult(text=result.text, metadata=result.metadata, score=result.score)
            for result in results
        ]

    def _combine_search_results(
        self, results1: List[SearchResult], results2: List[SearchResult]
    ) -> List[SearchResult]:
        """
        Combine and deduplicate search results from two different queries.
        Uses text content to identify duplicates and keeps the one with higher score.
        Results are sorted by score in descending order.
        """
        # Create a dictionary to track unique results by text content
        unique_results: Dict[str, SearchResult] = {}

        for result in results1 + results2:
            text_key = result.text.strip()

            # Keep the result with the higher score if duplicate exists
            if (
                text_key not in unique_results
                or result.score > unique_results[text_key].score
            ):
                unique_results[text_key] = result

        combined = sorted(unique_results.values(), key=lambda x: x.score, reverse=True)

        logger.info(
            f"Combined {len(results1)} + {len(results2)} results into {len(combined)} unique results"
        )

        return combined

    @measure_execution_time
    def _rerank_results(
        self, query: str, results: List[SearchResult], limit: int
    ) -> List[SearchResult]:
        """
        Rerank search results using BM25 for better relevance.
        Converts SearchResult objects to reranker format and back.
        """
        if not results or len(results) <= limit:
            return results[:limit]

        # Convert SearchResult objects to RerankerInput dataclass
        reranker_inputs = [
            RerankerInput(
                text=result.text,
                metadata=result.metadata,
                score=result.score,
            )
            for result in results
        ]

        # Rerank using BM25
        reranked_inputs = self.reranker.rerank(
            query=query, results=reranker_inputs, limit=limit
        )

        # Convert back to SearchResult objects
        reranked_results = [
            SearchResult(
                text=input_item.text,
                metadata=input_item.metadata,
                score=input_item.score,
            )
            for input_item in reranked_inputs
        ]

        logger.info(f"Reranked {len(results)} results to top {len(reranked_results)}")
        return reranked_results

    @measure_execution_time
    def _build_context(self, search_results: List[SearchResult]) -> str:
        context = "Here are some relevant Reddit discussions and recommendations:\n\n"
        for result in search_results[:3]:
            cleaned_text = json.dumps(result.text)[1:-1]
            context += f"- {cleaned_text}\n"

        # Add a clear instruction about how to use this context
        context += "\nPlease focus on answering the user's current question directly using this information. Prioritize addressing their specific query rather than summarizing previous exchanges."
        return context

    @measure_execution_time
    def _prepare_chat_history(
        self,
        chat_history: List[ChatMessage],
        search_result_context: str,
        query: str,
        rewritten_query: Optional[str] = None,
    ) -> List[ChatMessage]:
        # Ensure we always have the system persona message at the beginning
        if (
            not chat_history
            or chat_history[0].role != "system"
            or PERSONA not in chat_history[0].content
        ):
            chat_history = [ChatMessage(role="system", content=PERSONA)]

        if len(chat_history) > 8:
            chat_history = [chat_history[0]] + chat_history[-7:]

        # Add context as a separate system message
        chat_history.append(ChatMessage(role="system", content=search_result_context))

        actual_query = query
        if rewritten_query and rewritten_query != query:
            chat_history.append(
                ChatMessage(
                    role="system",
                    content=f'The user\'s original question was: "{query}"\nIt has been interpreted as: "{rewritten_query}"',
                )
            )
            actual_query = rewritten_query

        chat_history.append(ChatMessage(role="user", content=actual_query))

        # logger.info(f"Chat history: {chat_history}")
        return chat_history

    @measure_execution_time
    def _generate_response(self, chat_history: List[ChatMessage]) -> str:
        response = self.llm.chat(messages=chat_history)
        # logger.info(f"Response: {response}")
        return response

    def _prepare_results_for_metrics(
        self, results: List[SearchResult]
    ) -> List[RetrievalMetricsDocument]:
        """
        Prepare search results for retrieval metrics computation.

        Converts SearchResult objects to RetrievalMetricsDocument with doc_id.
        """
        import hashlib

        prepared_results = []
        for result in results:
            # MD5 used for non-cryptographic document ID generation only
            doc_id = hashlib.md5(
                result.text.encode(), usedforsecurity=False
            ).hexdigest()
            metrics_doc = RetrievalMetricsDocument.from_search_result(
                search_result=result, doc_id=doc_id
            )
            prepared_results.append(metrics_doc)

        return prepared_results
