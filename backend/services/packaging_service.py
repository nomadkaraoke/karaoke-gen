"""
Packaging Service.

Provides CDG and TXT package generation functionality, extracted from KaraokeFinalise
for use by both the cloud backend (video_worker) and local CLI.

This service handles:
- CDG (CD+G) file generation from LRC files
- TXT lyric file generation from LRC files
- ZIP packaging of CDG/MP3 and TXT/MP3 pairs
"""

import logging
import os
import zipfile
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class PackagingService:
    """
    Service for creating CDG and TXT karaoke packages.

    CDG (CD+Graphics) is a format used by karaoke machines.
    TXT packages are used by software karaoke players.
    Both formats are packaged as ZIP files with an MP3 audio track.
    """

    def __init__(
        self,
        cdg_styles: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        non_interactive: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the packaging service.

        Args:
            cdg_styles: CDG style configuration for CDG generation
            dry_run: If True, log actions without performing them
            non_interactive: If True, skip interactive prompts
            logger: Optional logger instance
        """
        self.cdg_styles = cdg_styles
        self.dry_run = dry_run
        self.non_interactive = non_interactive
        self.logger = logger or logging.getLogger(__name__)

    def create_cdg_package(
        self,
        lrc_file: str,
        audio_file: str,
        output_zip_path: str,
        artist: str,
        title: str,
        output_mp3_path: Optional[str] = None,
        output_cdg_path: Optional[str] = None,
        lrc_has_countdown_padding: bool = False,
        countdown_padding_seconds: float = 3.0,
        is_duet: bool = False,
        duet_corrections_json_path: Optional[str] = None,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Create a CDG package (ZIP containing CDG and MP3 files).

        Args:
            lrc_file: Path to the LRC lyrics file
            audio_file: Path to the instrumental audio file
            output_zip_path: Path for the output ZIP file
            artist: Artist name for metadata
            title: Song title for metadata
            output_mp3_path: Optional path for the extracted MP3 file
            output_cdg_path: Optional path for the extracted CDG file
            lrc_has_countdown_padding: If True, LRC contains countdown-shifted timestamps
                that need adjustment since CDG uses instrumental audio without padding
            countdown_padding_seconds: Duration of countdown padding to compensate for

        Returns:
            Tuple of (zip_path, mp3_path, cdg_path)

        Raises:
            ValueError: If CDG styles are not configured
            FileNotFoundError: If input files are missing
            Exception: If CDG generation fails
        """
        self.logger.info(f"Creating CDG package for {artist} - {title}")

        # Validate inputs
        if not os.path.isfile(lrc_file):
            raise FileNotFoundError(f"LRC file not found: {lrc_file}")
        if not os.path.isfile(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        # Check if ZIP already exists
        if os.path.isfile(output_zip_path):
            if self.non_interactive:
                self.logger.info(
                    f"CDG ZIP exists, will be overwritten: {output_zip_path}"
                )
            else:
                self.logger.info(f"CDG ZIP already exists: {output_zip_path}")

        # Check if individual files exist (allows skipping generation)
        if output_mp3_path and output_cdg_path:
            if os.path.isfile(output_mp3_path) and os.path.isfile(output_cdg_path):
                self.logger.info("Found existing MP3 and CDG files, creating ZIP directly")
                if not self.dry_run:
                    self._create_zip_from_files(
                        output_zip_path,
                        [(output_mp3_path, os.path.basename(output_mp3_path)),
                         (output_cdg_path, os.path.basename(output_cdg_path))]
                    )
                return output_zip_path, output_mp3_path, output_cdg_path

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would generate CDG package: {output_zip_path}"
            )
            return output_zip_path, output_mp3_path, output_cdg_path

        # Generate CDG files
        if self.cdg_styles is None:
            raise ValueError(
                "CDG styles configuration is required for CDG generation"
            )

        self.logger.info("Generating CDG and MP3 files")
        from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator

        output_dir = os.path.dirname(output_zip_path) or os.getcwd()

        # Duet-aware CDG generation: when is_duet is True AND we have a
        # corrections JSON with per-segment singer tags, use the segment-
        # based generate_cdg path so the TOML gets the 3-singer palette
        # and per-line `N|text` prefixes. The LRC-based path has no singer
        # information and would always render solo colors.
        duet_segments = None
        if is_duet:
            duet_segments = self._load_duet_segments(
                duet_corrections_json_path,
                countdown_padding_seconds=countdown_padding_seconds if lrc_has_countdown_padding else 0,
            )

        if duet_segments is not None:
            self.logger.info(
                f"Generating CDG in duet mode from {len(duet_segments)} reviewed segments"
            )
            generator = CDGGenerator(output_dir=output_dir, logger=self.logger, is_duet=True)
            cdg_file, mp3_file, zip_file = generator.generate_cdg(
                segments=duet_segments,
                audio_file=audio_file,
                title=title,
                artist=artist,
                cdg_styles=self.cdg_styles,
            )
        else:
            if is_duet:
                self.logger.warning(
                    "is_duet=True but no usable duet corrections JSON — falling back to LRC-based CDG (solo colors)"
                )
            generator = CDGGenerator(output_dir=output_dir, logger=self.logger)
            cdg_file, mp3_file, zip_file = generator.generate_cdg_from_lrc(
                lrc_file=lrc_file,
                audio_file=audio_file,
                title=title,
                artist=artist,
                cdg_styles=self.cdg_styles,
                lrc_has_countdown_padding=lrc_has_countdown_padding,
                countdown_padding_seconds=countdown_padding_seconds,
            )

        # Rename ZIP to expected output path if different
        if os.path.isfile(zip_file) and zip_file != output_zip_path:
            os.rename(zip_file, output_zip_path)
            self.logger.info(f"Renamed CDG ZIP: {zip_file} -> {output_zip_path}")

        if not os.path.isfile(output_zip_path):
            raise Exception(f"Failed to create CDG ZIP file: {output_zip_path}")

        # Extract the ZIP to get individual files if paths provided
        extracted_mp3 = None
        extracted_cdg = None
        if output_mp3_path or output_cdg_path:
            self.logger.info(f"Extracting CDG ZIP file: {output_zip_path}")
            with zipfile.ZipFile(output_zip_path, "r") as zip_ref:
                zip_ref.extractall(output_dir)

            # Find extracted files
            if output_mp3_path and os.path.isfile(output_mp3_path):
                extracted_mp3 = output_mp3_path
                self.logger.info(f"Extracted MP3: {extracted_mp3}")
            if output_cdg_path and os.path.isfile(output_cdg_path):
                extracted_cdg = output_cdg_path
                self.logger.info(f"Extracted CDG: {extracted_cdg}")

        self.logger.info(f"CDG package created: {output_zip_path}")
        return output_zip_path, extracted_mp3, extracted_cdg

    def create_txt_package(
        self,
        lrc_file: str,
        mp3_file: str,
        output_zip_path: str,
        output_txt_path: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Create a TXT package (ZIP containing TXT lyrics and MP3 files).

        Args:
            lrc_file: Path to the LRC lyrics file
            mp3_file: Path to the MP3 audio file
            output_zip_path: Path for the output ZIP file
            output_txt_path: Optional path for the generated TXT file

        Returns:
            Tuple of (zip_path, txt_path)

        Raises:
            FileNotFoundError: If input files are missing
            Exception: If TXT generation fails
        """
        self.logger.info(f"Creating TXT package from {lrc_file}")

        # Validate inputs
        if not os.path.isfile(lrc_file):
            raise FileNotFoundError(f"LRC file not found: {lrc_file}")
        if not os.path.isfile(mp3_file):
            raise FileNotFoundError(f"MP3 file not found: {mp3_file}")

        # Check if ZIP already exists
        if os.path.isfile(output_zip_path):
            if self.non_interactive:
                self.logger.info(
                    f"TXT ZIP exists, will be overwritten: {output_zip_path}"
                )
            else:
                self.logger.info(f"TXT ZIP already exists: {output_zip_path}")

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would create TXT package: {output_zip_path}"
            )
            return output_zip_path, output_txt_path

        # Generate TXT from LRC
        self.logger.info(f"Converting LRC to TXT format: {lrc_file}")
        from lyrics_converter import LyricsConverter

        txt_converter = LyricsConverter(output_format="txt", filepath=lrc_file)
        converted_txt = txt_converter.convert_file()

        # Write TXT file
        if output_txt_path is None:
            # Default to same name as ZIP but with .txt extension
            output_txt_path = output_zip_path.replace(".zip", ".txt")

        with open(output_txt_path, "w") as txt_file:
            txt_file.write(converted_txt)
            self.logger.info(f"TXT file written: {output_txt_path}")

        # Create ZIP containing MP3 and TXT
        self.logger.info(f"Creating TXT ZIP: {output_zip_path}")
        self._create_zip_from_files(
            output_zip_path,
            [(mp3_file, os.path.basename(mp3_file)),
             (output_txt_path, os.path.basename(output_txt_path))]
        )

        if not os.path.isfile(output_zip_path):
            raise Exception(f"Failed to create TXT ZIP file: {output_zip_path}")

        self.logger.info(f"TXT package created: {output_zip_path}")
        return output_zip_path, output_txt_path

    def _load_duet_segments(
        self,
        corrections_json_path: Optional[str],
        countdown_padding_seconds: float = 0,
    ):
        """Load reviewed segments with per-segment singer tags for duet CDG.

        Returns None when no usable JSON is available — caller falls back to
        the LRC-based (solo) CDG path. When countdown_padding_seconds > 0,
        shifts all timings back so CDG (which renders against the unpadded
        instrumental) stays in sync with the audio.
        """
        if not corrections_json_path or not os.path.isfile(corrections_json_path):
            self.logger.info(
                f"Duet corrections JSON not available at {corrections_json_path!r}"
            )
            return None
        try:
            import json as _json
            from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
            with open(corrections_json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            # corrections_updated.json can be either {corrections: {corrected_segments: [...]}}
            # or {corrected_segments: [...]} depending on the writer; support both.
            inner = data.get("corrections") if isinstance(data.get("corrections"), dict) else data
            raw_segments = inner.get("corrected_segments") or inner.get("segments") or []
            if not raw_segments:
                self.logger.warning(
                    f"Duet corrections JSON had no corrected_segments: {corrections_json_path}"
                )
                return None
            segments = [LyricsSegment.from_dict(s) for s in raw_segments]

            pad = countdown_padding_seconds or 0
            if pad and pad > 0:
                self.logger.info(
                    f"Shifting duet segments back by {pad}s to align with unpadded CDG instrumental"
                )
                shifted = []
                for seg in segments:
                    new_start = max(0.0, seg.start_time - pad)
                    new_end = max(new_start, seg.end_time - pad)
                    new_words = [
                        Word(
                            id=w.id,
                            text=w.text,
                            start_time=max(0.0, w.start_time - pad),
                            end_time=max(0.0, w.end_time - pad),
                            singer=w.singer,
                            confidence=w.confidence,
                            created_during_correction=w.created_during_correction,
                        )
                        for w in seg.words
                    ]
                    shifted.append(LyricsSegment(
                        id=seg.id,
                        text=seg.text,
                        words=new_words,
                        start_time=new_start,
                        end_time=new_end,
                        singer=seg.singer,
                    ))
                segments = [s for s in shifted if s.end_time > 0]
            return segments
        except Exception as exc:
            self.logger.warning(
                f"Failed to load duet segments from {corrections_json_path}: {exc} — falling back to LRC path"
            )
            return None

    def _create_zip_from_files(
        self,
        zip_path: str,
        files: list,
    ) -> None:
        """
        Create a ZIP file from a list of files.

        Args:
            zip_path: Path for the output ZIP file
            files: List of (file_path, archive_name) tuples
        """
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file_path, archive_name in files:
                if os.path.isfile(file_path):
                    zipf.write(file_path, archive_name)
                    self.logger.debug(f"Added to ZIP: {archive_name}")
                else:
                    self.logger.warning(f"File not found for ZIP: {file_path}")


# Singleton instance and factory function (following existing service pattern)
_packaging_service: Optional[PackagingService] = None


def get_packaging_service(
    cdg_styles: Optional[Dict[str, Any]] = None,
    **kwargs
) -> PackagingService:
    """
    Get a packaging service instance.

    Args:
        cdg_styles: CDG style configuration
        **kwargs: Additional arguments passed to PackagingService

    Returns:
        PackagingService instance
    """
    global _packaging_service

    # Create new instance if settings changed
    if _packaging_service is None or cdg_styles:
        _packaging_service = PackagingService(
            cdg_styles=cdg_styles,
            **kwargs
        )

    return _packaging_service
