"""
Exact previews of a (draft) theme: the real title card and one karaoke-video frame.

Used by the tenant theme editor. Both images come from the SAME renderers production
uses, fed the draft ``style_params`` exactly the way a render job would see them:

- **Title card**: ``karaoke_gen.video_generator.VideoGenerator.create_title_video``
  with ``intro_video_duration=0`` (PNG only — what ``screens_worker`` does on Cloud Run).
- **Karaoke frame**: ``SegmentResizer`` → ``SubtitlesGenerator.generate_ass`` (the
  generator's 4K layout: ``font_size`` default 250, ``line_height = font_size``,
  ``max_line_length`` default 36) → one frame burned with the same ffmpeg ``ass``
  filter + background handling as ``lyrics_transcriber.output.video.VideoGenerator``,
  at a moment when the second line is part-way sung (sung = primary colour).

Theme assets are resolved like ``style_loader.update_asset_paths``: background images
per section, and the single ``font`` asset (``intro.font``) applied to
intro.font / karaoke.font_path / end.font / cdg.font_path.
"""

import base64
import copy
import hashlib
import io
import logging
import os
import subprocess
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image

from backend.services.storage_service import StorageService
from backend.services.theme_service import THEMES_PREFIX

logger = logging.getLogger(__name__)

PREVIEW_WIDTH = 1280  # output thumbnails are 1280x720 JPEGs
VIDEO_RES = (3840, 2160)  # production karaoke render resolution ("4k")
DEFAULT_FONT_SIZE = 250  # OutputGenerator._get_video_params default for 4k
DEFAULT_MAX_LINE_LENGTH = 36  # OutputConfig.default_max_line_length
FFMPEG_TIMEOUT_S = 30

# Required ASS style keys build_karaoke_styles reads without defaults.
_REQUIRED_KARAOKE_KEYS = ("underline", "strike_out", "angle", "encoding", "ass_name")

BUNDLED_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "karaoke_gen",
    "resources",
)
FONT_EXTENSIONS = (".ttf", ".otf")

DEFAULT_SAMPLE_LYRICS = [
    "Every night I sing along",
    "Lights go down and here's my song",
    "Hold the mic and take the stage",
    "Turn the page, turn the page",
]


class ThemePreviewError(RuntimeError):
    """A preview couldn't be rendered (bad theme, missing asset, ffmpeg failure)."""


def bundled_fonts() -> List[str]:
    """Font files shipped with karaoke_gen (usable by any theme)."""
    try:
        return sorted(f for f in os.listdir(BUNDLED_FONTS_DIR) if f.lower().endswith(FONT_EXTENSIONS))
    except OSError:
        return []


def font_family_name(font_file: str) -> str:
    """The name libass matches for a font file (e.g. 'Avenir Next Bold')."""
    from PIL import ImageFont

    family, style = ImageFont.truetype(font_file, 20).getname()
    family = (family or "").strip()
    style = (style or "").strip()
    if style and style.lower() not in ("regular", "normal", "book", "roman"):
        return f"{family} {style}"
    return family


@dataclass
class PreviewImages:
    title_card: bytes  # JPEG
    karaoke_frame: bytes  # JPEG

    def as_data_urls(self) -> Dict[str, str]:
        enc = lambda b: "data:image/jpeg;base64," + base64.b64encode(b).decode()  # noqa: E731
        return {"title_card": enc(self.title_card), "karaoke_frame": enc(self.karaoke_frame)}


# ---------------------------------------------------------------------------
# Asset resolution (mirrors theme_service.prepare_job_style + style_loader)
# ---------------------------------------------------------------------------

_BG_FIELDS = (("intro", "background_image"), ("karaoke", "background_image"), ("end", "background_image"))
_FONT_TARGETS = (("intro", "font"), ("karaoke", "font_path"), ("end", "font"), ("cdg", "font_path"))

_download_lock = threading.Lock()

# Cloud Run's /tmp is in-memory: keep both on-disk caches bounded.
ASSET_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "theme-preview-assets")
BG_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "theme-preview-bg")
ASSET_CACHE_MAX_BYTES = 300 * 1024 * 1024
BG_CACHE_MAX_BYTES = 300 * 1024 * 1024


def _cache_dir(theme_id: str) -> str:
    path = os.path.join(ASSET_CACHE_ROOT, theme_id)
    os.makedirs(path, exist_ok=True)
    return path


def _evict(root: str, max_bytes: int, keep: str = "") -> None:
    """Delete least-recently-used files under root until it fits in max_bytes."""
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            files.append((st.st_atime, st.st_size, path))
    total = sum(size for _t, size, _p in files)
    for _atime, size, path in sorted(files):
        if total <= max_bytes:
            break
        if path == keep:
            continue
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


