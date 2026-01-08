#!/usr/bin/env python3
"""
Setup script for Vocal Star white-label tenant.

This script:
1. Uploads Vocal Star theme assets to GCS
2. Creates the theme configuration
3. Creates the tenant configuration
4. Uploads the logo

Usage:
    # Set environment variable for resources path (optional)
    export VOCALSTAR_RESOURCES=/path/to/VocalStar/Resources

    # Run from the project root
    python scripts/setup-vocalstar-tenant.py

    # Or pass path as argument
    python scripts/setup-vocalstar-tenant.py /path/to/VocalStar/Resources

Environment Variables:
    VOCALSTAR_RESOURCES: Path to VocalStar resource files (images, fonts)

Expected resource layout:
    - vocal-star-title-background-black.4k.png
    - blk-YT-background-wall3.fw-upscaled.jpg
    - vocal-star-end-card-black.4k.png
    - OswaldFont/static/Oswald-SemiBold.ttf
    - cdg-instrumental-background-vocalstar.gif
    - cdg-title-screen-background-vocalstar-simple.gif
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import storage

# Configuration
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"
TENANT_ID = "vocalstar"
THEME_ID = "vocalstar"

# Default path (developer-specific, override via env var or CLI arg)
DEFAULT_RESOURCES_PATH = (
    "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/"
    "Tracks-NonPublished/VocalStar/Resources"
)


def get_resources_path() -> Path:
    """Get the VocalStar resources path from env var, CLI arg, or default."""
    # Check CLI argument first
    parser = argparse.ArgumentParser(description="Setup Vocal Star tenant")
    parser.add_argument(
        "resources_path",
        nargs="?",
        help="Path to VocalStar resource files"
    )
    args, _ = parser.parse_known_args()

    if args.resources_path:
        return Path(args.resources_path)

    # Check environment variable
    env_path = os.environ.get("VOCALSTAR_RESOURCES")
    if env_path:
        return Path(env_path)

    # Fall back to default
    return Path(DEFAULT_RESOURCES_PATH)


# Source paths for Vocal Star assets (resolved at runtime)
VOCALSTAR_RESOURCES = get_resources_path()

# Asset mappings: source filename -> GCS destination
THEME_ASSETS = {
    "vocal-star-title-background-black.4k.png": "intro_background.png",
    "blk-YT-background-wall3.fw-upscaled.jpg": "karaoke_background.jpg",
    "vocal-star-end-card-black.4k.png": "end_background.png",
    "OswaldFont/static/Oswald-SemiBold.ttf": "Oswald-SemiBold.ttf",
    "cdg-instrumental-background-vocalstar.gif": "cdg_instrumental_background.gif",
    "cdg-title-screen-background-vocalstar-simple.gif": "cdg_title_background.gif",
}

# Logo path (in worktree)
LOGO_SOURCE = Path(__file__).parent.parent / "vocalstar-logo.jpg"


def upload_file(bucket, source_path: Path, dest_path: str) -> str:
    """Upload a file to GCS and return the gs:// URL."""
    blob = bucket.blob(dest_path)
    blob.upload_from_filename(str(source_path))
    print(f"  Uploaded: {source_path.name} -> gs://{GCS_BUCKET}/{dest_path}")
    return f"gs://{GCS_BUCKET}/{dest_path}"


