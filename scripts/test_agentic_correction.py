#!/usr/bin/env python3
"""
Test script for the agentic correction workflow.

This script allows testing the agentic correction locally without deploying
to Cloud Run or running a full karaoke job.

Usage:
    # Test with default Vertex AI (requires gcloud auth):
    python scripts/test_agentic_correction.py

    # Test with a specific model:
    AGENTIC_AI_MODEL=openai/gpt-4o python scripts/test_agentic_correction.py
    AGENTIC_AI_MODEL=anthropic/claude-3-5-sonnet-20241022 python scripts/test_agentic_correction.py

    # Test with local Ollama:
    PRIVACY_MODE=1 python scripts/test_agentic_correction.py

Environment variables:
    GOOGLE_CLOUD_PROJECT: GCP project ID (required for Vertex AI)
    GCP_LOCATION: Vertex AI location (default: global)
    AGENTIC_AI_MODEL: Override the model (e.g., openai/gpt-4o)
    PRIVACY_MODE: Set to 1 to use local Ollama
    LANGFUSE_PUBLIC_KEY: Langfuse public key for tracing
    LANGFUSE_SECRET_KEY: Langfuse secret key for tracing
"""

import os
import sys
import json
import logging
from typing import List, Dict, Any

# Add the project to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Sample test cases representing different gap categories
TEST_CASES = [
    {
        "name": "Sound-alike error (wurld -> world)",
        "gap_id": "test_gap_1",
        "gap_words": [
            {"id": "w1", "text": "wurld", "start_time": 0.5, "end_time": 1.0}
        ],
        "preceding_words": "hello",
        "following_words": "this is a test",
        "reference_contexts": {
            "genius": "hello world this is a test",
            "lrclib": "Hello world, this is a test"
        },
        "artist": "Test Artist",
        "title": "Test Song"
    },
    {
        "name": "Punctuation difference",
        "gap_id": "test_gap_2",
        "gap_words": [
            {"id": "w2", "text": "Hello", "start_time": 0.0, "end_time": 0.5}
        ],
        "preceding_words": "",
        "following_words": "is it me you're looking for",
        "reference_contexts": {
            "genius": "Hello, is it me you're looking for?"
        },
        "artist": "Lionel Richie",
        "title": "Hello"
    },
    {
        "name": "Extra filler word",
        "gap_id": "test_gap_3",
        "gap_words": [
            {"id": "w3", "text": "And", "start_time": 2.0, "end_time": 2.2},
            {"id": "w4", "text": "I", "start_time": 2.2, "end_time": 2.4},
            {"id": "w5", "text": "love", "start_time": 2.4, "end_time": 2.6},
            {"id": "w6", "text": "you", "start_time": 2.6, "end_time": 2.8}
        ],
        "preceding_words": "baby",
        "following_words": "so much",
        "reference_contexts": {
            "genius": "baby I love you so much"
        },
        "artist": "Test Artist",
        "title": "Love Song"
    },
    {
        "name": "Complex multi-word error",
        "gap_id": "test_gap_4",
        "gap_words": [
            {"id": "w7", "text": "gonna", "start_time": 3.0, "end_time": 3.2},
            {"id": "w8", "text": "give", "start_time": 3.2, "end_time": 3.4},
            {"id": "w9", "text": "you", "start_time": 3.4, "end_time": 3.6},
            {"id": "w10", "text": "up", "start_time": 3.6, "end_time": 3.8}
        ],
        "preceding_words": "never",
        "following_words": "never gonna let you down",
        "reference_contexts": {
            "genius": "Never gonna give you up, never gonna let you down"
        },
        "artist": "Rick Astley",
        "title": "Never Gonna Give You Up"
    }
]


