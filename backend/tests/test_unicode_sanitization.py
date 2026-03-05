"""
Comprehensive tests for Unicode and special character handling.

This test file verifies that artist/title with Unicode characters (curly quotes,
em dashes, non-ASCII characters) are properly sanitized throughout the codebase
to prevent:
1. HTTP header encoding failures (Content-Disposition, email subjects)
2. API query failures (Google Drive)
3. Filename encoding issues (Modal API, filesystem)

The root cause was job d49efab1 which had title "Mama Says (You Can't Back Down)"
with a curly apostrophe (U+2019) that caused the audio separation to fail because
the Modal API couldn't handle the non-latin-1 character in HTTP headers.
"""

import pytest
from karaoke_gen.utils import (
    sanitize_filename,
    normalize_text,
    UNICODE_REPLACEMENTS,
    TEXT_NORMALIZATIONS,
    APOSTROPHE_REPLACEMENTS,
    DOUBLE_QUOTE_REPLACEMENTS,
    DASH_REPLACEMENTS,
    WHITESPACE_REPLACEMENTS,
)


class TestSanitizeFilename:
    """Test the sanitize_filename function handles all edge cases."""

    def test_curly_single_quotes(self):
        """Test that curly single quotes are converted to straight quotes."""
        # The exact character that caused job d49efab1 to fail
        assert sanitize_filename("Can't") == "Can't"
        assert sanitize_filename("It's") == "It's"
        # Left single quote
        assert sanitize_filename("'Hello'") == "'Hello'"

    def test_curly_double_quotes(self):
        """Test that curly double quotes are converted to underscores.

        Note: Double quotes are filesystem-unsafe so they become underscores."""
        # Curly quotes first become straight, then straight quotes become underscore
        assert sanitize_filename("\u201cHello\u201d") == "_Hello_"

    def test_em_dash(self):
        """Test that em dashes are converted to regular hyphens."""
        assert sanitize_filename("Artist — Title") == "Artist - Title"
        assert sanitize_filename("Song—Name") == "Song-Name"

    def test_en_dash(self):
        """Test that en dashes are converted to regular hyphens."""
        assert sanitize_filename("1990–2000") == "1990-2000"

    def test_ellipsis(self):
        """Test that horizontal ellipsis is converted to three dots."""
        # Note: The ellipsis becomes "..." but trailing dots are stripped
        assert sanitize_filename("Wait\u2026") == "Wait"
        # But in the middle of a string they're preserved
        assert sanitize_filename("Wait\u2026Here") == "Wait...Here"

    def test_non_breaking_space(self):
        """Test that non-breaking spaces are converted to regular spaces."""
        # U+00A0 is non-breaking space
        assert sanitize_filename("Hello\u00A0World") == "Hello World"

    def test_filesystem_unsafe_characters(self):
        """Test that filesystem-unsafe characters are replaced."""
        assert sanitize_filename("file/name") == "file_name"
        assert sanitize_filename("file\\name") == "file_name"
        assert sanitize_filename("file:name") == "file_name"
        assert sanitize_filename("file*name") == "file_name"
        assert sanitize_filename("file?name") == "file_name"
        assert sanitize_filename('file"name') == "file_name"
        assert sanitize_filename("file<name") == "file_name"
        assert sanitize_filename("file>name") == "file_name"
        assert sanitize_filename("file|name") == "file_name"

    def test_trailing_periods_and_spaces(self):
        """Test that trailing periods and spaces are removed."""
        assert sanitize_filename("filename.") == "filename"
        assert sanitize_filename("filename...") == "filename"
        assert sanitize_filename("filename ") == "filename"
        assert sanitize_filename("filename . ") == "filename"

    def test_leading_periods_and_spaces(self):
        """Test that leading periods and spaces are removed."""
        assert sanitize_filename(".filename") == "filename"
        assert sanitize_filename("...filename") == "filename"
        assert sanitize_filename(" filename") == "filename"
        assert sanitize_filename(" . filename") == "filename"

    def test_multiple_underscores_collapsed(self):
        """Test that multiple consecutive underscores are collapsed to one."""
        assert sanitize_filename("file___name") == "file_name"
        # Multiple unsafe chars in a row
        assert sanitize_filename("file?*:name") == "file_name"

    def test_multiple_spaces_collapsed(self):
        """Test that multiple consecutive spaces are collapsed to one."""
        assert sanitize_filename("file   name") == "file name"

    def test_none_input(self):
        """Test that None input returns None."""
        assert sanitize_filename(None) is None

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert sanitize_filename("") == ""

    def test_real_world_examples(self):
        """Test real-world examples that have caused issues."""
        # The exact title from job d49efab1
        assert sanitize_filename("Mama Says (You Can't Back Down)") == "Mama Says (You Can't Back Down)"

        # Broadway cast with smart quotes
        assert sanitize_filename("Footloose (Broadway Cast) \u2014 \u201cFinal Song\u201d") == "Footloose (Broadway Cast) - _Final Song_"

        # Japanese artist with em dash
        assert sanitize_filename("宇多田ヒカル — First Love") == "宇多田ヒカル - First Love"

        # Korean title (should pass through unchanged)
        assert sanitize_filename("아이유 - 좋은 날") == "아이유 - 좋은 날"

        # Mixed content
        assert sanitize_filename("L'Arc～en～Ciel - Driver's High") == "L'Arc～en～Ciel - Driver's High"

    def test_combination_of_issues(self):
        """Test strings with multiple problematic characters."""
        result = sanitize_filename("It\u2019s \u201cMy\u201d Song \u2014 Volume\u20261")
        # Curly ' -> ', curly " -> _ (via filesystem check), em dash -> -, ellipsis -> ...
        assert result == "It's _My_ Song - Volume...1"

    def test_unicode_replacements_dict_complete(self):
        """Verify all expected Unicode characters are in the replacements dict."""
        # Curly quotes
        assert "\u2018" in UNICODE_REPLACEMENTS  # LEFT SINGLE QUOTATION MARK
        assert "\u2019" in UNICODE_REPLACEMENTS  # RIGHT SINGLE QUOTATION MARK
        assert "\u201A" in UNICODE_REPLACEMENTS  # SINGLE LOW-9 QUOTATION MARK
        assert "\u201B" in UNICODE_REPLACEMENTS  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
        assert "\u201C" in UNICODE_REPLACEMENTS  # LEFT DOUBLE QUOTATION MARK
        assert "\u201D" in UNICODE_REPLACEMENTS  # RIGHT DOUBLE QUOTATION MARK
        assert "\u201E" in UNICODE_REPLACEMENTS  # DOUBLE LOW-9 QUOTATION MARK
        assert "\u201F" in UNICODE_REPLACEMENTS  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
        # Dashes
        assert "\u2013" in UNICODE_REPLACEMENTS  # EN DASH
        assert "\u2014" in UNICODE_REPLACEMENTS  # EM DASH
        # Other
        assert "\u2026" in UNICODE_REPLACEMENTS  # HORIZONTAL ELLIPSIS
        assert "\u00A0" in UNICODE_REPLACEMENTS  # NON-BREAKING SPACE


