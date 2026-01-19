"""Parser for LLM responses into structured correction proposals."""
from __future__ import annotations

import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class ResponseParser:
    """Parses LLM responses into structured proposal dictionaries.
    
    Handles both JSON and raw text responses, providing consistent
    output format for downstream processing.
    
    Single Responsibility: Response parsing only, no model invocation.
    """
    
    def parse(self, content: str) -> List[Dict[str, Any]]:
        """Parse response content into proposal dictionaries.
        
        Attempts to parse as JSON first. If that fails, tries to fix
        common JSON issues and retries. Falls back to raw content.
        
        Args:
            content: Raw response content from LLM
            
        Returns:
            List of proposal dictionaries. On parse failure, returns
            [{"raw": content}] to preserve the response.
        """
        # Try JSON parsing first
        try:
            data = json.loads(content)
            return self._normalize_json_response(data)
        except json.JSONDecodeError as e:
            logger.debug(f"🤖 Response is not valid JSON: {e}")
            
            # Try to fix common issues
            fixed_content = self._attempt_json_fix(content)
            if fixed_content != content:
                try:
                    data = json.loads(fixed_content)
                    logger.debug("🤖 Successfully parsed after JSON fix")
                    return self._normalize_json_response(data)
                except json.JSONDecodeError:
                    pass  # Fall through to raw handling
            
            return self._handle_raw_response(content)
    
    def _attempt_json_fix(self, content: str) -> str:
        """Attempt to fix common JSON formatting issues.

        Args:
            content: Raw JSON string

        Returns:
            Fixed JSON string (or original if no fixes applied)
        """
        import re

        # Fix 0: Strip markdown code fences (```json ... ``` or ``` ... ```)
        # Models often wrap JSON in markdown code blocks
        fixed = content.strip()
        if fixed.startswith("```"):
            # Remove opening fence (with optional language identifier)
            fixed = re.sub(r'^```\w*\s*\n?', '', fixed)
            # Remove closing fence
            fixed = re.sub(r'\n?```\s*$', '', fixed)
            fixed = fixed.strip()
            logger.debug("🤖 Stripped markdown code fences from response")

        # Fix 1: Replace invalid escape sequences like \' with '
        # (JSON only allows \", \\, \/, \b, \f, \n, \r, \t)
        fixed = fixed.replace("\\'", "'")

        # Fix 2: Remove any trailing commas before } or ]
        fixed = re.sub(r',\s*}', '}', fixed)
        fixed = re.sub(r',\s*]', ']', fixed)

        return fixed
    
    def _normalize_json_response(self, data: Any) -> List[Dict[str, Any]]:
        """Normalize JSON data into a list of dictionaries.
        
        Handles both single dict and list of dicts responses.
        
        Args:
            data: Parsed JSON data
            
        Returns:
            List of dictionaries
        """
        if isinstance(data, dict):
            # Single proposal - wrap in list
            return [data]
        elif isinstance(data, list):
            # Already a list - return as-is
            return data
        else:
            # Unexpected type - wrap in error dict
            logger.warning(f"🤖 Unexpected JSON type: {type(data)}")
            return [{"error": "unexpected_type", "data": str(data)}]
    
    def _handle_raw_response(self, content: str) -> List[Dict[str, Any]]:
        """Handle non-JSON responses.
        
        Wraps raw content in a dict for downstream handling.
        The "raw" key indicates this needs manual processing.
        
        Args:
            content: Raw response text
            
        Returns:
            List with single dict containing raw content
        """
        logger.info(
            f"🤖 Returning raw response (non-JSON): "
            f"{content[:100]}{'...' if len(content) > 100 else ''}"
        )
        return [{"raw": content}]

