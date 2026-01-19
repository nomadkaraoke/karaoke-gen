#!/usr/bin/env python3

import logging
import argparse
import json
import sys
from pathlib import Path

from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator

logger = logging.getLogger(__name__)


def cli_main():
    """Command-line interface entry point for the lrc2cdg tool."""
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Convert LRC file to CDG")
    parser.add_argument("lrc_file", help="Path to the LRC file")
    parser.add_argument("audio_file", help="Path to the audio file")
    parser.add_argument("--title", required=True, help="Title of the song")
    parser.add_argument("--artist", required=True, help="Artist of the song")
    parser.add_argument("--style_params_json", required=True, help="Path to JSON file containing CDG style configuration")

    args = parser.parse_args()

    try:
        with open(args.style_params_json, "r") as f:
            style_params = json.loads(f.read())
            cdg_styles = style_params["cdg"]
    except FileNotFoundError:
        logger.error(f"Style configuration file not found: {args.style_params_json}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in style configuration file: {e}")
        sys.exit(1)

    try:
        output_dir = str(Path(args.lrc_file).parent)
        generator = CDGGenerator(output_dir=output_dir, logger=logger)

        cdg_file, mp3_file, zip_file = generator.generate_cdg_from_lrc(
            lrc_file=args.lrc_file,
            audio_file=args.audio_file,
            title=args.title,
            artist=args.artist,
            cdg_styles=cdg_styles,
        )

        logger.info(f"Generated files:\nCDG: {cdg_file}\nMP3: {mp3_file}\nZIP: {zip_file}")

    except ValueError as e:
        logger.error(f"Invalid style configuration: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error generating CDG: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
