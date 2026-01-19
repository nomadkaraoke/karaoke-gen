"""
Tests for PackagingService.

Tests cover:
- Service initialization
- CDG package creation
- TXT package creation
- Dry run mode
- Error handling
"""

import os
import zipfile
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from backend.services.packaging_service import (
    PackagingService,
    get_packaging_service,
)


class TestPackagingServiceInit:
    """Test service initialization."""

    def test_init_default_values(self):
        """Test default initialization."""
        service = PackagingService()
        assert service.cdg_styles is None
        assert service.dry_run is False
        assert service.non_interactive is False

    def test_init_with_cdg_styles(self):
        """Test initialization with CDG styles."""
        styles = {"font_size": 24, "bg_color": "black"}
        service = PackagingService(cdg_styles=styles)
        assert service.cdg_styles == styles

    def test_init_with_dry_run(self):
        """Test initialization with dry run mode."""
        service = PackagingService(dry_run=True)
        assert service.dry_run is True

    def test_init_with_non_interactive(self):
        """Test initialization with non-interactive mode."""
        service = PackagingService(non_interactive=True)
        assert service.non_interactive is True


class TestPackagingServiceCreateZipFromFiles:
    """Test internal ZIP creation."""

    def test_create_zip_from_files(self):
        """Test creating a ZIP file from multiple files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            file1 = os.path.join(tmpdir, "test1.txt")
            file2 = os.path.join(tmpdir, "test2.txt")
            zip_path = os.path.join(tmpdir, "output.zip")

            with open(file1, "w") as f:
                f.write("content1")
            with open(file2, "w") as f:
                f.write("content2")

            service = PackagingService()
            service._create_zip_from_files(
                zip_path,
                [(file1, "test1.txt"), (file2, "test2.txt")]
            )

            assert os.path.isfile(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "test1.txt" in names
                assert "test2.txt" in names


class TestPackagingServiceCDGPackage:
    """Test CDG package creation."""

    def test_create_cdg_package_missing_lrc_file(self):
        """Test that missing LRC file raises FileNotFoundError."""
        service = PackagingService()

        with pytest.raises(FileNotFoundError) as exc_info:
            service.create_cdg_package(
                lrc_file="/nonexistent/file.lrc",
                audio_file="/some/audio.flac",
                output_zip_path="/output/test.zip",
                artist="Test Artist",
                title="Test Title",
            )
        assert "LRC file not found" in str(exc_info.value)

    def test_create_cdg_package_missing_audio_file(self):
        """Test that missing audio file raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")

            service = PackagingService()

            with pytest.raises(FileNotFoundError) as exc_info:
                service.create_cdg_package(
                    lrc_file=lrc_file,
                    audio_file="/nonexistent/audio.flac",
                    output_zip_path="/output/test.zip",
                    artist="Test Artist",
                    title="Test Title",
                )
            assert "Audio file not found" in str(exc_info.value)

    def test_create_cdg_package_no_styles_raises(self):
        """Test that CDG generation without styles raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            audio_file = os.path.join(tmpdir, "test.flac")
            zip_path = os.path.join(tmpdir, "output.zip")

            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(audio_file, "w") as f:
                f.write("fake audio")

            # Service without CDG styles
            service = PackagingService(cdg_styles=None)

            with pytest.raises(ValueError) as exc_info:
                service.create_cdg_package(
                    lrc_file=lrc_file,
                    audio_file=audio_file,
                    output_zip_path=zip_path,
                    artist="Test Artist",
                    title="Test Title",
                )
            assert "CDG styles configuration is required" in str(exc_info.value)

    def test_create_cdg_package_dry_run(self):
        """Test CDG package creation in dry run mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            audio_file = os.path.join(tmpdir, "test.flac")
            zip_path = os.path.join(tmpdir, "output.zip")

            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(audio_file, "w") as f:
                f.write("fake audio")

            service = PackagingService(dry_run=True)

            result = service.create_cdg_package(
                lrc_file=lrc_file,
                audio_file=audio_file,
                output_zip_path=zip_path,
                artist="Test Artist",
                title="Test Title",
            )

            # In dry run, ZIP should not be created
            assert not os.path.isfile(zip_path)
            assert result[0] == zip_path

    def test_create_cdg_package_existing_files(self):
        """Test CDG package creation with existing MP3 and CDG files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            audio_file = os.path.join(tmpdir, "test.flac")
            mp3_file = os.path.join(tmpdir, "test.mp3")
            cdg_file = os.path.join(tmpdir, "test.cdg")
            zip_path = os.path.join(tmpdir, "output.zip")

            # Create all files
            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(audio_file, "w") as f:
                f.write("fake audio")
            with open(mp3_file, "w") as f:
                f.write("fake mp3")
            with open(cdg_file, "w") as f:
                f.write("fake cdg")

            service = PackagingService()

            result = service.create_cdg_package(
                lrc_file=lrc_file,
                audio_file=audio_file,
                output_zip_path=zip_path,
                artist="Test Artist",
                title="Test Title",
                output_mp3_path=mp3_file,
                output_cdg_path=cdg_file,
            )

            # ZIP should be created from existing files
            assert os.path.isfile(zip_path)
            assert result[0] == zip_path
            assert result[1] == mp3_file
            assert result[2] == cdg_file

    @patch("karaoke_gen.lyrics_transcriber.output.cdg.CDGGenerator")
    def test_create_cdg_package_with_generator(self, mock_cdg_generator_class):
        """Test CDG package creation using CDGGenerator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            audio_file = os.path.join(tmpdir, "test.flac")
            zip_path = os.path.join(tmpdir, "output.zip")
            generated_zip = os.path.join(tmpdir, "generated.zip")

            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(audio_file, "w") as f:
                f.write("fake audio")

            # Create the zip file that would be generated
            with zipfile.ZipFile(generated_zip, "w") as zf:
                zf.writestr("test.mp3", "fake mp3")
                zf.writestr("test.cdg", "fake cdg")

            mock_generator = MagicMock()
            mock_generator.generate_cdg_from_lrc.return_value = (
                "test.cdg", "test.mp3", generated_zip
            )
            mock_cdg_generator_class.return_value = mock_generator

            styles = {"font_size": 24}
            service = PackagingService(cdg_styles=styles)

            result = service.create_cdg_package(
                lrc_file=lrc_file,
                audio_file=audio_file,
                output_zip_path=zip_path,
                artist="Test Artist",
                title="Test Title",
            )

            mock_generator.generate_cdg_from_lrc.assert_called_once_with(
                lrc_file=lrc_file,
                audio_file=audio_file,
                title="Test Title",
                artist="Test Artist",
                cdg_styles=styles,
            )
            assert os.path.isfile(zip_path)