def run_single_test(test_case: Dict[str, Any], agent) -> Dict[str, Any]:
    """Run a single test case and return results."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Test: {test_case['name']}")
    logger.info(f"Gap words: {[w['text'] for w in test_case['gap_words']]}")
    logger.info(f"Reference: {list(test_case['reference_contexts'].values())[0][:50]}...")
    logger.info(f"{'='*60}")

    try:
        proposals = agent.propose_for_gap(
            gap_id=test_case["gap_id"],
            gap_words=test_case["gap_words"],
            preceding_words=test_case["preceding_words"],
            following_words=test_case["following_words"],
            reference_contexts=test_case["reference_contexts"],
            artist=test_case.get("artist"),
            title=test_case.get("title")
        )

        result = {
            "test_name": test_case["name"],
            "success": True,
            "proposals": []
        }

        if not proposals:
            logger.info("No proposals returned (gap may be correct)")
            result["proposals"] = []
        else:
            for p in proposals:
                proposal_dict = {
                    "action": p.action,
                    "word_id": p.word_id,
                    "word_ids": p.word_ids,
                    "replacement_text": p.replacement_text,
                    "confidence": p.confidence,
                    "reason": p.reason,
                    "gap_category": str(p.gap_category) if p.gap_category else None,
                    "requires_human_review": p.requires_human_review
                }
                result["proposals"].append(proposal_dict)

                logger.info(f"\nProposal:")
                logger.info(f"  Action: {p.action}")
                logger.info(f"  Word ID(s): {p.word_id or p.word_ids}")
                if p.replacement_text:
                    logger.info(f"  Replacement: {p.replacement_text}")
                logger.info(f"  Confidence: {p.confidence:.2f}")
                logger.info(f"  Category: {p.gap_category}")
                logger.info(f"  Reason: {p.reason}")
                if p.requires_human_review:
                    logger.info(f"  ⚠️  Requires human review")

        return result

    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "test_name": test_case["name"],
            "success": False,
            "error": str(e)
        }


def main():
    """Main entry point."""
    # Check environment
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    location = os.getenv("GCP_LOCATION", "global")
    model_override = os.getenv("AGENTIC_AI_MODEL")
    privacy_mode = os.getenv("PRIVACY_MODE", "").lower() in ("1", "true", "yes")

    logger.info("=" * 60)
    logger.info("AGENTIC CORRECTION TEST SCRIPT")
    logger.info("=" * 60)
    logger.info(f"GCP Project: {project_id or 'not set'}")
    logger.info(f"GCP Location: {location}")
    logger.info(f"Model Override: {model_override or 'none (using default)'}")
    logger.info(f"Privacy Mode: {privacy_mode}")
    logger.info(f"Langfuse: {'enabled' if os.getenv('LANGFUSE_PUBLIC_KEY') else 'disabled'}")

    # Import after path setup
    try:
        from karaoke_gen.lyrics_transcriber.correction.agentic.agent import AgenticCorrector
        from karaoke_gen.lyrics_transcriber.correction.agentic.router import ModelRouter
        from karaoke_gen.lyrics_transcriber.correction.agentic.providers.config import ProviderConfig
    except ImportError as e:
        logger.error(f"Failed to import agentic correction modules: {e}")
        logger.error("Make sure you're running from the karaoke-gen directory")
        sys.exit(1)

    # Determine which model to use
    config = ProviderConfig.from_env()
    router = ModelRouter(config)
    model = router.choose_model("unknown", 0.5)
    logger.info(f"Selected model: {model}")

    # Validate environment for Vertex AI
    if model.startswith("vertexai/") and not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT environment variable is required for Vertex AI")
        logger.error("Set it with: export GOOGLE_CLOUD_PROJECT=your-project-id")
        logger.error("Or use a different model: AGENTIC_AI_MODEL=openai/gpt-4o")
        sys.exit(1)

    # Create the agent
    logger.info(f"\nCreating AgenticCorrector with model: {model}")
    try:
        agent = AgenticCorrector.from_model(
            model=model,
            session_id="test_session_local",
            cache_dir=os.path.expanduser("~/lyrics-transcriber-cache")
        )
        logger.info("Agent created successfully!")
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Run test cases
    results = []
    for test_case in TEST_CASES:
        result = run_single_test(test_case, agent)
        results.append(result)

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)

    passed = sum(1 for r in results if r["success"])
    failed = len(results) - passed

    for r in results:
        status = "✅ PASS" if r["success"] else "❌ FAIL"
        logger.info(f"{status}: {r['test_name']}")
        if not r["success"]:
            logger.info(f"       Error: {r.get('error', 'unknown')}")

    logger.info(f"\nResults: {passed}/{len(results)} passed")

    # Output JSON results
    output_file = "agentic_test_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"\nDetailed results saved to: {output_file}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