def create_theme_config() -> dict:
    """Create the Vocal Star theme configuration."""
    return {
        "intro": {
            "video_duration": 5,
            "existing_image": None,
            "background_color": "#000000",
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/intro_background.png",
            "font": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Oswald-SemiBold.ttf",
            "title_region": "370,450,3100,480",
            "title_text_transform": None,
            "title_color": "#ffff00",
            "title_gradient": {
                "color1": "#ffffff",
                "color2": "#ffff00",
                "direction": "vertical",
                "start": 0.4,
                "stop": 0.8
            },
            "artist_region": "370,1200,3100,480",
            "artist_text_transform": None,
            "artist_color": "#ffff00",
            "artist_gradient": {
                "color1": "#ffffff",
                "color2": "#ffff00",
                "direction": "vertical",
                "start": 0.3,
                "stop": 0.6
            },
            "extra_text": None,
            "extra_text_color": "#ffffff",
            "extra_text_region": None,
            "extra_text_gradient": None
        },
        "karaoke": {
            "background_color": "#000000",
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/karaoke_background.jpg",
            "font": "Oswald SemiBold",
            "font_path": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Oswald-SemiBold.ttf",
            "font_size": 420,
            "top_padding": 100,
            "ass_name": "VocalStar",
            "primary_color": "0, 108, 249, 255",
            "secondary_color": "252, 223, 1, 255",
            "outline_color": "26, 58, 235, 255",
            "back_color": "0, 255, 0, 255",
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
            "encoding": 0
        },
        "end": {
            "video_duration": 5,
            "existing_image": None,
            "background_color": "#000000",
            "background_image": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/end_background.png",
            "font": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Oswald-SemiBold.ttf",
            "title_region": None,
            "title_text_transform": "none",
            "title_color": "#ffff00",
            "title_gradient": None,
            "artist_region": None,
            "artist_text_transform": "none",
            "artist_color": "#ffff00",
            "artist_gradient": None,
            "extra_text": None,
            "extra_text_color": None,
            "extra_text_region": None,
            "extra_text_gradient": None
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
            "background_color": "#000000",
            "border_color": "#ffff00",
            "title_color": "#ffff00",
            "artist_color": "#ffff00",
            "outro_line1_color": "#ffff00",
            "outro_line2_color": "#ffff00",
            "active_fill": "#006CF9",
            "active_stroke": "#000000",
            "inactive_fill": "#ffff00",
            "inactive_stroke": "#000000",
            "instrumental_font_color": "#ffff00",
            "font_path": f"gs://{GCS_BUCKET}/themes/{THEME_ID}/assets/Oswald-SemiBold.ttf",
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
            "outro_line1_line2_gap": 30
        }
    }


def create_tenant_config() -> dict:
    """Create the Vocal Star tenant configuration."""
    return {
        "id": TENANT_ID,
        "name": "Vocal Star",
        "subdomain": "vocalstar.nomadkaraoke.com",
        "is_active": True,
        "branding": {
            "logo_url": f"gs://{GCS_BUCKET}/tenants/{TENANT_ID}/logo.jpg",
            "logo_height": 60,
            "primary_color": "#ffff00",  # Yellow
            "secondary_color": "#006CF9",  # Blue
            "accent_color": "#ffffff",  # White
            "background_color": "#000000",  # Black
            "favicon_url": None,  # TODO: Create favicon
            "site_title": "Vocal Star Karaoke Generator",
            "tagline": "Whoever You Are, Be a Vocal Star!"
        },
        "features": {
            "audio_search": False,  # Vocal Star provides their own audio
            "file_upload": True,
            "youtube_url": False,
            "youtube_upload": False,  # Download only
            "dropbox_upload": False,
            "gdrive_upload": False,
            "theme_selection": False,  # Always use Vocal Star theme
            "color_overrides": False,
            "enable_cdg": True,
            "enable_4k": True,
            "admin_access": False
        },
        "defaults": {
            "theme_id": THEME_ID,
            "locked_theme": THEME_ID,  # Lock to Vocal Star theme - users cannot change
            "distribution_mode": "download_only",
            "brand_prefix": "VSTAR",
            "youtube_description_template": None
        },
        "auth": {
            "allowed_email_domains": ["vocal-star.com", "vocalstarmusic.com"],
            "require_email_domain": True,
            "fixed_token_ids": [],  # Will be populated when tokens are created
            "sender_email": "vocalstar@nomadkaraoke.com"
        }
    }


