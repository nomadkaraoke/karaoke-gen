"""Service for generating personalized referral flyer PDFs."""
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "printables")

CHROMIUM_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

VALID_THEMES = {"light", "dark"}
MAX_QR_DATA_URL_BYTES = 500_000


class FlyerError(Exception):
    """Error during flyer generation."""


class FlyerService:
    """Generates personalized referral flyer PDFs."""

    def __init__(self):
        self._chromium_path: str | None = None

    def _get_chromium(self) -> str:
        """Lazily find Chromium binary. Only needed when generating PDFs."""
        if self._chromium_path:
            return self._chromium_path
        for path in CHROMIUM_PATHS:
            if os.path.exists(path):
                self._chromium_path = path
                return path
        for name in ["chromium", "chromium-browser", "google-chrome"]:
            found = shutil.which(name)
            if found:
                self._chromium_path = found
                return found
        raise FlyerError("Chromium not found. Install chromium to generate flyer PDFs.")

    def _validate_qr_data_url(self, qr_data_url: str) -> None:
        if not qr_data_url.startswith("data:image/"):
            raise FlyerError("Invalid QR data URL: must be a data:image/ URL")
        if len(qr_data_url) > MAX_QR_DATA_URL_BYTES:
            raise FlyerError(f"QR data URL too large (>{MAX_QR_DATA_URL_BYTES} bytes)")

    def _render_template(
        self,
        theme: str,
        referral_code: str,
        discount_percent: int,
        qr_data_url: str,
    ) -> str:
        if theme not in VALID_THEMES:
            raise FlyerError(f"Invalid theme: {theme}. Must be one of: {VALID_THEMES}")

        suffix = "-dark" if theme == "dark" else ""
        template_path = os.path.join(TEMPLATE_DIR, f"website-referral-flyer{suffix}.html")

        with open(template_path) as f:
            html = f.read()

        html = html.replace("{{REFERRAL_CODE}}", referral_code.upper())
        html = html.replace("{{REFERRAL_CODE_LOWER}}", referral_code.lower())
        html = html.replace("{{DISCOUNT_PERCENT}}", str(discount_percent))
        html = html.replace("{{QR_DATA_URL}}", qr_data_url)

        return html

    def generate_pdf(
        self,
        theme: str,
        referral_code: str,
        discount_percent: int,
        qr_data_url: str,
    ) -> bytes:
        """Generate a personalized flyer PDF. Returns raw PDF bytes."""
        self._validate_qr_data_url(qr_data_url)
        html = self._render_template(theme, referral_code, discount_percent, qr_data_url)

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as tmp_html:
            tmp_html.write(html)
            tmp_html_path = tmp_html.name

        tmp_pdf_path = tmp_html_path.replace(".html", ".pdf")

        try:
            cmd = [
                self._get_chromium(),
                "--headless",
                f"--print-to-pdf={tmp_pdf_path}",
                "--no-margins",
                "--print-background",
                "--no-pdf-header-footer",
                "--virtual-time-budget=5000",
                "--paper-width=8.5",
                "--paper-height=11",
                "--disable-gpu",
                "--no-sandbox",
                tmp_html_path,
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                logger.error("Chromium PDF generation failed: %s", result.stderr)
                raise FlyerError(f"PDF generation failed: {result.stderr[:200]}")

            if not os.path.exists(tmp_pdf_path):
                raise FlyerError("PDF generation failed: no output file created")

            with open(tmp_pdf_path, "rb") as f:
                return f.read()

        finally:
            for path in [tmp_html_path, tmp_pdf_path]:
                try:
                    os.unlink(path)
                except OSError:
                    pass


_flyer_service = None


def get_flyer_service() -> FlyerService:
    global _flyer_service
    if _flyer_service is None:
        _flyer_service = FlyerService()
    return _flyer_service