class TestSanitizationInContext:
    """Test that sanitization is applied correctly in various contexts.

    These tests verify that the fixes we applied work in the actual
    code paths that use artist/title data.
    """

    def test_artist_title_format_for_audio_separation(self):
        """Verify artist-title formatting is sanitized for Modal API."""
        # Simulate what audio_worker.py does
        artist = "Footloose (Broadway Cast)"
        title = "Mama Says (You Can\u2019t Back Down)"  # Curly apostrophe U+2019

        safe_artist = sanitize_filename(artist) if artist else "Unknown"
        safe_title = sanitize_filename(title) if title else "Unknown"
        artist_title = f"{safe_artist} - {safe_title}"

        # The curly apostrophe should be converted to straight
        assert "'" in artist_title  # Straight apostrophe
        assert "\u2019" not in artist_title  # No curly apostrophe (U+2019)

    def test_email_subject_safe_for_latin1(self):
        """Verify email subjects can be encoded to latin-1 after sanitization."""
        artist = "Café del Mar"  # Has accented e
        title = "It\u2019s a \u201cBeautiful\u201d Day \u2014 Remix"

        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else None
        subject = f"{safe_artist} - {safe_title}"

        # Accented characters should pass through (they're valid latin-1)
        assert "é" in subject
        # But curly quotes and em dash should be converted
        assert "'" in subject  # Straight apostrophe
        assert "-" in subject  # Regular hyphen
        # No curly apostrophe (U+2019)
        assert "\u2019" not in subject

    def test_content_disposition_safe(self):
        """Verify filenames are safe for HTTP Content-Disposition headers."""
        artist = "Artist\u2019s \u201cName\u201d"
        title = "Song\u2014Title"

        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else None
        filename = f"{safe_artist} - {safe_title} (Final Karaoke).mp4"

        # Should be able to encode to latin-1 for HTTP headers
        try:
            filename.encode('latin-1')
            can_encode = True
        except UnicodeEncodeError:
            can_encode = False

        # Standard ASCII chars and latin-1 safe accented chars should work
        # Note: The test above with 'é' passes latin-1; smart quotes do not
        assert can_encode

    def test_content_disposition_cjk_uses_rfc5987(self):
        """Verify CJK filenames use RFC 5987 encoding in Content-Disposition.

        Chinese/Japanese/Korean characters can't be encoded in latin-1,
        so the download endpoint must use filename*=UTF-8'' encoding.
        """
        from urllib.parse import quote

        artist = "周杰倫"
        title = "青花瓷"

        safe_artist = sanitize_filename(artist) if artist else None
        safe_title = sanitize_filename(title) if title else None
        filename = f"{safe_artist} - {safe_title} (Final Karaoke).mp4"

        # CJK characters cannot be encoded as latin-1
        with pytest.raises(UnicodeEncodeError):
            filename.encode('latin-1')

        # The download endpoint should fall back to RFC 5987 encoding
        ascii_filename = filename.encode('ascii', 'replace').decode('ascii')
        utf8_filename = quote(filename)
        content_disposition = f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{utf8_filename}"

        # ASCII fallback should be safe
        ascii_filename.encode('latin-1')

        # UTF-8 encoded filename should contain the original characters when decoded
        from urllib.parse import unquote
        decoded = unquote(utf8_filename)
        assert "周杰倫" in decoded
        assert "青花瓷" in decoded

    def test_google_drive_query_safe(self):
        """Verify filenames don't break Google Drive API queries."""
        base_name = "Artist\u2019s \u201cSong\u201d"

        safe_base_name = sanitize_filename(base_name) if base_name else base_name
        filename = f"NOMAD-1234 - {safe_base_name}.mp4"

        # Google Drive queries use single quotes - our sanitized string
        # should have straight single quotes, not curly
        # The query escaping handles straight quotes: '
        escaped = filename.replace("'", "\\'")
        query = f"name='{escaped}'"

        # Should be a valid query string (no curly quotes to break syntax)
        assert "\u2019" not in query  # No curly single quote (U+2019)
        assert "\u2018" not in query  # No curly single quote (U+2018)

    def test_dropbox_path_safe(self):
        """Verify Dropbox paths don't have problematic characters."""
        artist = "Don\u2019t Stop"
        title = "Believin\u2019"

        safe_artist = sanitize_filename(artist) if artist else "Unknown"
        safe_title = sanitize_filename(title) if title else "Unknown"
        folder_name = f"NOMAD-1234 - {safe_artist} - {safe_title}"
        remote_path = f"/Karaoke/{folder_name}"

        # Path should not have curly quotes (U+2018 and U+2019)
        assert "\u2018" not in remote_path
        assert "\u2019" not in remote_path
        # But should have the converted straight apostrophe
        assert "'" in remote_path