def create_theme_metadata() -> dict:
    """Create the theme metadata entry for the registry."""
    return {
        "id": THEME_ID,
        "name": "Vocal Star",
        "description": "Official Vocal Star karaoke style with yellow/blue color scheme",
        "is_default": False
    }


def main():
    print("=" * 60)
    print("Vocal Star Tenant Setup")
    print("=" * 60)

    # Verify source files exist
    print("\n1. Verifying source files...")
    missing_files = []
    for source_file in THEME_ASSETS.keys():
        source_path = VOCALSTAR_RESOURCES / source_file
        if not source_path.exists():
            missing_files.append(str(source_path))
            print(f"  MISSING: {source_path}")
        else:
            print(f"  OK: {source_file}")

    if not LOGO_SOURCE.exists():
        missing_files.append(str(LOGO_SOURCE))
        print(f"  MISSING: {LOGO_SOURCE}")
    else:
        print(f"  OK: {LOGO_SOURCE.name}")

    if missing_files:
        print(f"\nERROR: {len(missing_files)} files missing. Cannot continue.")
        sys.exit(1)

    # Initialize GCS client
    print("\n2. Connecting to GCS...")
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        print(f"  Connected to bucket: {GCS_BUCKET}")
    except Exception as e:
        print(f"  ERROR: Failed to connect to GCS: {e}")
        sys.exit(1)

    # Upload theme assets
    print(f"\n3. Uploading theme assets to themes/{THEME_ID}/assets/...")
    for source_file, dest_file in THEME_ASSETS.items():
        source_path = VOCALSTAR_RESOURCES / source_file
        dest_path = f"themes/{THEME_ID}/assets/{dest_file}"
        upload_file(bucket, source_path, dest_path)

    # Upload theme config
    print(f"\n4. Creating theme configuration...")
    theme_config = create_theme_config()
    theme_blob = bucket.blob(f"themes/{THEME_ID}/style_params.json")
    theme_blob.upload_from_string(
        json.dumps(theme_config, indent=2),
        content_type="application/json"
    )
    print(f"  Created: gs://{GCS_BUCKET}/themes/{THEME_ID}/style_params.json")

    # Upload tenant logo
    print(f"\n5. Uploading tenant logo...")
    upload_file(bucket, LOGO_SOURCE, f"tenants/{TENANT_ID}/logo.jpg")

    # Upload tenant config
    print(f"\n6. Creating tenant configuration...")
    tenant_config = create_tenant_config()
    tenant_blob = bucket.blob(f"tenants/{TENANT_ID}/config.json")
    tenant_blob.upload_from_string(
        json.dumps(tenant_config, indent=2),
        content_type="application/json"
    )
    print(f"  Created: gs://{GCS_BUCKET}/tenants/{TENANT_ID}/config.json")

    # Update theme registry (add Vocal Star theme)
    print(f"\n7. Updating theme registry...")
    registry_blob = bucket.blob("themes/_metadata.json")
    try:
        registry_data = json.loads(registry_blob.download_as_string())
    except Exception:
        print("  Creating new registry...")
        registry_data = {"version": 1, "themes": []}

    # Check if theme already exists
    existing_ids = [t["id"] for t in registry_data.get("themes", [])]
    if THEME_ID not in existing_ids:
        registry_data["themes"].append(create_theme_metadata())
        registry_blob.upload_from_string(
            json.dumps(registry_data, indent=2),
            content_type="application/json"
        )
        print(f"  Added {THEME_ID} to theme registry")
    else:
        print(f"  Theme {THEME_ID} already in registry")

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print(f"\nTenant: {TENANT_ID}")
    print(f"Subdomain: vocalstar.nomadkaraoke.com")
    print(f"Theme: {THEME_ID}")
    print(f"\nNext steps:")
    print("1. Configure DNS: Add CNAME for vocalstar.nomadkaraoke.com")
    print("2. Update Cloudflare Pages to accept the subdomain")
    print("3. Test the portal at https://vocalstar.nomadkaraoke.com")


if __name__ == "__main__":
    main()
