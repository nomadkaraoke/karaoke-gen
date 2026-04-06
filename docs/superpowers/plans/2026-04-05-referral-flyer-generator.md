# Referral Marketing Flyer Generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Generate Flyer" feature to the QR Code dialog that produces a personalized print-ready PDF marketing flyer using headless Chromium.

**Architecture:** Frontend sends the user's styled QR code (as a base64 data URL) + theme choice to a new backend endpoint. Backend injects the QR code, referral code, and discount into an HTML template, renders it to PDF with headless Chromium, and returns the PDF. Templates are copied from kjbox with placeholder substitution.

**Tech Stack:** Python (FastAPI), headless Chromium (`--print-to-pdf`), HTML/CSS templates, React/TypeScript frontend, `qr-code-styling` (existing)

**Spec:** `docs/archive/2026-04-05-referral-flyer-generator-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/templates/printables/website-referral-flyer.html` | Light theme template with placeholders |
| Create | `backend/templates/printables/website-referral-flyer-dark.html` | Dark theme template with placeholders |
| Create | `backend/services/flyer_service.py` | Template rendering + Chromium PDF generation |
| Create | `backend/tests/test_flyer_service.py` | Unit tests for flyer service |
| Modify | `backend/api/routes/referrals.py` | Add `POST /me/flyer` endpoint |
| Modify | `backend/Dockerfile.base` | Add `chromium` apt package |
| Modify | `frontend/components/referrals/QRCodeDialog.tsx` | Add theme picker + Generate Flyer button |
| Modify | `frontend/lib/api.ts` | Add `generateFlyer()` function |
| Modify | `frontend/messages/en.json` | Add flyer i18n strings |

---

### Task 1: Local Chrome verification (proof of concept)

Verify the template + headless Chrome PDF approach works locally before writing any production code.

**Files:**
- Create (temporary): `backend/templates/printables/website-referral-flyer.html`

- [ ] **Step 1: Copy and templatize the light flyer**

Copy `/Users/andrew/Projects/nomadkaraoke/kjbox/printables/website-referral-flyer.html` to `backend/templates/printables/website-referral-flyer.html`.

Then make 3 substitutions in the new file:

1. Replace `src="qr-code-angel.svg"` with `src="{{QR_DATA_URL}}"` (line 330 of the original)
2. Replace `<span class="referral-code">ANGEL</span>` with `<span class="referral-code">{{REFERRAL_CODE}}</span>` (line 334)
3. Replace `First track free + 10% off with this link` with `First track free + {{DISCOUNT_PERCENT}}% off with this link` (line 336)
4. Replace `alt="QR Code - nomadkaraoke.com/r/angel"` with `alt="QR Code - nomadkaraoke.com/r/{{REFERRAL_CODE_LOWER}}"` (line 330)

- [ ] **Step 2: Write a quick test script**

Create a temporary script `backend/test_flyer_local.py` (gitignored — add `test_flyer_local.py` to `.gitignore` or just delete after):

```python
#!/usr/bin/env python3
"""Local verification: generate a flyer PDF using Chrome headless."""
import subprocess
import tempfile
import os
import sys

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates/printables/website-referral-flyer.html")

# Read template
with open(TEMPLATE_PATH) as f:
    html = f.read()

# Substitute placeholders
html = html.replace("{{REFERRAL_CODE}}", "TESTCODE")
html = html.replace("{{REFERRAL_CODE_LOWER}}", "testcode")
html = html.replace("{{DISCOUNT_PERCENT}}", "10")
# Use a simple black QR placeholder (1x1 black pixel as data URL for now)
html = html.replace("{{QR_DATA_URL}}", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# Write to temp file
with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as tmp:
    tmp.write(html)
    tmp_html = tmp.name

output_pdf = "/tmp/test-flyer-output.pdf"

# Find Chrome
chrome_paths = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]
chrome = None
for p in chrome_paths:
    if os.path.exists(p):
        chrome = p
        break

if not chrome:
    print("ERROR: No Chrome/Chromium found")
    sys.exit(1)

print(f"Using: {chrome}")
print(f"Template: {tmp_html}")
print(f"Output: {output_pdf}")

cmd = [
    chrome,
    "--headless",
    f"--print-to-pdf={output_pdf}",
    "--no-margins",
    "--print-background",
    "--no-pdf-header-footer",
    "--virtual-time-budget=5000",
    "--paper-width=8.5",
    "--paper-height=11",
    tmp_html,
]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
os.unlink(tmp_html)

if result.returncode != 0:
    print(f"FAILED (exit {result.returncode})")
    print(result.stderr)
    sys.exit(1)

if os.path.exists(output_pdf):
    size = os.path.getsize(output_pdf)
    print(f"SUCCESS: {output_pdf} ({size} bytes)")
    # Open the PDF for visual inspection
    subprocess.run(["open", output_pdf])
else:
    print("FAILED: No output PDF generated")
    sys.exit(1)
```

