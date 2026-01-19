"""Next.js frontend module for karaoke-gen unified web interface.

This module provides utilities for serving the consolidated Next.js frontend
for both lyrics review and instrumental selection in local CLI mode.

The Next.js frontend is built with `npm run build` in the frontend/ directory
and produces a static export in frontend/out/ which can be served by the
local review servers.
"""

import os
from pathlib import Path
from typing import Optional


# Get the directory containing this module
_MODULE_DIR = Path(__file__).parent.absolute()

# The Next.js static export location relative to the repo root
# When packaged, it will be at karaoke_gen/nextjs_frontend/out/
# In development, it's at frontend/out/
_PACKAGED_DIR = _MODULE_DIR / "out"
_DEV_DIR = _MODULE_DIR.parent.parent / "frontend" / "out"


def get_nextjs_assets_dir() -> Optional[Path]:
    """Get the path to the Next.js static export directory.

    Returns:
        Path to the frontend/out directory containing the Next.js static export,
        or None if the assets are not available.
    """
    # Check packaged location first
    if _PACKAGED_DIR.exists() and (_PACKAGED_DIR / "index.html").exists():
        return _PACKAGED_DIR

    # Check development location
    if _DEV_DIR.exists() and (_DEV_DIR / "index.html").exists():
        return _DEV_DIR

    return None


def is_nextjs_frontend_available() -> bool:
    """Check if the Next.js frontend is available for serving."""
    return get_nextjs_assets_dir() is not None


def get_spa_index_html(assets_dir: Path) -> Path:
    """Get the path to the SPA index.html file for routing."""
    return assets_dir / "index.html"


def get_route_html_path(assets_dir: Path, route: str) -> Optional[Path]:
    """Get the path to a specific route's HTML file.

    For Next.js static export, routes like /app/jobs/local/review
    are pre-rendered to /app/jobs/[...slug].html or similar.

    Args:
        assets_dir: Path to the Next.js static export directory
        route: The URL route being requested (e.g., "/app/jobs/local/review")

    Returns:
        Path to the HTML file for this route, or None if not found.
    """
    # Clean the route
    route = route.strip("/")
    if not route:
        return assets_dir / "index.html"

    # Try exact path match first
    exact_path = assets_dir / route / "index.html"
    if exact_path.exists():
        return exact_path

    # Try .html extension
    html_path = assets_dir / f"{route}.html"
    if html_path.exists():
        return html_path

    # For dynamic routes like /app/jobs/[[...slug]],
    # Next.js creates /app/jobs.html or /app/jobs/[[...slug]].html
    # We need to fall back to the catch-all route
    parts = route.split("/")
    for i in range(len(parts), 0, -1):
        parent = "/".join(parts[:i])
        # Try the [...slug] catch-all pattern
        catch_all = assets_dir / parent / "[[...slug]].html"
        if catch_all.exists():
            return catch_all
        # Try parent index
        parent_index = assets_dir / parent / "index.html"
        if parent_index.exists():
            return parent_index

    # Fallback to main index.html for SPA routing
    return assets_dir / "index.html"