class TestInternationalCharacters:
    """Test that international/non-ASCII characters are handled properly.

    The goal is NOT to strip all non-ASCII, but to convert problematic
    Unicode to safe equivalents while preserving legitimate international text.
    """

    def test_japanese_preserved(self):
        """Japanese characters should pass through unchanged."""
        assert sanitize_filename("宇多田ヒカル") == "宇多田ヒカル"
        assert sanitize_filename("君が代") == "君が代"

    def test_korean_preserved(self):
        """Korean characters should pass through unchanged."""
        assert sanitize_filename("방탄소년단") == "방탄소년단"
        assert sanitize_filename("좋은 날") == "좋은 날"

    def test_chinese_preserved(self):
        """Chinese characters should pass through unchanged."""
        assert sanitize_filename("周杰倫") == "周杰倫"
        assert sanitize_filename("青花瓷") == "青花瓷"

    def test_cyrillic_preserved(self):
        """Cyrillic characters should pass through unchanged."""
        assert sanitize_filename("Тату") == "Тату"
        assert sanitize_filename("Не верь, не бойся") == "Не верь, не бойся"

    def test_arabic_preserved(self):
        """Arabic characters should pass through unchanged."""
        assert sanitize_filename("فيروز") == "فيروز"

    def test_accented_latin_preserved(self):
        """Accented Latin characters should pass through unchanged."""
        assert sanitize_filename("Café") == "Café"
        assert sanitize_filename("Señorita") == "Señorita"
        assert sanitize_filename("Naïve") == "Naïve"
        assert sanitize_filename("Björk") == "Björk"

    def test_mixed_script_preserved(self):
        """Mixed script text should work correctly."""
        # Japanese artist, English title
        assert sanitize_filename("宇多田ヒカル - First Love") == "宇多田ヒカル - First Love"
        # K-pop with English
        assert sanitize_filename("BTS 방탄소년단 - Dynamite") == "BTS 방탄소년단 - Dynamite"


