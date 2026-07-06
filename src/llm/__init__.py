"""Provider-agnostic LLM client package ("bring your own LLM").

See src.llm.client for the LLMClient interface, the get_client() cached
factory, and the LLMError exception used by both the extraction layer
(src/extraction/) and the agent layer (src/agent/).
"""

from src.llm.client import LLMClient, LLMError, get_client

__all__ = ["LLMClient", "LLMError", "get_client"]
