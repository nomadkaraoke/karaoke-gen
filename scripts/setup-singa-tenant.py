#!/usr/bin/env python3
"""
Setup script for Singa white-label tenant.

This script generates all assets programmatically (no external design files needed):
1. Downloads Singa's SVG logo and converts to PNG
2. Generates 4K backgrounds (3840x2160) using Pillow
3. Generates CDG backgrounds (300x216, indexed color GIF)
4. Downloads Inter SemiBold font from Google Fonts
5. Creates and uploads theme config (style_params.json)
6. Creates and uploads tenant config (config.json)
7. Updates the theme registry (_metadata.json)
8. Prints DNS/Cloudflare setup instructions

Usage:
    python scripts/setup-singa-tenant.py

Requirements:
    pip install Pillow google-cloud-storage requests cairosvg
    gcloud auth application-default login
"""
import io
import json
import math
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import storage

# ─── Constants ────────────────────────────────────────────────────────────────

TENANT_ID = "singa"
THEME_ID = "singa"
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"

# Singa brand colors
SINGA_GREEN = "#17E87A"
SINGA_BLACK = "#000000"
SINGA_WHITE = "#FFFFFF"
SINGA_DARK_GRAY = "#1A1A1A"

# RGB tuples
GREEN_RGB = (23, 232, 122)
BLACK_RGB = (0, 0, 0)
WHITE_RGB = (255, 255, 255)
DARK_GRAY_RGB = (26, 26, 26)
DARK_GREEN_RGB = (0, 80, 50)

# Local logo path (download from singa.com brand assets manually)
SINGA_LOGO_SVG_DEFAULT = Path.home() / "Downloads" / "singa-logo-white.svg"

# Dimensions
VIDEO_4K_WIDTH = 3840
VIDEO_4K_HEIGHT = 2160
CDG_WIDTH = 300
CDG_HEIGHT = 216


# ─── Asset Generation ─────────────────────────────────────────────────────────


def convert_logo(output_dir: Path, svg_path: Path) -> Path:
    """Convert Singa SVG logo to PNG."""
    if not svg_path.exists():
        print(f"  ERROR: Logo SVG not found at {svg_path}")
        print(f"  Download from singa.com brand assets and save to {svg_path}")
        sys.exit(1)

    svg_data = svg_path.read_bytes()
    png_path = output_dir / "logo.png"

    # Try cairosvg first for best quality
    try:
        import cairosvg

        cairosvg.svg2png(
            bytestring=svg_data,
            write_to=str(png_path),
            output_width=600,
            background_color="transparent",
        )
        print(f"  Converted {svg_path.name} to PNG via cairosvg ({png_path.name})")
        return png_path
    except ImportError:
        print("  cairosvg not available, trying rsvg-convert...")

    # Fallback: try rsvg-convert (available via brew install librsvg)
    import subprocess

    try:
        subprocess.run(
            ["rsvg-convert", "-w", "600", "-o", str(png_path), str(svg_path)],
            check=True,
            capture_output=True,
        )
        print(f"  Converted {svg_path.name} to PNG via rsvg-convert")
        return png_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  rsvg-convert not available either")

    # Last resort: create text-based logo
    print("  WARNING: No SVG converter found. Creating text-based logo fallback.")
    print("  Install cairosvg (pip install cairosvg) or librsvg (brew install librsvg) for proper logo.")
    img = Image.new("RGBA", (600, 150), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
    except OSError:
        font = ImageFont.load_default()
    draw.text((50, 30), "singa", fill=WHITE_RGB, font=font)
    img.save(png_path)
    return png_path


def download_inter_font(output_dir: Path) -> Path:
    """Download Inter SemiBold TTF font."""
    font_path = output_dir / "Inter-SemiBold.ttf"

    # Try downloading the static Inter SemiBold directly from GitHub
    static_url = (
        "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
    )
    print("  Downloading Inter font package...")

    try:
        response = requests.get(static_url, timeout=60, stream=True)
        response.raise_for_status()

        import zipfile

        zip_data = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_data) as zf:
            # Look for Inter-SemiBold.ttf in the zip
            for name in zf.namelist():
                if name.endswith("Inter-SemiBold.ttf"):
                    with zf.open(name) as src, open(font_path, "wb") as dst:
                        dst.write(src.read())
                    print(f"  Extracted {name}")
                    return font_path

            # If no static SemiBold, look for any static TTF
            for name in sorted(zf.namelist()):
                if "static" in name.lower() and name.endswith(".ttf"):
                    print(f"  Available: {name}")

            # Fallback: use the variable font
            for name in zf.namelist():
                if name.endswith("Inter.ttc") or (
                    "Inter" in name and name.endswith(".ttf") and "static" not in name.lower()
                ):
                    with zf.open(name) as src, open(font_path, "wb") as dst:
                        dst.write(src.read())
                    print(f"  Extracted variable font: {name}")
                    return font_path

        print("  WARNING: Could not find Inter SemiBold in zip")
    except Exception as e:
        print(f"  WARNING: Failed to download Inter font: {e}")

    # Final fallback: try Google Fonts API
    try:
        gf_url = "https://fonts.google.com/download?family=Inter"
        print("  Trying Google Fonts download...")
        response = requests.get(gf_url, timeout=60)
        response.raise_for_status()

        import zipfile

        zip_data = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_data) as zf:
            for name in zf.namelist():
                if "SemiBold" in name and name.endswith(".ttf"):
                    with zf.open(name) as src, open(font_path, "wb") as dst:
                        dst.write(src.read())
                    print(f"  Extracted {name}")
                    return font_path
    except Exception as e:
        print(f"  WARNING: Google Fonts fallback also failed: {e}")

    raise RuntimeError(
        "Could not download Inter font. Please manually place Inter-SemiBold.ttf "
        f"at {font_path}"
    )