class TestEdgeCasesAndRegression:
    """Edge cases and regression tests for specific bugs."""

    def test_job_d49efab1_exact_title(self):
        """Exact reproduction of job d49efab1 failure case.

        The job had:
        - artist: "Footloose (Broadway Cast)" (from display_artist override)
        - title: "Mama Says (You Can't Back Down)" (with curly apostrophe U+2019)

        This caused Stage 2 audio separation to fail because the filename
        with the curly apostrophe couldn't be encoded in HTTP headers.
        """
        artist = "Footloose (Broadway Cast)"
        title = "Mama Says (You Can\u2019t Back Down)"  # Explicit U+2019

        safe_artist = sanitize_filename(artist)
        safe_title = sanitize_filename(title)
        artist_title = f"{safe_artist} - {safe_title}"

        # The result should use straight apostrophe
        assert "Can't" in artist_title
        assert "\u2019" not in artist_title  # No curly apostrophe

        # Should be HTTP-header safe
        try:
            artist_title.encode('latin-1')
            header_safe = True
        except UnicodeEncodeError:
            header_safe = False
        assert header_safe

    def test_double_sanitization_idempotent(self):
        """Sanitizing twice should give the same result as once."""
        original = "It\u2019s \u201cMy\u201d Song \u2014 Test\u2026"
        once = sanitize_filename(original)
        twice = sanitize_filename(once)
        assert once == twice

    def test_only_problematic_chars_string(self):
        """Test string made entirely of problematic characters."""
        result = sanitize_filename("\u2018\u2019\u201c\u201d\u2014\u2026")
        # Should become: ''""--...  then quotes -> underscores, collapses
        # Actually: ' ' " " - ... -> underscores for filesystem chars
        assert result  # Should not be empty

    def test_very_long_filename(self):
        """Test that very long filenames are handled."""
        long_name = "A" * 1000
        result = sanitize_filename(long_name)
        assert result == long_name  # No truncation in sanitize_filename itself

    def test_special_musical_characters(self):
        """Test musical symbols and special characters."""
        # These should pass through as they're not in our replacement list
        assert "♪" in sanitize_filename("♪ Intro ♪")
        assert "♫" in sanitize_filename("♫ Music ♫")
        assert "♯" in sanitize_filename("C♯ Minor")
        assert "♭" in sanitize_filename("B♭ Major")