- [ ] **Step 3: Run the test**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
python3 backend/test_flyer_local.py
```

Expected: PDF opens in Preview showing the flyer with "TESTCODE" referral code and a placeholder QR image. Verify: correct layout, gradients render, fonts load, no second blank page, no margins.

- [ ] **Step 4: Clean up and commit the template**

Delete the test script:
```bash
rm backend/test_flyer_local.py
```

Commit the templatized flyer:
```bash
git add backend/templates/printables/website-referral-flyer.html
git commit -m "feat(referral): add templatized light flyer from kjbox printables"
```

---

### Task 2: Templatize dark flyer + add Chromium to Dockerfile

**Files:**
- Create: `backend/templates/printables/website-referral-flyer-dark.html`
- Modify: `backend/Dockerfile.base`

- [ ] **Step 1: Copy and templatize the dark flyer**

Copy `/Users/andrew/Projects/nomadkaraoke/kjbox/printables/website-referral-flyer-dark.html` to `backend/templates/printables/website-referral-flyer-dark.html`.

Make the same 4 substitutions as Task 1 Step 1:

1. `src="qr-code-angel.svg"` → `src="{{QR_DATA_URL}}"`
2. `<span class="referral-code">ANGEL</span>` → `<span class="referral-code">{{REFERRAL_CODE}}</span>`
3. `First track free + 10% off with this link` → `First track free + {{DISCOUNT_PERCENT}}% off with this link`
4. `alt="QR Code - nomadkaraoke.com/r/angel"` → `alt="QR Code - nomadkaraoke.com/r/{{REFERRAL_CODE_LOWER}}"`

- [ ] **Step 2: Add Chromium to Dockerfile.base**

In `backend/Dockerfile.base`, add `chromium` to the first `apt-get install` block (the system dependencies section, around line 19-26). Add it after `xz-utils`:

```dockerfile
RUN apt-get update && apt-get install -y \
    libsndfile1 \
    libsox-dev \
    sox \
    build-essential \
    curl \
    xz-utils \
    chromium \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add backend/templates/printables/website-referral-flyer-dark.html backend/Dockerfile.base
git commit -m "feat(referral): add dark flyer template and chromium to Dockerfile"
```

---

### Task 3: Implement flyer service with tests

**Files:**
- Create: `backend/services/flyer_service.py`
- Create: `backend/tests/test_flyer_service.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_flyer_service.py`:

```python
"""Tests for flyer PDF generation service."""
import os
from unittest.mock import patch, MagicMock

import pytest

from backend.services.flyer_service import FlyerService, FlyerError


