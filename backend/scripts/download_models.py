#!/usr/bin/env python3
"""
Download ensemble preset models at Docker build time.

Bakes models into the image so they're available immediately at runtime,
avoiding cold-start model download latency in Cloud Run Jobs.

Usage: python backend/scripts/download_models.py /models
"""
import json
import sys
import logging
import importlib.resources as resources
from audio_separator.separator import Separator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Presets to bake into the GPU image
PRESETS_TO_DOWNLOAD = ["instrumental_clean", "karaoke"]


def download_preset_models(model_dir: str) -> None:
    """Download all models for the configured presets individually.

    Ensemble presets resolve to multiple model filenames. We must download
    each model individually via load_model(model_filename) — calling
    load_model() with no args on an ensemble preset doesn't trigger downloads.
    """
    with resources.open_text("audio_separator", "ensemble_presets.json") as f:
        presets = json.load(f)["presets"]

    models_to_download = set()
    for preset_name in PRESETS_TO_DOWNLOAD:
        models_to_download.update(presets[preset_name]["models"])

    logger.info(f"Downloading {len(models_to_download)} models for presets: {PRESETS_TO_DOWNLOAD}")
    for model in sorted(models_to_download):
        logger.info(f"  Downloading: {model}")
        sep = Separator(model_file_dir=model_dir)
        sep.load_model(model)
        logger.info(f"  Done: {model}")


if __name__ == "__main__":
    model_dir = sys.argv[1] if len(sys.argv) > 1 else "/models"
    download_preset_models(model_dir)
    logger.info(f"All preset models downloaded to {model_dir}")