class TestPackagingServiceTXTPackage:
    """Test TXT package creation."""

    def test_create_txt_package_missing_lrc_file(self):
        """Test that missing LRC file raises FileNotFoundError."""
        service = PackagingService()

        with pytest.raises(FileNotFoundError) as exc_info:
            service.create_txt_package(
                lrc_file="/nonexistent/file.lrc",
                mp3_file="/some/audio.mp3",
                output_zip_path="/output/test.zip",
            )
        assert "LRC file not found" in str(exc_info.value)

    def test_create_txt_package_missing_mp3_file(self):
        """Test that missing MP3 file raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")

            service = PackagingService()

            with pytest.raises(FileNotFoundError) as exc_info:
                service.create_txt_package(
                    lrc_file=lrc_file,
                    mp3_file="/nonexistent/audio.mp3",
                    output_zip_path="/output/test.zip",
                )
            assert "MP3 file not found" in str(exc_info.value)

    def test_create_txt_package_dry_run(self):
        """Test TXT package creation in dry run mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            mp3_file = os.path.join(tmpdir, "test.mp3")
            zip_path = os.path.join(tmpdir, "output.zip")

            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(mp3_file, "w") as f:
                f.write("fake mp3")

            service = PackagingService(dry_run=True)

            result = service.create_txt_package(
                lrc_file=lrc_file,
                mp3_file=mp3_file,
                output_zip_path=zip_path,
            )

            # In dry run, ZIP should not be created
            assert not os.path.isfile(zip_path)
            assert result[0] == zip_path

    @patch("lyrics_converter.LyricsConverter")
    def test_create_txt_package_success(self, mock_converter_class):
        """Test successful TXT package creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_file = os.path.join(tmpdir, "test.lrc")
            mp3_file = os.path.join(tmpdir, "test.mp3")
            zip_path = os.path.join(tmpdir, "output.zip")
            txt_path = os.path.join(tmpdir, "output.txt")

            with open(lrc_file, "w") as f:
                f.write("[00:00.00]Test lyrics")
            with open(mp3_file, "w") as f:
                f.write("fake mp3")

            mock_converter = MagicMock()
            mock_converter.convert_file.return_value = "Converted lyrics text"
            mock_converter_class.return_value = mock_converter

            service = PackagingService()

            result = service.create_txt_package(
                lrc_file=lrc_file,
                mp3_file=mp3_file,
                output_zip_path=zip_path,
                output_txt_path=txt_path,
            )

            mock_converter_class.assert_called_once_with(
                output_format="txt",
                filepath=lrc_file
            )
            mock_converter.convert_file.assert_called_once()

            assert os.path.isfile(zip_path)
            assert os.path.isfile(txt_path)
            assert result[0] == zip_path
            assert result[1] == txt_path

            # Verify ZIP contents
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "test.mp3" in names
                assert "output.txt" in names


class TestGetPackagingService:
    """Test factory function."""

    def test_get_service_creates_instance(self):
        """Test that factory function creates a new instance."""
        import backend.services.packaging_service as module
        module._packaging_service = None

        service = get_packaging_service()

        assert service is not None
        assert isinstance(service, PackagingService)

    def test_get_service_with_cdg_styles(self):
        """Test factory function with CDG styles."""
        import backend.services.packaging_service as module
        module._packaging_service = None

        styles = {"font_size": 24}
        service = get_packaging_service(cdg_styles=styles)

        assert service.cdg_styles == styles

    def test_get_service_with_options(self):
        """Test factory function with additional options."""
        import backend.services.packaging_service as module
        module._packaging_service = None

        service = get_packaging_service(dry_run=True, non_interactive=True)

        assert service.dry_run is True
        assert service.non_interactive is True