class TestFlyerService:
    """Tests for FlyerService."""

    def setup_method(self):
        self.service = FlyerService()

    def test_render_template_light_substitutes_placeholders(self):
        html = self.service._render_template(
            theme="light",
            referral_code="MYCODE",
            discount_percent=15,
            qr_data_url="data:image/png;base64,abc123",
        )
        assert "MYCODE" in html
        assert "{{REFERRAL_CODE}}" not in html
        assert "{{QR_DATA_URL}}" not in html
        assert "{{DISCOUNT_PERCENT}}" not in html
        assert "{{REFERRAL_CODE_LOWER}}" not in html
        assert "15% off" in html
        assert "data:image/png;base64,abc123" in html
        assert "mycode" in html  # lowercase version in alt text

    def test_render_template_dark_substitutes_placeholders(self):
        html = self.service._render_template(
            theme="dark",
            referral_code="DARKCODE",
            discount_percent=20,
            qr_data_url="data:image/png;base64,xyz789",
        )
        assert "DARKCODE" in html
        assert "{{REFERRAL_CODE}}" not in html
        assert "data:image/png;base64,xyz789" in html
        assert "20% off" in html

    def test_render_template_invalid_theme_raises(self):
        with pytest.raises(FlyerError, match="Invalid theme"):
            self.service._render_template(
                theme="neon",
                referral_code="CODE",
                discount_percent=10,
                qr_data_url="data:image/png;base64,abc",
            )

    def test_render_template_uppercases_referral_code(self):
        html = self.service._render_template(
            theme="light",
            referral_code="mycode",
            discount_percent=10,
            qr_data_url="data:image/png;base64,abc",
        )
        # The code in the URL display should be uppercased
        assert '<span class="referral-code">MYCODE</span>' in html

    @patch("backend.services.flyer_service.subprocess.run")
    def test_generate_pdf_calls_chromium_with_correct_flags(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        # Mock the temp file to exist
        with patch("backend.services.flyer_service.tempfile.NamedTemporaryFile") as mock_tmp:
            mock_tmp_instance = MagicMock()
            mock_tmp_instance.__enter__ = MagicMock(return_value=mock_tmp_instance)
            mock_tmp_instance.__exit__ = MagicMock(return_value=False)
            mock_tmp_instance.name = "/tmp/test.html"
            mock_tmp.return_value = mock_tmp_instance

            with patch("builtins.open", MagicMock()):
                with patch("os.path.exists", return_value=True):
                    with patch("os.unlink"):
                        self.service.generate_pdf(
                            theme="light",
                            referral_code="TEST",
                            discount_percent=10,
                            qr_data_url="data:image/png;base64,abc",
                        )

        # Verify chromium was called with the right flags
        call_args = mock_run.call_args[0][0]
        assert "--headless" in call_args
        assert "--no-margins" in call_args
        assert "--print-background" in call_args
        assert "--no-pdf-header-footer" in call_args
        assert "--virtual-time-budget=5000" in call_args
        assert "--paper-width=8.5" in call_args
        assert "--paper-height=11" in call_args

    def test_validate_qr_data_url_rejects_non_data_url(self):
        with pytest.raises(FlyerError, match="Invalid QR"):
            self.service._validate_qr_data_url("https://evil.com/qr.png")

    def test_validate_qr_data_url_rejects_too_large(self):
        huge_url = "data:image/png;base64," + "A" * 600_000
        with pytest.raises(FlyerError, match="too large"):
            self.service._validate_qr_data_url(huge_url)

    def test_validate_qr_data_url_accepts_valid(self):
        # Should not raise
        self.service._validate_qr_data_url("data:image/png;base64,abc123")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
python -m pytest backend/tests/test_flyer_service.py -v 2>&1 | tail -20
```

Expected: FAIL — `backend.services.flyer_service` module doesn't exist.

- [ ] **Step 3: Implement the flyer service**

Create `backend/services/flyer_service.py`:

```python
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
        self._chromium_path = self._find_chromium()

    def _find_chromium(self) -> str:
        for path in CHROMIUM_PATHS:
            if os.path.exists(path):
                return path
        # Also try shutil.which
        for name in ["chromium", "chromium-browser", "google-chrome"]:
            found = shutil.which(name)
            if found:
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
        """Generate a personalized flyer PDF.

        Returns the raw PDF bytes.
        """
        self._validate_qr_data_url(qr_data_url)
        html = self._render_template(theme, referral_code, discount_percent, qr_data_url)

        # Write HTML to temp file (Chromium needs a file path)
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as tmp_html:
            tmp_html.write(html)
            tmp_html_path = tmp_html.name

        tmp_pdf_path = tmp_html_path.replace(".html", ".pdf")

        try:
            cmd = [
                self._chromium_path,
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
            # Clean up temp files
            for path in [tmp_html_path, tmp_pdf_path]:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# Singleton
_flyer_service = None


def get_flyer_service() -> FlyerService:
    global _flyer_service
    if _flyer_service is None:
        _flyer_service = FlyerService()
    return _flyer_service
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
python -m pytest backend/tests/test_flyer_service.py -v 2>&1 | tail -20
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add backend/services/flyer_service.py backend/tests/test_flyer_service.py
git commit -m "feat(referral): add flyer PDF generation service with tests"
```

---

### Task 4: Add flyer endpoint to referral routes

**Files:**
- Modify: `backend/api/routes/referrals.py`

- [ ] **Step 1: Add the flyer endpoint**

In `backend/api/routes/referrals.py`, add after the existing imports at the top:

```python
from fastapi.responses import Response
from pydantic import BaseModel, Field
from backend.services.flyer_service import get_flyer_service, FlyerError
```

Add a request model after the existing imports:

```python
class GenerateFlyerRequest(BaseModel):
    theme: str = Field(..., pattern="^(light|dark)$")
    qr_data_url: str = Field(..., max_length=500_000)
```

Add the endpoint after the `start_stripe_connect` endpoint (around line 118), before the Admin section:

```python
@router.post("/me/flyer")
async def generate_flyer(
    request: GenerateFlyerRequest,
    auth=Depends(require_auth),
):
    """Generate a personalized referral flyer PDF."""
    service = get_referral_service()
    link = service.get_or_create_link(auth.user_email)

    try:
        flyer_service = get_flyer_service()
        pdf_bytes = flyer_service.generate_pdf(
            theme=request.theme,
            referral_code=link.code,
            discount_percent=link.discount_percent,
            qr_data_url=request.qr_data_url,
        )
    except FlyerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="nomad-karaoke-referral-flyer.pdf"',
        },
    )