class TestNormalizeText:
    """Test the normalize_text function for data consistency normalization."""

    def test_curly_single_quotes_normalized(self):
        """Test that curly single quotes are converted to straight apostrophe."""
        assert normalize_text("Can\u2019t") == "Can't"
        assert normalize_text("It\u2018s") == "It's"
        assert normalize_text("\u201Aquote\u201B") == "'quote'"

    def test_curly_double_quotes_normalized(self):
        """Test that curly double quotes are converted to straight double quotes."""
        assert normalize_text("\u201CHello\u201D") == '"Hello"'
        assert normalize_text("\u201Equote\u201F") == '"quote"'

    def test_backticks_normalized(self):
        """Test that backticks and similar marks are converted to apostrophe."""
        assert normalize_text("code`here") == "code'here"
        assert normalize_text("acute\u00B4accent") == "acute'accent"
        assert normalize_text("prime\u2032mark") == "prime'mark"

    def test_dashes_normalized(self):
        """Test that various dashes are converted to hyphen-minus."""
        # EN DASH
        assert normalize_text("1990\u20132000") == "1990-2000"
        # EM DASH
        assert normalize_text("word\u2014word") == "word-word"
        # MINUS SIGN
        assert normalize_text("a\u2212b") == "a-b"
        # FIGURE DASH
        assert normalize_text("phone\u2012number") == "phone-number"

    def test_whitespace_normalized(self):
        """Test that various whitespace characters are normalized."""
        # NON-BREAKING SPACE
        assert normalize_text("hello\u00A0world") == "hello world"
        # EM SPACE
        assert normalize_text("hello\u2003world") == "hello world"
        # IDEOGRAPHIC SPACE (CJK full-width)
        assert normalize_text("hello\u3000world") == "hello world"
        # ZERO WIDTH SPACE (removed entirely)
        assert normalize_text("hello\u200Bworld") == "helloworld"

    def test_ellipsis_normalized(self):
        """Test that ellipsis character is converted to three dots."""
        assert normalize_text("Wait\u2026") == "Wait..."
        assert normalize_text("Loading\u2026please wait") == "Loading...please wait"

    def test_multiple_spaces_collapsed(self):
        """Test that multiple spaces are collapsed to one."""
        assert normalize_text("hello   world") == "hello world"
        # After normalizing multiple whitespace chars
        assert normalize_text("hello\u00A0\u00A0\u00A0world") == "hello world"

    def test_leading_trailing_whitespace_stripped(self):
        """Test that leading/trailing whitespace is stripped."""
        assert normalize_text("  hello  ") == "hello"
        assert normalize_text("\u00A0hello\u00A0") == "hello"

    def test_none_input(self):
        """Test that None input returns None."""
        assert normalize_text(None) is None

    def test_non_string_input(self):
        """Test that non-string input is returned unchanged."""
        assert normalize_text(123) == 123
        assert normalize_text(["list"]) == ["list"]

    def test_international_characters_preserved(self):
        """Test that international characters are NOT normalized away."""
        assert normalize_text("日本語") == "日本語"
        assert normalize_text("한국어") == "한국어"
        assert normalize_text("Café") == "Café"
        assert normalize_text("Björk") == "Björk"

    def test_real_world_examples(self):
        """Test real-world examples with mixed content."""
        # Job d49efab1's title
        assert normalize_text("Mama Says (You Can\u2019t Back Down)") == "Mama Says (You Can't Back Down)"
        # Broadway cast with smart quotes and em dash
        assert normalize_text("Footloose \u2014 \u201CFinal Song\u201D") == 'Footloose - "Final Song"'
        # Japanese with em dash
        assert normalize_text("宇多田ヒカル \u2014 First Love") == "宇多田ヒカル - First Love"

    def test_idempotent(self):
        """Test that normalizing twice gives the same result."""
        original = "It\u2019s \u201CMy\u201D Song \u2014 Test\u2026"
        once = normalize_text(original)
        twice = normalize_text(once)
        assert once == twice

    def test_text_normalizations_dict_complete(self):
        """Verify TEXT_NORMALIZATIONS includes all expected categories."""
        # Check apostrophe-like characters
        for char in APOSTROPHE_REPLACEMENTS:
            assert char in TEXT_NORMALIZATIONS

        # Check double quote-like characters
        for char in DOUBLE_QUOTE_REPLACEMENTS:
            assert char in TEXT_NORMALIZATIONS

        # Check dash-like characters
        for char in DASH_REPLACEMENTS:
            assert char in TEXT_NORMALIZATIONS

        # Check whitespace characters
        for char in WHITESPACE_REPLACEMENTS:
            assert char in TEXT_NORMALIZATIONS


class TestModelValidatorNormalization:
    """Test that model validators correctly normalize input text."""

    def test_job_create_normalizes_artist_title(self):
        """Test that JobCreate model normalizes artist and title fields."""
        from backend.models.job import JobCreate

        job = JobCreate(
            artist="Don\u2019t Stop",
            title="Believin\u2019 \u2014 Live",
        )
        assert job.artist == "Don't Stop"
        assert job.title == "Believin' - Live"

    def test_job_create_preserves_international_chars(self):
        """Test that JobCreate preserves international characters."""
        from backend.models.job import JobCreate

        job = JobCreate(
            artist="宇多田ヒカル",
            title="First Love",
        )
        assert job.artist == "宇多田ヒカル"
        assert job.title == "First Love"

    def test_job_create_handles_none(self):
        """Test that JobCreate handles None values correctly."""
        from backend.models.job import JobCreate

        job = JobCreate(
            artist=None,
            title="Some Title",
        )
        assert job.artist is None
        assert job.title == "Some Title"

    def test_audio_search_normalizes_fields(self):
        """Test that AudioSearchRequest normalizes text fields."""
        from backend.api.routes.audio_search import AudioSearchRequest

        request = AudioSearchRequest(
            artist="Artist\u2019s Name",
            title="Song \u2014 Remix",
            display_artist="Display\u2019s Artist",
            display_title="Display\u2019s Title",
        )
        assert request.artist == "Artist's Name"
        assert request.title == "Song - Remix"
        assert request.display_artist == "Display's Artist"
        assert request.display_title == "Display's Title"

    def test_audio_search_preserves_international_chars(self):
        """Test that AudioSearchRequest preserves international characters."""
        from backend.api.routes.audio_search import AudioSearchRequest

        request = AudioSearchRequest(
            artist="방탄소년단",
            title="Dynamite",
        )
        assert request.artist == "방탄소년단"
        assert request.title == "Dynamite"
