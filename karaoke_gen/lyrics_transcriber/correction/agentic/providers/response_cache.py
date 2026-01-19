"""Response caching for LLM calls to avoid redundant API requests."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ResponseCache:
    """Caches LLM responses based on prompt hash.
    
    This allows reusing responses when iterating on frontend/UI changes
    without re-running expensive LLM inference calls.
    
    Cache Structure:
        {
            "prompt_hash": {
                "prompt": "full prompt text",
                "response": "llm response",
                "timestamp": "iso datetime",
                "model": "model identifier",
                "metadata": {...}
            }
        }
    """
    
    def __init__(self, cache_dir: str = "cache", enabled: bool = True):
        """Initialize response cache.
        
        Args:
            cache_dir: Directory to store cache file
            enabled: Whether caching is enabled (can be disabled via env var)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "llm_response_cache.json"
        self.enabled = enabled
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
    
    def _load_cache(self) -> None:
        """Load cache from disk."""
        if not self.cache_file.exists():
            self._cache = {}
            return
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                self._cache = json.load(f)
            logger.debug(f"📦 Loaded {len(self._cache)} cached responses")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            self._cache = {}
    
    def _save_cache(self) -> None:
        """Save cache to disk."""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
            logger.debug(f"💾 Saved {len(self._cache)} cached responses")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _compute_hash(self, prompt: str, model: str) -> str:
        """Compute hash for prompt + model combination.
        
        Args:
            prompt: The full prompt text
            model: Model identifier
        
        Returns:
            SHA256 hash as hex string
        """
        # Include both prompt and model in hash
        combined = f"{model}::{prompt}"
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()
    
    def get(self, prompt: str, model: str) -> Optional[str]:
        """Get cached response for prompt if available.
        
        Args:
            prompt: The prompt text
            model: Model identifier
        
        Returns:
            Cached response string or None if not found
        """
        if not self.enabled:
            return None
        
        prompt_hash = self._compute_hash(prompt, model)
        
        if prompt_hash in self._cache:
            cached = self._cache[prompt_hash]
            logger.info(f"🎯 Cache HIT for {model} (hash: {prompt_hash[:8]}...)")
            logger.debug(f"   Cached at: {cached.get('timestamp')}")
            return cached.get('response')
        
        logger.debug(f"📭 Cache MISS for {model} (hash: {prompt_hash[:8]}...)")
        return None
    
    def set(
        self,
        prompt: str,
        model: str,
        response: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Store response in cache.
        
        Args:
            prompt: The prompt text
            model: Model identifier
            response: The LLM response
            metadata: Optional metadata to store with cache entry
        """
        if not self.enabled:
            return
        
        prompt_hash = self._compute_hash(prompt, model)
        
        self._cache[prompt_hash] = {
            "prompt": prompt[:500] + "..." if len(prompt) > 500 else prompt,  # Truncate for readability
            "response": response,
            "timestamp": datetime.utcnow().isoformat(),
            "model": model,
            "metadata": metadata or {}
        }
        
        # Save to disk immediately (for persistence across runs)
        self._save_cache()
        logger.debug(f"💾 Cached response for {model} (hash: {prompt_hash[:8]}...)")
    
    def clear(self) -> int:
        """Clear all cached responses.
        
        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache = {}
        self._save_cache()
        logger.info(f"🗑️ Cleared {count} cached responses")
        return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        if not self._cache:
            return {
                "total_entries": 0,
                "cache_file": str(self.cache_file),
                "enabled": self.enabled
            }
        
        # Count by model
        by_model = {}
        for entry in self._cache.values():
            model = entry.get('model', 'unknown')
            by_model[model] = by_model.get(model, 0) + 1
        
        # Find oldest and newest
        timestamps = [
            datetime.fromisoformat(entry['timestamp'])
            for entry in self._cache.values()
            if 'timestamp' in entry
        ]
        
        return {
            "total_entries": len(self._cache),
            "by_model": by_model,
            "oldest": min(timestamps).isoformat() if timestamps else None,
            "newest": max(timestamps).isoformat() if timestamps else None,
            "cache_file": str(self.cache_file),
            "enabled": self.enabled
        }
    
    def prune_old_entries(self, days: int = 30) -> int:
        """Remove cache entries older than specified days.
        
        Args:
            days: Remove entries older than this many days
        
        Returns:
            Number of entries removed
        """
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        to_remove = []
        for prompt_hash, entry in self._cache.items():
            if 'timestamp' in entry:
                try:
                    entry_time = datetime.fromisoformat(entry['timestamp'])
                    if entry_time < cutoff:
                        to_remove.append(prompt_hash)
                except Exception:
                    pass
        
        for prompt_hash in to_remove:
            del self._cache[prompt_hash]
        
        if to_remove:
            self._save_cache()
            logger.info(f"🗑️ Pruned {len(to_remove)} old cache entries (older than {days} days)")
        
        return len(to_remove)