```

- [ ] **Step 2: Run backend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
python -m pytest backend/tests/ -v --timeout=30 2>&1 | tail -30
```

Expected: All tests pass (new endpoint doesn't break existing tests).

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add backend/api/routes/referrals.py
git commit -m "feat(referral): add POST /me/flyer endpoint for PDF generation"
```

---

### Task 5: Docker verification

Build the base image locally with Chromium and verify PDF generation works inside the container.

**Files:** None changed — this is a verification step.

- [ ] **Step 1: Build the Docker base image locally**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
docker build -f backend/Dockerfile.base -t karaoke-backend-base:local .
```

This will take a while (~5-10 min) as it installs Chromium + all deps. Watch for errors.

- [ ] **Step 2: Test Chromium works inside the container**

```bash
docker run --rm karaoke-backend-base:local chromium --version
```

Expected: Prints Chromium version (e.g., `Chromium 120.0.6099.224 built on Debian ...`).

- [ ] **Step 3: Test PDF generation inside the container**

```bash
docker run --rm -v $(pwd)/backend/templates:/app/backend/templates karaoke-backend-base:local \
  python3 -c "
import subprocess, tempfile, os
# Read template
with open('/app/backend/templates/printables/website-referral-flyer.html') as f:
    html = f.read()
html = html.replace('{{REFERRAL_CODE}}', 'DOCKERTEST')
html = html.replace('{{REFERRAL_CODE_LOWER}}', 'dockertest')
html = html.replace('{{DISCOUNT_PERCENT}}', '10')
html = html.replace('{{QR_DATA_URL}}', 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
tmp = '/tmp/test.html'
with open(tmp, 'w') as f:
    f.write(html)
result = subprocess.run([
    'chromium', '--headless', '--print-to-pdf=/tmp/out.pdf',
    '--no-margins', '--print-background', '--no-pdf-header-footer',
    '--virtual-time-budget=5000', '--paper-width=8.5', '--paper-height=11',
    '--disable-gpu', '--no-sandbox', tmp
], capture_output=True, text=True, timeout=30)
if result.returncode != 0:
    print('FAILED:', result.stderr[:500])
elif os.path.exists('/tmp/out.pdf'):
    print(f'SUCCESS: PDF generated ({os.path.getsize(\"/tmp/out.pdf\")} bytes)')
else:
    print('FAILED: no output')
"
```

Expected: `SUCCESS: PDF generated (XXXXX bytes)`.

- [ ] **Step 4: Extract and verify the Docker-generated PDF**

```bash
# Run again but copy the PDF out
docker run --rm -v $(pwd)/backend/templates:/app/backend/templates -v /tmp:/output karaoke-backend-base:local \
  python3 -c "
import subprocess, os
with open('/app/backend/templates/printables/website-referral-flyer.html') as f:
    html = f.read()
html = html.replace('{{REFERRAL_CODE}}', 'DOCKERTEST')
html = html.replace('{{REFERRAL_CODE_LOWER}}', 'dockertest')
html = html.replace('{{DISCOUNT_PERCENT}}', '10')
html = html.replace('{{QR_DATA_URL}}', 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
with open('/tmp/test.html', 'w') as f:
    f.write(html)
subprocess.run([
    'chromium', '--headless', '--print-to-pdf=/output/docker-flyer-test.pdf',
    '--no-margins', '--print-background', '--no-pdf-header-footer',
    '--virtual-time-budget=5000', '--paper-width=8.5', '--paper-height=11',
    '--disable-gpu', '--no-sandbox', '/tmp/test.html'
], capture_output=True, text=True, timeout=30)
print(f'PDF size: {os.path.getsize(\"/output/docker-flyer-test.pdf\")} bytes')
"
open /tmp/docker-flyer-test.pdf
```

Expected: PDF opens showing the flyer with correct layout, fonts (Google Fonts loaded via `--virtual-time-budget`), gradients, and "DOCKERTEST" referral code. No second blank page, no margins, no URL header/footer.

If fonts are missing or rendering is wrong, troubleshoot before proceeding. Common fixes:
- Add `fonts-liberation` to apt-get for fallback fonts
- Increase `--virtual-time-budget` if Google Fonts don't load in time
- Add `--font-render-hinting=none` for better font rendering

- [ ] **Step 5: Report result**

No commit needed — this is a verification gate. If it passes, proceed. If it fails, fix the Dockerfile or template before continuing.

---

### Task 6: Frontend — add flyer generation to QRCodeDialog

**Files:**
- Modify: `frontend/components/referrals/QRCodeDialog.tsx`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Add i18n strings**

Add to the `"referrals"` namespace in `frontend/messages/en.json`, after the existing QR strings:

```json
"flyerTheme": "Flyer Theme",
"flyerThemeLight": "Light",
"flyerThemeDark": "Dark",
"flyerGenerate": "Generate Flyer",
"flyerGenerating": "Generating..."
```

- [ ] **Step 2: Add generateFlyer to API client**

Add to `frontend/lib/api.ts`, after the `startConnectOnboarding` function (around line 3550):

```typescript
export async function generateFlyer(theme: 'light' | 'dark', qrDataUrl: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/referrals/me/flyer`, {
    method: 'POST',
    headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme, qr_data_url: qrDataUrl }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to generate flyer' }));
    throw new Error(error.detail || 'Failed to generate flyer');
  }
  return response.blob();
}
```

- [ ] **Step 3: Add flyer generation to QRCodeDialog**

In `frontend/components/referrals/QRCodeDialog.tsx`, make these changes:

**Add import** at the top:

```typescript
import { generateFlyer } from '@/lib/api';
```

**Add state** inside the component (after `const debounceRef`):

```typescript
const [flyerTheme, setFlyerTheme] = useState<'light' | 'dark'>('light');
const [flyerLoading, setFlyerLoading] = useState(false);
```

**Add the handleGenerateFlyer function** after `handleDownload`:

```typescript
const handleGenerateFlyer = async () => {
  setFlyerLoading(true);
  try {
    // Get QR as PNG data URL from the qr-code-styling instance
    let qrDataUrl: string;
    if (qrRef.current) {
      const blob = await qrRef.current.getRawData('png');
      if (blob) {
        qrDataUrl = await new Promise<string>((resolve) => {
          const reader = new FileReader();
          reader.onloadend = () => resolve(reader.result as string);
          reader.readAsDataURL(blob);
        });
      } else {
        throw new Error('Failed to generate QR code image');
      }
    } else {
      throw new Error('QR code not initialized');
    }

    const pdfBlob = await generateFlyer(flyerTheme, qrDataUrl);
    const url = URL.createObjectURL(pdfBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'nomad-karaoke-referral-flyer.pdf';
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error('Failed to generate flyer:', err);
  } finally {
    setFlyerLoading(false);
  }
};
```

**Add the flyer section in the dialog footer**, replacing the existing `<DialogFooter>` block with:

```tsx
<DialogFooter>
  <div className="w-full space-y-3">
    {/* Flyer generation */}
    <div className="flex items-center gap-3 pt-2 border-t border-border">
      <span className="text-sm font-medium text-foreground">{t('flyerTheme')}</span>
      <div className="flex gap-1.5">
        {(['light', 'dark'] as const).map(theme => (
          <button
            key={theme}
            onClick={() => setFlyerTheme(theme)}
            className={`px-2 py-1 text-xs rounded border transition-colors ${
              flyerTheme === theme
                ? 'border-primary bg-primary/10 text-primary font-medium'
                : 'border-border text-muted-foreground hover:border-primary/50'
            }`}
          >
            {t(theme === 'light' ? 'flyerThemeLight' : 'flyerThemeDark')}
          </button>
        ))}
      </div>
      <button
        onClick={handleGenerateFlyer}
        disabled={flyerLoading}
        className="ml-auto px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center gap-2 disabled:opacity-50"
      >
        {flyerLoading ? t('flyerGenerating') : t('flyerGenerate')}
      </button>
    </div>
    {/* QR download buttons */}
    <div className="flex gap-2 sm:justify-end">
      <button
        onClick={() => handleDownload('png')}
        className="flex-1 sm:flex-none px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center justify-center gap-2"
      >
        <Download className="w-4 h-4" />
        {t('qrDownloadPng')}
      </button>
      <button
        onClick={() => handleDownload('svg')}
        className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary"
      >
        <Download className="w-4 h-4" />
        {t('qrDownloadSvg')}
      </button>
    </div>
  </div>
</DialogFooter>
```

- [ ] **Step 4: Run frontend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx jest components/referrals/__tests__/QRCodeDialog.test.tsx --no-coverage 2>&1 | tail -20
```

Expected: Existing tests still pass. The new flyer UI elements are present but don't break existing tests (they're just buttons and toggles). If the "Generate Flyer" text assertion is needed, add a quick test:

```typescript
it('renders flyer generation controls', () => {
  renderDialog();
  expect(screen.getByText('Generate Flyer')).toBeInTheDocument();
  expect(screen.getByText('Light')).toBeInTheDocument();
  expect(screen.getByText('Dark')).toBeInTheDocument();
});
```

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/components/referrals/QRCodeDialog.tsx frontend/lib/api.ts frontend/messages/en.json
git commit -m "feat(referral): add flyer generation UI to QR code dialog"
```

---

### Task 7: Update tests and final verification

**Files:**
- Modify: `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx` (add flyer test)

- [ ] **Step 1: Add flyer UI test**

Add this test to the existing `describe('QRCodeDialog')` block in `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx`:

```typescript
it('renders flyer generation controls', () => {
  renderDialog();
  expect(screen.getByText('Generate Flyer')).toBeInTheDocument();
  expect(screen.getByText('Light')).toBeInTheDocument();
  expect(screen.getByText('Dark')).toBeInTheDocument();
  expect(screen.getByText('Flyer Theme')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run all frontend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx jest --no-coverage 2>&1 | tail -20
```

Expected: All tests pass.

- [ ] **Step 3: Run all backend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
python -m pytest backend/tests/ -v --timeout=30 2>&1 | tail -30
```

Expected: All tests pass.

- [ ] **Step 4: Verify frontend build**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx next build 2>&1 | tail -20
```

Expected: Build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/components/referrals/__tests__/QRCodeDialog.test.tsx
git commit -m "test(referral): add flyer UI rendering test"
```
