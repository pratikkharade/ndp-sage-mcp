"""Documentation MCP tools."""

from __future__ import annotations

import logging

from ..docs_helper import SAGEDocsHelper


logger = logging.getLogger(__name__)


def register(mcp, *, docs_helper: SAGEDocsHelper) -> None:
    @mcp.tool
    def ask_sage_docs(question: str) -> str:
        """Ask questions about Sage documentation."""
        try:
            if not question.strip():
                return "Please provide a specific question about SAGE. " + docs_helper.list_faq_topics()
            return docs_helper.search_and_answer(question)
        except Exception as e:
            logger.error(f"Error querying documentation: {e}")
            return f"Error searching documentation: {e}"

    @mcp.tool
    def sage_faq(topic: str = "") -> str:
        """Get answers to frequently asked questions about SAGE.

        Available topics: getting_started, plugin_development, data_access,
        job_submission, sensors, troubleshooting, node_access.
        """
        try:
            if not topic:
                return docs_helper.list_faq_topics()
            answer = docs_helper.get_faq_answer(topic)
            if answer:
                return answer
            available = ", ".join(docs_helper.faqs.keys())
            return f"Topic '{topic}' not found. Available topics: {available}"
        except Exception as e:
            logger.error(f"Error getting FAQ: {e}")
            return f"Error getting FAQ: {e}"

    @mcp.tool
    def search_sage_docs(query: str, max_results: int = 5) -> str:
        """Search the Sage documentation."""
        try:
            if not query.strip():
                return "Please provide a search query."
            results = docs_helper.search_docs(query, max_results)
            if not results:
                return f"No documentation found for '{query}'."
            parts = [f"Documentation search results for '{query}':\n"]
            for i, (section, content, score) in enumerate(results, 1):
                parts.append(f"**{i}. {section}** (relevance: {score})")
                parts.append(content)
                parts.append("")
            return "\n".join(parts)
        except Exception as e:
            logger.error(f"Error searching documentation: {e}")
            return f"Error searching documentation: {e}"