def generate_4k_intro_background(output_dir: Path, logo_path: Path) -> Path:
    """Generate 4K intro background: black with subtle gradient, logo, green accent line."""
    path = output_dir / "intro_background.png"
    img = Image.new("RGB", (VIDEO_4K_WIDTH, VIDEO_4K_HEIGHT), BLACK_RGB)
    draw = ImageDraw.Draw(img)

    # Subtle radial gradient from center (slightly lighter) to edges (pure black)
    _draw_radial_gradient(
        img,
        center=(VIDEO_4K_WIDTH // 2, VIDEO_4K_HEIGHT // 3),
        radius=VIDEO_4K_HEIGHT,
        center_color=(20, 20, 20),
        edge_color=BLACK_RGB,
    )

    # Load and place logo in upper-center area
    try:
        logo = Image.open(logo_path).convert("RGBA")
        # Scale logo to ~800px wide, maintaining aspect ratio
        logo_target_width = 800
        scale = logo_target_width / logo.width
        logo_resized = logo.resize(
            (int(logo.width * scale), int(logo.height * scale)),
            Image.LANCZOS,
        )
        logo_x = (VIDEO_4K_WIDTH - logo_resized.width) // 2
        logo_y = VIDEO_4K_HEIGHT // 3 - logo_resized.height // 2
        img.paste(logo_resized, (logo_x, logo_y), logo_resized)
    except Exception as e:
        print(f"  WARNING: Could not place logo on intro background: {e}")

    # Thin green accent line below logo
    line_y = VIDEO_4K_HEIGHT // 3 + 200
    line_width = 600
    line_x_start = (VIDEO_4K_WIDTH - line_width) // 2
    draw.line(
        [(line_x_start, line_y), (line_x_start + line_width, line_y)],
        fill=GREEN_RGB,
        width=3,
    )

    img.save(path, "PNG")
    print(f"  Generated: intro_background.png ({VIDEO_4K_WIDTH}x{VIDEO_4K_HEIGHT})")
    return path


def generate_4k_karaoke_background(output_dir: Path) -> Path:
    """Generate 4K karaoke background: near-black with very subtle radial gradient."""
    path = output_dir / "karaoke_background.png"
    img = Image.new("RGB", (VIDEO_4K_WIDTH, VIDEO_4K_HEIGHT), BLACK_RGB)

    # Very subtle radial gradient - dark center to slightly lighter edges
    # This needs to be extremely subtle so lyrics remain clearly readable
    _draw_radial_gradient(
        img,
        center=(VIDEO_4K_WIDTH // 2, VIDEO_4K_HEIGHT // 2),
        radius=max(VIDEO_4K_WIDTH, VIDEO_4K_HEIGHT),
        center_color=BLACK_RGB,
        edge_color=(12, 12, 12),
    )

    img.save(path, "PNG")
    print(f"  Generated: karaoke_background.png ({VIDEO_4K_WIDTH}x{VIDEO_4K_HEIGHT})")
    return path


def generate_4k_end_background(output_dir: Path, logo_path: Path) -> Path:
    """Generate 4K end background: same style as intro."""
    path = output_dir / "end_background.png"
    img = Image.new("RGB", (VIDEO_4K_WIDTH, VIDEO_4K_HEIGHT), BLACK_RGB)
    draw = ImageDraw.Draw(img)

    # Subtle radial gradient
    _draw_radial_gradient(
        img,
        center=(VIDEO_4K_WIDTH // 2, VIDEO_4K_HEIGHT // 3),
        radius=VIDEO_4K_HEIGHT,
        center_color=(20, 20, 20),
        edge_color=BLACK_RGB,
    )

    # Load and place logo
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo_target_width = 800
        scale = logo_target_width / logo.width
        logo_resized = logo.resize(
            (int(logo.width * scale), int(logo.height * scale)),
            Image.LANCZOS,
        )
        logo_x = (VIDEO_4K_WIDTH - logo_resized.width) // 2
        logo_y = VIDEO_4K_HEIGHT // 3 - logo_resized.height // 2
        img.paste(logo_resized, (logo_x, logo_y), logo_resized)
    except Exception as e:
        print(f"  WARNING: Could not place logo on end background: {e}")

    # Thin green accent line below logo
    line_y = VIDEO_4K_HEIGHT // 3 + 200
    line_width = 600
    line_x_start = (VIDEO_4K_WIDTH - line_width) // 2
    draw.line(
        [(line_x_start, line_y), (line_x_start + line_width, line_y)],
        fill=GREEN_RGB,
        width=3,
    )

    img.save(path, "PNG")
    print(f"  Generated: end_background.png ({VIDEO_4K_WIDTH}x{VIDEO_4K_HEIGHT})")
    return path


def generate_cdg_title_background(output_dir: Path) -> Path:
    """Generate CDG title/outro background: black with green border accent, 4-color indexed GIF."""
    path = output_dir / "cdg_title_background.gif"

    # Create in RGB first, then convert to palette
    img = Image.new("RGB", (CDG_WIDTH, CDG_HEIGHT), BLACK_RGB)
    draw = ImageDraw.Draw(img)

    # Green border accent (thin lines on edges)
    border_width = 2
    # Top border
    draw.rectangle(
        [0, 0, CDG_WIDTH - 1, border_width - 1],
        fill=GREEN_RGB,
    )
    # Bottom border
    draw.rectangle(
        [0, CDG_HEIGHT - border_width, CDG_WIDTH - 1, CDG_HEIGHT - 1],
        fill=GREEN_RGB,
    )
    # Left border
    draw.rectangle(
        [0, 0, border_width - 1, CDG_HEIGHT - 1],
        fill=GREEN_RGB,
    )
    # Right border
    draw.rectangle(
        [CDG_WIDTH - border_width, 0, CDG_WIDTH - 1, CDG_HEIGHT - 1],
        fill=GREEN_RGB,
    )

    # Convert to indexed color (4-color palette for CDG compatibility)
    # CDG supports max 16 colors, but we keep it minimal
    palette_img = _convert_to_indexed_gif(img)
    palette_img.save(path, "GIF")
    print(f"  Generated: cdg_title_background.gif ({CDG_WIDTH}x{CDG_HEIGHT})")
    return path


def generate_cdg_instrumental_background(output_dir: Path) -> Path:
    """Generate CDG instrumental background: black with green accents and text area."""
    path = output_dir / "cdg_instrumental_background.gif"

    img = Image.new("RGB", (CDG_WIDTH, CDG_HEIGHT), BLACK_RGB)
    draw = ImageDraw.Draw(img)

    # Green accent lines (horizontal, subtle)
    line_y_top = CDG_HEIGHT // 3
    line_y_bottom = 2 * CDG_HEIGHT // 3
    draw.line(
        [(20, line_y_top), (CDG_WIDTH - 20, line_y_top)],
        fill=GREEN_RGB,
        width=1,
    )
    draw.line(
        [(20, line_y_bottom), (CDG_WIDTH - 20, line_y_bottom)],
        fill=GREEN_RGB,
        width=1,
    )

    # Convert to indexed GIF
    palette_img = _convert_to_indexed_gif(img)
    palette_img.save(path, "GIF")
    print(f"  Generated: cdg_instrumental_background.gif ({CDG_WIDTH}x{CDG_HEIGHT})")
    return path


def _draw_radial_gradient(
    img: Image.Image,
    center: tuple[int, int],
    radius: int,
    center_color: tuple[int, int, int],
    edge_color: tuple[int, int, int],
) -> None:
    """Draw a radial gradient on an image. Generates at 1/4 resolution and upscales for speed."""
    width, height = img.size

    # Generate at 1/4 resolution for speed, then upscale (gradient is smooth)
    scale = 4
    small_w, small_h = width // scale, height // scale
    cx, cy = center[0] // scale, center[1] // scale
    small_radius = radius // scale

    try:
        import numpy as np

        y_coords, x_coords = np.mgrid[0:small_h, 0:small_w]
        dist = np.sqrt((x_coords - cx) ** 2 + (y_coords - cy) ** 2)
        t = np.clip(dist / small_radius, 0, 1)

        result = np.zeros((small_h, small_w, 3), dtype=np.uint8)
        for c in range(3):
            result[:, :, c] = (
                center_color[c] + (edge_color[c] - center_color[c]) * t
            ).astype(np.uint8)

        small_img = Image.fromarray(result)
    except ImportError:
        # Pure Pillow fallback at reduced resolution
        small_img = Image.new("RGB", (small_w, small_h), edge_color)
        pixels = small_img.load()
        for y in range(small_h):
            for x in range(small_w):
                dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
                t = min(dist / small_radius, 1.0)
                r = int(center_color[0] + (edge_color[0] - center_color[0]) * t)
                g = int(center_color[1] + (edge_color[1] - center_color[1]) * t)
                b = int(center_color[2] + (edge_color[2] - center_color[2]) * t)
                pixels[x, y] = (r, g, b)

    # Upscale to target resolution with smooth interpolation
    upscaled = small_img.resize((width, height), Image.LANCZOS)
    img.paste(upscaled, (0, 0))


def _convert_to_indexed_gif(img: Image.Image) -> Image.Image:
    """Convert RGB image to indexed color GIF with limited palette."""
    # Quantize to 4 colors for CDG compatibility
    return img.quantize(colors=4, method=Image.Quantize.MEDIANCUT)


# ─── Configuration Builders ───────────────────────────────────────────────────


def create_theme_config() -> dict:
    """Create the Singa theme configuration (style_params.json)."""
    return {
        "intro": {
            "video_duration": 5,
            "existing_image": None,
            "background_color": SINGA_BLACK,
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/intro_background.png",
            "font": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Inter-SemiBold.ttf",
            "title_region": "370,450,3100,480",
            "title_text_transform": None,
            "title_color": SINGA_WHITE,
            "title_gradient": {
                "color1": SINGA_WHITE,
                "color2": SINGA_GREEN,
                "direction": "vertical",
                "start": 0.4,
                "stop": 0.8,
            },
            "artist_region": "370,1200,3100,480",
            "artist_text_transform": None,
            "artist_color": SINGA_GREEN,
            "artist_gradient": None,
            "extra_text": None,
            "extra_text_color": SINGA_WHITE,
            "extra_text_region": None,
            "extra_text_gradient": None,
        },
        "karaoke": {
            "background_color": SINGA_BLACK,
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/karaoke_background.png",
            "font": "Inter SemiBold",
            "font_path": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Inter-SemiBold.ttf",
            "font_size": 420,
            "top_padding": 100,
            "ass_name": "Singa",
            "primary_color": "23, 232, 122, 255",  # Green (sung)
            "secondary_color": "255, 255, 255, 255",  # White (unsung)
            "outline_color": "0, 80, 50, 255",  # Dark green
            "back_color": "0, 0, 0, 255",
            "bold": False,
            "italic": False,
            "underline": False,
            "strike_out": False,
            "scale_x": 100,
            "scale_y": 100,
            "spacing": 0,
            "angle": 0.0,
            "border_style": 1,
            "outline": 1,
            "shadow": 0,
            "margin_l": 0,
            "margin_r": 0,
            "margin_v": 0,
            "encoding": 0,
        },
        "end": {
            "video_duration": 5,
            "existing_image": None,
            "background_color": SINGA_BLACK,
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/end_background.png",
            "font": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Inter-SemiBold.ttf",
            "title_region": None,
            "title_text_transform": "none",
            "title_color": SINGA_WHITE,
            "title_gradient": None,
            "artist_region": None,
            "artist_text_transform": "none",
            "artist_color": SINGA_GREEN,
            "artist_gradient": None,
            "extra_text": None,
            "extra_text_color": None,
            "extra_text_region": None,
            "extra_text_gradient": None,
        },
        "cdg": {
            "clear_mode": "eager",
            "sync_offset": 0,
            "lead_in_threshold": 300,
            "lead_in_duration": 30,
            "lead_in_total": 200,
            "lead_in_symbols": ["/>", ">", ">"],
            "row": 4,
            "line_tile_height": 3,
            "lines_per_page": 4,
            "background_color": SINGA_BLACK,
            "border_color": SINGA_GREEN,
            "title_color": SINGA_GREEN,
            "artist_color": SINGA_GREEN,
            "outro_line1_color": SINGA_GREEN,
            "outro_line2_color": SINGA_GREEN,
            "active_fill": SINGA_GREEN,
            "active_stroke": SINGA_BLACK,
            "inactive_fill": SINGA_WHITE,
            "inactive_stroke": SINGA_BLACK,
            "instrumental_font_color": SINGA_GREEN,
            "font_path": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Inter-SemiBold.ttf",
            "font_size": 18,
            "stroke_width": 0,
            "stroke_style": "octagon",
            "instrumental_gap_threshold": 1500,
            "instrumental_text": "INSTRUMENTAL",
            "instrumental_background": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/cdg_instrumental_background.gif",
            "instrumental_transition": "topleftmusicalnotes",
            "title_screen_background": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/cdg_title_background.gif",
            "title_screen_transition": "centertexttoplogobottomtext",
            "title_artist_gap": 30,
            "intro_duration_seconds": 10.0,
            "first_syllable_buffer_seconds": 3.0,
            "outro_background": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/cdg_title_background.gif",
            "outro_transition": "centertexttoplogobottomtext",
            "outro_text_line1": "$title",
            "outro_text_line2": "$artist",
            "outro_line1_line2_gap": 30,
        },
    }


def create_tenant_config() -> dict:
    """Create the Singa tenant configuration."""
    return {
        "id": TENANT_ID,
        "name": "Singa",
        "subdomain": "singa.nomadkaraoke.com",
        "is_active": True,
        "branding": {
            "logo_url": f"https://api.nomadkaraoke.com/api/tenant/asset/{TENANT_ID}/logo.png",
            "logo_height": 50,
            "primary_color": SINGA_GREEN,
            "secondary_color": SINGA_WHITE,
            "accent_color": SINGA_GREEN,
            "background_color": SINGA_BLACK,
            "favicon_url": None,
            "site_title": "Singa Karaoke Generator",
            "tagline": "Sing your favorite karaoke songs",
        },
        "features": {
            "audio_search": False,
            "file_upload": True,
            "youtube_url": False,
            "youtube_upload": False,
            "dropbox_upload": False,
            "gdrive_upload": False,
            "theme_selection": False,
            "color_overrides": False,
            "enable_cdg": True,
            "enable_4k": True,
            "admin_access": False,
        },
        "defaults": {
            "theme_id": THEME_ID,
            "locked_theme": THEME_ID,
            "distribution_mode": "download_only",
            "brand_prefix": "SINGA",
            "youtube_description_template": None,
        },
        "auth": {
            "allowed_email_domains": ["singa.com", "nomadkaraoke.com"],
            "require_email_domain": True,
            "fixed_token_ids": [],
            "sender_email": "singa@nomadkaraoke.com",
        },
    }


def create_theme_metadata() -> dict:
    """Create the theme metadata entry for the registry."""
    return {
        "id": THEME_ID,
        "name": "Singa",
        "description": "Official Singa karaoke style with green/white color scheme on black",
        "is_default": False,
    }


# ─── GCS Upload Helpers ───────────────────────────────────────────────────────


def upload_file(bucket, source_path: Path, dest_path: str) -> str:
    """Upload a file to GCS and return the gs:// URL."""
    blob = bucket.blob(dest_path)
    blob.upload_from_filename(str(source_path))
    gs_url = f"gs://{GCS_BUCKET}/{dest_path}"
    print(f"  Uploaded: {source_path.name} -> {gs_url}")
    return gs_url


def upload_json(bucket, data: dict, dest_path: str) -> str:
    """Upload a JSON object to GCS and return the gs:// URL."""
    blob = bucket.blob(dest_path)
    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )
    gs_url = f"gs://{GCS_BUCKET}/{dest_path}"
    print(f"  Created: {gs_url}")
    return gs_url


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Setup Singa tenant on Nomad Karaoke")
    parser.add_argument(
        "--logo",
        type=Path,
        default=SINGA_LOGO_SVG_DEFAULT,
        help=f"Path to Singa logo SVG (default: {SINGA_LOGO_SVG_DEFAULT})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Singa Tenant Setup")
    print("=" * 60)
    print()
    print("This script generates all Singa assets programmatically")
    print("and uploads them to GCS.")
    print()

    with tempfile.TemporaryDirectory(prefix="singa-tenant-") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Step 1: Convert logo SVG to PNG
        print("1. Converting Singa logo SVG to PNG...")
        logo_path = convert_logo(tmp_path, args.logo)
        print()

        # Step 2: Download Inter font
        print("2. Downloading Inter SemiBold font...")
        font_path = download_inter_font(tmp_path)
        print()

        # Step 3: Generate 4K backgrounds
        print("3. Generating 4K backgrounds (3840x2160)...")
        intro_bg = generate_4k_intro_background(tmp_path, logo_path)
        karaoke_bg = generate_4k_karaoke_background(tmp_path)
        end_bg = generate_4k_end_background(tmp_path, logo_path)
        print()

        # Step 4: Generate CDG backgrounds
        print("4. Generating CDG backgrounds (300x216)...")
        cdg_title_bg = generate_cdg_title_background(tmp_path)
        cdg_instrumental_bg = generate_cdg_instrumental_background(tmp_path)
        print()

        # Step 5: Connect to GCS
        print("5. Connecting to GCS...")
        try:
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET)
            print(f"  Connected to bucket: {GCS_BUCKET}")
        except Exception as e:
            print(f"  ERROR: Failed to connect to GCS: {e}")
            print("  Make sure you've run: gcloud auth application-default login")
            sys.exit(1)
        print()

        # Step 6: Upload theme assets
        print(f"6. Uploading theme assets to themes/{THEME_ID}/assets/...")
        assets = {
            intro_bg: "intro_background.png",
            karaoke_bg: "karaoke_background.png",
            end_bg: "end_background.png",
            font_path: "Inter-SemiBold.ttf",
            cdg_title_bg: "cdg_title_background.gif",
            cdg_instrumental_bg: "cdg_instrumental_background.gif",
        }
        for source, dest_name in assets.items():
            upload_file(bucket, source, f"themes/{THEME_ID}/assets/{dest_name}")
        print()

        # Step 7: Upload theme config
        print(f"7. Uploading theme configuration...")
        theme_config = create_theme_config()
        upload_json(bucket, theme_config, f"themes/{THEME_ID}/style_params.json")
        print()

        # Step 8: Upload tenant logo
        print(f"8. Uploading tenant logo...")
        upload_file(bucket, logo_path, f"tenants/{TENANT_ID}/logo.png")
        print()

        # Step 9: Upload tenant config
        print(f"9. Uploading tenant configuration...")
        tenant_config = create_tenant_config()
        upload_json(bucket, tenant_config, f"tenants/{TENANT_ID}/config.json")
        print()

        # Step 10: Update theme registry
        print(f"10. Updating theme registry...")
        registry_blob = bucket.blob("themes/_metadata.json")
        try:
            registry_data = json.loads(registry_blob.download_as_string())
        except Exception:
            print("  Creating new registry...")
            registry_data = {"version": 1, "themes": []}

        existing_ids = [t["id"] for t in registry_data.get("themes", [])]
        if THEME_ID not in existing_ids:
            registry_data["themes"].append(create_theme_metadata())
            registry_blob.upload_from_string(
                json.dumps(registry_data, indent=2),
                content_type="application/json",
            )
            print(f"  Added '{THEME_ID}' to theme registry")
        else:
            print(f"  Theme '{THEME_ID}' already in registry, skipping")
        print()

    # Step 11: Print summary and next steps
    print("=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print()
    print(f"  Tenant ID:  {TENANT_ID}")
    print(f"  Theme ID:   {THEME_ID}")
    print(f"  Subdomain:  singa.nomadkaraoke.com")
    print()
    print("GCS paths created:")
    print(f"  themes/{THEME_ID}/style_params.json")
    print(f"  themes/{THEME_ID}/assets/  (6 files)")
    print(f"  tenants/{TENANT_ID}/config.json")
    print(f"  tenants/{TENANT_ID}/logo.png")
    print()
    print("─" * 60)
    print("MANUAL STEPS REQUIRED:")
    print("─" * 60)
    print()
    print("1. DNS: Add CNAME record")
    print("   singa.nomadkaraoke.com -> <cloudflare-pages-domain>")
    print()
    print("2. Cloudflare Pages: Add custom domain")
    print("   - Go to Cloudflare Pages > your project > Custom domains")
    print("   - Add 'singa.nomadkaraoke.com'")
    print()
    print("3. SendGrid: Verify sender (if needed)")
    print("   - Verify singa@nomadkaraoke.com as a sender identity")
    print("   - (May already be covered by domain-level verification)")
    print()
    print("4. Test:")
    print("   - API: curl -H 'X-Tenant-ID: singa' https://api.nomadkaraoke.com/api/tenant/config")
    print("   - GCS: gsutil ls gs://karaoke-gen-storage-nomadkaraoke/themes/singa/")
    print("   - GCS: gsutil ls gs://karaoke-gen-storage-nomadkaraoke/tenants/singa/")
    print("   - Web: https://singa.nomadkaraoke.com (after DNS propagation)")
    print()


if __name__ == "__main__":
    main()