def _local_theme_asset(storage: StorageService, theme_id: str, basename: str) -> Optional[str]:
    """Download themes/<id>/assets/<basename> into a per-generation cache; None if absent."""
    blob = storage.bucket.get_blob(f"{THEMES_PREFIX}/{theme_id}/assets/{basename}")
    if blob is None:
        return None
    gen_dir = os.path.join(_cache_dir(theme_id), str(blob.generation))
    local = os.path.join(gen_dir, basename)
    with _download_lock:
        if not os.path.isfile(local):
            os.makedirs(gen_dir, exist_ok=True)
            # Download to a temp name + atomic rename: a failed download must
            # never leave a partial file that later looks cached.
            partial = f"{local}.part-{os.getpid()}-{threading.get_ident()}"
            try:
                blob.download_to_filename(partial)
                os.replace(partial, local)
            finally:
                if os.path.exists(partial):
                    os.remove(partial)
            _evict(ASSET_CACHE_ROOT, ASSET_CACHE_MAX_BYTES, keep=local)
    return local


def resolve_assets(
    style_params: Dict, theme_id: str, storage: Optional[StorageService] = None
) -> Dict:
    """Return a deep copy of style_params with asset basenames replaced by local paths.

    Backgrounds resolve from the theme's GCS assets. The font resolves from the
    theme's assets, else from bundled fonts (a bundled font is copied into the
    theme on save, so production resolves it the same way).
    """
    storage = storage or StorageService()
    styles = copy.deepcopy(style_params)

    for section, field in _BG_FIELDS:
        value = (styles.get(section) or {}).get(field)
        if not value:
            continue
        local = _local_theme_asset(storage, theme_id, os.path.basename(value))
        if not local:
            raise ThemePreviewError(f"Background image '{value}' ({section}) was not found in the theme's assets.")
        styles[section][field] = local

    font = (styles.get("intro") or {}).get("font")
    if font:
        base = os.path.basename(font)
        local = _local_theme_asset(storage, theme_id, base)
        if not local and base in bundled_fonts():
            local = os.path.join(BUNDLED_FONTS_DIR, base)
        if not local:
            raise ThemePreviewError(f"Font '{font}' was not found in the theme's assets.")
        for section, field in _FONT_TARGETS:
            if isinstance(styles.get(section), dict):
                styles[section][field] = local
        # libass selects by family name — same value save_tenant_theme persists.
        if isinstance(styles.get("karaoke"), dict):
            try:
                styles["karaoke"]["font"] = font_family_name(local)
            except Exception as exc:
                raise ThemePreviewError(f"Font '{font}' couldn't be read.") from exc
    return styles


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _to_jpeg(png_path: str) -> bytes:
    with Image.open(png_path) as img:
        img = img.convert("RGB")
        height = round(img.height * PREVIEW_WIDTH / img.width)
        img = img.resize((PREVIEW_WIDTH, height), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def render_title_card(styles: Dict, artist: str, title: str, workdir: str) -> bytes:
    """Title card exactly as screens_worker renders it (PNG via PIL, no video)."""
    from karaoke_gen.style_loader import DEFAULT_INTRO_STYLE
    from karaoke_gen.video_generator import VideoGenerator

    fmt = {**DEFAULT_INTRO_STYLE, **(styles.get("intro") or {})}
    generator = VideoGenerator(
        logger=logger,
        ffmpeg_base_command="ffmpeg -hide_banner -nostats -loglevel error",
        render_bounding_boxes=False,
        output_png=True,
        output_jpg=False,
    )
    noext = os.path.join(workdir, "title")
    try:
        generator.create_title_video(
            artist=artist,
            title=title,
            format=fmt,
            output_image_filepath_noext=noext,
            output_video_filepath=os.path.join(workdir, "title.mov"),
            existing_title_image=fmt.get("existing_image"),
            intro_video_duration=0,
        )
    except Exception as exc:
        raise ThemePreviewError(f"Title card render failed: {exc}") from exc
    return _to_jpeg(f"{noext}.png")


def _sample_segments(lines: List[str]):
    """Evenly-timed segments: line i starts at 2+3i s, words spread over 2.5 s.

    Starts < 10 s and gaps < 10 s so SectionDetector adds no intro/instrumental.
    """
    from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word

    segments = []
    for i, text in enumerate(lines):
        words_text = text.split() or ["…"]
        start = 2.0 + 3.0 * i
        step = 2.5 / len(words_text)
        words = [
            Word(id=f"w{i}-{j}", text=w, start_time=start + j * step, end_time=start + (j + 1) * step - 0.05)
            for j, w in enumerate(words_text)
        ]
        segments.append(
            LyricsSegment(id=f"s{i}", text=" ".join(words_text), words=words, start_time=start, end_time=start + 2.5)
        )
    return segments


class _FrameRenderer:
    """Borrow lyrics VideoGenerator's background + ASS filter handling without its
    __init__ (which probes for NVENC via nvidia-smi — pointless for one frame)."""

    def __init__(self, styles: Dict, cache_dir: str):
        from karaoke_gen.lyrics_transcriber.output.video import VideoGenerator as LyricsVideoGenerator

        gen = LyricsVideoGenerator.__new__(LyricsVideoGenerator)
        gen.output_dir = cache_dir
        gen.cache_dir = cache_dir
        gen.video_resolution = VIDEO_RES
        gen.styles = styles
        gen.logger = logger
        karaoke = styles.get("karaoke", {})
        gen.background_image = karaoke.get("background_image")
        gen.background_color = karaoke.get("background_color", "black")
        if gen.background_image and not os.path.isfile(gen.background_image):
            raise ThemePreviewError(f"Karaoke background image not found: {gen.background_image}")
        self.gen = gen

    def _resized_background(self, path: str) -> str:
        """Production's 4K scale+pad of the background, cached per source file."""
        st = os.stat(path)
        key = hashlib.sha1(f"{path}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:16]
        cached = os.path.join(BG_CACHE_ROOT, f"{key}.png")
        if os.path.isfile(cached):
            return cached
        os.makedirs(BG_CACHE_ROOT, exist_ok=True)
        resized = self.gen._resize_background_image(path)
        if resized == path:
            return path
        os.replace(resized, cached)
        _evict(BG_CACHE_ROOT, BG_CACHE_MAX_BYTES, keep=cached)
        return cached

    def frame(self, ass_path: str, at_seconds: float, out_png: str) -> None:
        w, h = VIDEO_RES
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-r", "30"]
        if self.gen.background_image:
            cmd += ["-loop", "1", "-i", self._resized_background(self.gen.background_image)]
        else:
            cmd += ["-f", "lavfi", "-i", f"color=c={self.gen.background_color}:s={w}x{h}:r=30"]
        try:
            ass_filter = self.gen._build_ass_filter(ass_path)
        except FileNotFoundError as exc:
            raise ThemePreviewError(str(exc)) from exc
        # Shift the single input frame's timestamp to `at_seconds` so libass renders
        # that moment directly (output-side -ss would render every 4K frame up to it).
        # Downscale inside the same pass (encoding a 4K PNG is most of the cost).
        vf = f"setpts=PTS+{at_seconds:.3f}/TB,{ass_filter},scale={PREVIEW_WIDTH}:-2:flags=lanczos"
        cmd += ["-vf", vf, "-frames:v", "1", "-y", out_png]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
        except subprocess.CalledProcessError as exc:
            raise ThemePreviewError(f"Karaoke frame render failed: {exc.stderr[-500:]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ThemePreviewError("Karaoke frame render timed out") from exc


def render_karaoke_frame(styles: Dict, lines: List[str], workdir: str) -> bytes:
    """One frame of the karaoke video with the first line about half sung."""
    from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
    from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
    from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

    karaoke = dict(styles.get("karaoke") or {})
    # build_karaoke_styles reads these without defaults; fill so a sparse theme previews.
    for key in _REQUIRED_KARAOKE_KEYS:
        karaoke.setdefault(key, DEFAULT_KARAOKE_STYLE[key])
    styles = {**styles, "karaoke": karaoke}

    font_size = karaoke.get("font_size", DEFAULT_FONT_SIZE)
    max_line_length = karaoke.get("max_line_length", DEFAULT_MAX_LINE_LENGTH)
    segments = SegmentResizer(max_line_length=max_line_length, logger=logger).resize_segments(
        _sample_segments(lines)
    )
    subtitles = SubtitlesGenerator(
        output_dir=workdir,
        video_resolution=VIDEO_RES,
        font_size=font_size,
        line_height=font_size,
        styles=styles,
        logger=logger,
    )
    try:
        ass_path = subtitles.generate_ass(segments, "preview", os.path.join(workdir, "no-audio.flac"))
    except Exception as exc:
        raise ThemePreviewError(f"Lyrics layout failed: {exc}") from exc

    first = segments[0]
    at = first.start_time + (first.end_time - first.start_time) * 0.5
    out_png = os.path.join(workdir, "karaoke.png")
    _FrameRenderer(styles, workdir).frame(ass_path, at, out_png)
    return _to_jpeg(out_png)


# ---------------------------------------------------------------------------
# Public entry point (+ small cache for identical drafts)
# ---------------------------------------------------------------------------

_CACHE: "OrderedDict[str, PreviewImages]" = OrderedDict()
_CACHE_MAX = 32
_cache_lock = threading.Lock()


def render_theme_preview(
    theme_id: str,
    style_params: Dict,
    *,
    artist: str = "Artist Name",
    title: str = "Song Title",
    lyrics: Optional[List[str]] = None,
    storage: Optional[StorageService] = None,
) -> PreviewImages:
    lines = [ln.strip() for ln in (lyrics or DEFAULT_SAMPLE_LYRICS) if ln and ln.strip()][:4] or DEFAULT_SAMPLE_LYRICS
    key = hashlib.sha256(
        repr((theme_id, sorted_repr(style_params), artist, title, lines)).encode()
    ).hexdigest()
    with _cache_lock:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]

    styles = resolve_assets(style_params, theme_id, storage)
    with tempfile.TemporaryDirectory(prefix="theme-preview-") as workdir:
        result = PreviewImages(
            title_card=render_title_card(styles, artist, title, workdir),
            karaoke_frame=render_karaoke_frame(styles, lines, workdir),
        )
    with _cache_lock:
        _CACHE[key] = result
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return result


def sorted_repr(value) -> str:
    """Stable repr for hashing nested dicts."""
    import json

    return json.dumps(value, sort_keys=True, default=str)
