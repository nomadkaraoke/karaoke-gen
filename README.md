# Karaoke Generator 🎶 🎥 🚀

![PyPI - Version](https://img.shields.io/pypi/v/karaoke-gen)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/karaoke-gen)
![Tests](https://github.com/nomadkaraoke/karaoke-gen/workflows/Test%20and%20Publish/badge.svg)
![Test Coverage](https://codecov.io/gh/nomadkaraoke/karaoke-gen/branch/main/graph/badge.svg)

Generate professional karaoke videos with instrumental audio and synchronized lyrics. Available as a **local CLI** (`karaoke-gen`) or **cloud-based CLI** (`karaoke-gen-remote`) that offloads processing to Google Cloud.

## ✨ Two Ways to Generate Karaoke

### 1. Local CLI (`karaoke-gen`)
Run all processing locally on your machine. Requires GPU for optimal audio separation performance.

```bash
karaoke-gen "ABBA" "Waterloo"
```

### 2. Remote CLI (`karaoke-gen-remote`) 
Offload all processing to a cloud backend. No GPU required - just authenticate and submit jobs.

```bash
karaoke-gen-remote ./song.flac "ABBA" "Waterloo"
```

Both CLIs produce identical outputs: 4K karaoke videos, CDG+MP3 packages, audio stems, and more.

---

## 🎯 Features

### Core Pipeline
- **Audio Separation**: AI-powered vocal/instrumental separation using MDX and Demucs models
- **Lyrics Transcription**: Word-level timestamps via AudioShake API
- **Lyrics Correction**: Match transcription against online lyrics (Genius, Spotify, Musixmatch)
- **Human Review**: Interactive UI for correcting lyrics before final render
- **Video Rendering**: High-quality 4K karaoke videos with customizable styles
- **Multiple Outputs**: MP4 (4K lossless/lossy, 720p), MKV, CDG+MP3, TXT+MP3

### Distribution Features
- **YouTube Upload**: Automatic upload to your YouTube channel
- **Dropbox Integration**: Organize output in brand-coded folders
- **Google Drive**: Upload to public share folders
- **Discord Notifications**: Webhook notifications on completion

---

## 📦 Installation

```bash
pip install karaoke-gen
```

This installs both `karaoke-gen` (local) and `karaoke-gen-remote` (cloud) CLIs.

### Requirements
- Python 3.10-3.13
- FFmpeg
- For local processing: CUDA-capable GPU or Apple Silicon CPU recommended

### Transcription Provider Setup

**Transcription is required** for creating karaoke videos with synchronized lyrics. The system needs word-level timing data to display lyrics in sync with the music.

#### Option 1: AudioShake (Recommended)
Commercial service with high-quality transcription. Best for production use.

```bash
export AUDIOSHAKE_API_TOKEN="your_audioshake_token"
```

Get an API key at [https://www.audioshake.ai/](https://www.audioshake.ai/) - business only, at time of writing this.

#### Option 2: Local Whisper (No Cloud Required)
Run Whisper directly on your local machine using whisper-timestamped. Works on CPU, NVIDIA GPU (CUDA), or Apple Silicon.

```bash
# Install with local Whisper support
pip install "karaoke-gen[local-whisper]"

# Optional: Configure model size (tiny, base, small, medium, large)
export WHISPER_MODEL_SIZE="medium"

# Optional: Force specific device (cpu, cuda, mps)
export WHISPER_DEVICE="cpu"
```

**Model Size Guide:**
| Model | VRAM | Speed | Quality |
|-------|------|-------|---------|
| tiny | ~1GB | Fast | Lower |
| base | ~1GB | Fast | Basic |
| small | ~2GB | Medium | Good |
| medium | ~5GB | Slower | Better |
| large | ~10GB | Slowest | Best |

**CPU-Only Installation** (no GPU required):
```bash
# Pre-install CPU-only PyTorch first
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install "karaoke-gen[local-whisper]"
```

Local Whisper runs automatically as a fallback when no cloud transcription services are configured.

#### Option 3: Whisper via RunPod
Cloud-based alternative using OpenAI's Whisper model on RunPod infrastructure.

```bash
export RUNPOD_API_KEY="your_runpod_key"
export WHISPER_RUNPOD_ID="your_whisper_endpoint_id"
```

Set up a Whisper endpoint at [https://www.runpod.io/](https://www.runpod.io/)

#### Without Transcription (Instrumental Only)
If you don't need synchronized lyrics, use the `--skip-lyrics` flag:

```bash
karaoke-gen --skip-lyrics "Artist" "Title"
```

This creates an instrumental-only karaoke video without lyrics overlay.

> **Note:** See `lyrics_transcriber_temp/README.md` for detailed transcription provider configuration options.

---

## 🖥️ Local CLI (`karaoke-gen`)

### Basic Usage

```bash
# Generate from local audio file
karaoke-gen ./song.mp3 "Artist Name" "Song Title"

# Search and download audio automatically
karaoke-gen "Rick Astley" "Never Gonna Give You Up"

# Process from YouTube URL
karaoke-gen "https://www.youtube.com/watch?v=dQw4w9WgXcQ" "Rick Astley" "Never Gonna Give You Up"
```

### Remote Audio Separation (Optional)

Offload just the GPU-intensive audio separation to Modal.com while keeping other processing local:

```bash
export AUDIO_SEPARATOR_API_URL="https://USERNAME--audio-separator-api.modal.run"
karaoke-gen "Artist" "Title"
```

### Key Options

```bash
# Custom styling
karaoke-gen --style_params_json="./styles.json" "Artist" "Title"

# Generate CDG and TXT packages
karaoke-gen --enable_cdg --enable_txt "Artist" "Title"

# Skip video encoding (CDG/TXT only, faster)
karaoke-gen --no-video --enable_cdg "Artist" "Title"

# YouTube upload
karaoke-gen --enable_youtube_upload --youtube_description_file="./desc.txt" "Artist" "Title"

# Full production run
karaoke-gen \
  --style_params_json="./branding.json" \
  --enable_cdg \
  --enable_txt \
  --brand_prefix="BRAND" \
  --enable_youtube_upload \
  --youtube_description_file="./description.txt" \
  "Artist" "Title"
```

### Full Options Reference

```bash
karaoke-gen --help
```

---

## ☁️ Remote CLI (`karaoke-gen-remote`)

The remote CLI submits jobs to a Google Cloud backend that handles all processing. You don't need a GPU or any audio processing libraries installed locally.

### Setup

1. **Set the backend URL:**
   ```bash
   export KARAOKE_GEN_URL="https://api.nomadkaraoke.com"  # Or your own backend
   ```

2. **Authenticate with Google Cloud:**
   ```bash
   gcloud auth login
   ```

### Basic Usage

```bash
# Submit a job
karaoke-gen-remote ./song.flac "ABBA" "Waterloo"

# The CLI will:
# 1. Upload your audio file
# 2. Monitor processing progress
# 3. Open lyrics review UI when ready
# 4. Prompt for instrumental selection
# 5. Download all outputs when complete
```

### Job Management

```bash
# List all jobs
karaoke-gen-remote --list

# Resume monitoring an existing job
karaoke-gen-remote --resume abc12345

# Cancel a running job
karaoke-gen-remote --cancel abc12345

# Delete a job and its files
karaoke-gen-remote --delete abc12345
```

### Full Production Run

```bash
karaoke-gen-remote \
  --style_params_json="./karaoke-styles.json" \
  --enable_cdg \
  --enable_txt \
  --brand_prefix=NOMAD \
  --enable_youtube_upload \
  --youtube_description_file="./youtube-description.txt" \
  ./song.flac "Artist" "Title"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `KARAOKE_GEN_URL` | Backend service URL | Required |
| `KARAOKE_GEN_AUTH_TOKEN` | Admin auth token (for protected endpoints) | Optional |
| `REVIEW_UI_URL` | Lyrics review UI URL | `https://gen.nomadkaraoke.com/lyrics/` |
| `POLL_INTERVAL` | Seconds between status polls | `5` |

**Note:** The `REVIEW_UI_URL` defaults to the hosted lyrics review UI. For local development, set it to `http://localhost:5173` if you're running the frontend dev server.

### Authentication

The backend uses token-based authentication for admin operations (bulk delete, internal worker triggers). For basic job submission and monitoring, authentication is optional.

**For admin access:**
```bash
export KARAOKE_GEN_AUTH_TOKEN="your-admin-token"
```

The token must match one of the tokens configured in the backend's `ADMIN_TOKENS` environment variable.

### Non-Interactive Mode

For automated/CI usage:

```bash
karaoke-gen-remote -y ./song.flac "Artist" "Title"
```

The `-y` flag auto-accepts default corrections and selects clean instrumental.

---

## 🎨 Style Configuration

Create a `styles.json` file to customize the karaoke video appearance:

```json
{
  "intro": {
    "video_duration": 5,
    "background_image": "/path/to/title-background.png",
    "font": "/path/to/Font.ttf",
    "artist_color": "#ffdf6b",
    "title_color": "#ffffff"
  },
  "karaoke": {
    "background_image": "/path/to/karaoke-background.png",
    "font_path": "/path/to/Font.ttf"
  },
  "end": {
    "background_image": "/path/to/end-background.png"
  },
  "cdg": {
    "font_path": "/path/to/Font.ttf",
    "instrumental_background": "/path/to/cdg-background.png"
  }
}
```

When using `karaoke-gen-remote`, all referenced files are automatically uploaded with your job.

---

## 📤 Output Files

A completed job produces:

```
BRAND-1234 - Artist - Title/
├── Artist - Title (Final Karaoke Lossless 4k).mp4    # ProRes 4K
├── Artist - Title (Final Karaoke Lossless 4k).mkv    # FLAC audio 4K
├── Artist - Title (Final Karaoke Lossy 4k).mp4       # H.264 4K
├── Artist - Title (Final Karaoke Lossy 720p).mp4     # H.264 720p
├── Artist - Title (Final Karaoke CDG).zip            # CDG+MP3 package
├── Artist - Title (Final Karaoke TXT).zip            # TXT+MP3 package
├── Artist - Title (Karaoke).cdg                      # Individual CDG
├── Artist - Title (Karaoke).mp3                      # Karaoke audio
├── Artist - Title (Karaoke).lrc                      # LRC lyrics
├── Artist - Title (Karaoke).ass                      # ASS subtitles
├── Artist - Title (Title).mov                        # Title screen video
├── Artist - Title (End).mov                          # End screen video
├── Artist - Title (Instrumental...).flac             # Clean instrumental
├── Artist - Title (Instrumental +BV...).flac         # With backing vocals
└── stems/                                            # All audio stems
    ├── ...Vocals....flac
    ├── ...Bass....flac
    ├── ...Drums....flac
    └── ...
```

---

## 🏗️ Deploy Your Own Backend

The cloud backend runs on Google Cloud Platform using:
- **Cloud Run**: Serverless API hosting
- **Firestore**: Job state management
- **Cloud Storage**: File uploads and outputs
- **Modal.com**: GPU-accelerated audio separation
- **AudioShake**: Lyrics transcription API

### Prerequisites

- Google Cloud account with billing enabled
- [Pulumi CLI](https://www.pulumi.com/docs/install/)
- Modal.com account (for audio separation)
- AudioShake API key

### Infrastructure Setup

```bash
cd infrastructure

# Install dependencies
pip install -r requirements.txt

# Login to Pulumi
pulumi login

# Create a stack
pulumi stack init prod

# Configure GCP project
pulumi config set gcp:project your-project-id
pulumi config set gcp:region us-central1

# Deploy infrastructure
pulumi up
```

This creates:
- Firestore database
- Cloud Storage bucket
- Artifact Registry
- Service account with IAM roles
- Secret Manager secrets (you add values)

### Add Secret Values

```bash
# AudioShake API key
echo -n "your-audioshake-key" | gcloud secrets versions add audioshake-api-key --data-file=-

# Genius API key
echo -n "your-genius-key" | gcloud secrets versions add genius-api-key --data-file=-

# Modal API URL
echo -n "https://your-modal-url" | gcloud secrets versions add audio-separator-api-url --data-file=-

# YouTube OAuth credentials (JSON)
gcloud secrets versions add youtube-oauth-credentials --data-file=./youtube-creds.json

# Dropbox OAuth credentials (JSON)
gcloud secrets versions add dropbox-oauth-credentials --data-file=./dropbox-creds.json

# Google Drive service account (JSON)
gcloud secrets versions add gdrive-service-account --data-file=./gdrive-sa.json
```

### Deploy Cloud Run

Deployments happen automatically via GitHub Actions CI when pushing to `main`.
See `.github/workflows/ci.yml` for the full deployment workflow.

### Point CLI to Your Backend

```bash
export KARAOKE_GEN_URL="https://your-backend.run.app"
karaoke-gen-remote ./song.flac "Artist" "Title"
```

---

## 🔌 Backend API Reference

The backend exposes a REST API for job management.

### Job Submission

**POST** `/api/jobs/upload`

Submit a new karaoke generation job with audio file and options.

```bash
curl -X POST "https://api.example.com/api/jobs/upload" \
  -F "file=@song.flac" \
  -F "artist=ABBA" \
  -F "title=Waterloo" \
  -F "enable_cdg=true" \
  -F "enable_txt=true" \
  -F "brand_prefix=NOMAD" \
  -F "style_params=@styles.json" \
  -F "style_karaoke_background=@background.png"
```

### Job Status

**GET** `/api/jobs/{job_id}`

Get job status and details.

```bash
curl "https://api.example.com/api/jobs/abc12345"
```

### List Jobs

**GET** `/api/jobs`

List all jobs with optional status filter.

```bash
curl "https://api.example.com/api/jobs?status=complete&limit=10"
```

### Cancel Job

**POST** `/api/jobs/{job_id}/cancel`

Cancel a running job.

```bash
curl -X POST "https://api.example.com/api/jobs/abc12345/cancel" \
  -H "Content-Type: application/json" \
  -d '{"reason": "User cancelled"}'
```

### Delete Job

**DELETE** `/api/jobs/{job_id}`

Delete a job and its files.

```bash
curl -X DELETE "https://api.example.com/api/jobs/abc12345?delete_files=true"
```

### Lyrics Review

**GET** `/api/review/{job_id}/correction-data`

Get correction data for lyrics review.

**POST** `/api/review/{job_id}/complete`

Submit corrected lyrics and trigger video rendering.

### Instrumental Selection

**GET** `/api/jobs/{job_id}/instrumental-options`

Get available instrumental options.

**POST** `/api/jobs/{job_id}/select-instrumental`

Submit instrumental selection (clean or with_backing).

```bash
curl -X POST "https://api.example.com/api/jobs/abc12345/select-instrumental" \
  -H "Content-Type: application/json" \
  -d '{"selection": "clean"}'
```

### Download Files

**GET** `/api/jobs/{job_id}/download-urls`

Get download URLs for all output files.

**GET** `/api/jobs/{job_id}/download/{category}/{file_key}`

Stream download a specific file.

### Health Check

**GET** `/api/health`

Check backend health status.

---

## 🔧 Troubleshooting

### "No suitable files found for processing"

This error occurs during the finalisation step when the `(With Vocals).mkv` file is missing. This file is created during lyrics transcription.

**Most common cause:** No transcription provider configured.

**Quick fix:**
1. Check if transcription providers are configured:
   ```bash
   echo $AUDIOSHAKE_API_TOKEN
   echo $RUNPOD_API_KEY
   ```

2. If both are empty, set up a provider (see [Transcription Provider Setup](#transcription-provider-setup))

3. Or use `--skip-lyrics` for instrumental-only karaoke:
   ```bash
   karaoke-gen --skip-lyrics "Artist" "Title"
   ```

**Other causes:**
- Invalid API credentials - verify your tokens are correct and active
- API service unavailable - check service status pages
- Network connectivity issues - ensure you can reach the API endpoints
- Transcription timeout - try again or use a different provider

### Transcription Fails Silently

If karaoke-gen runs without errors but produces no synchronized lyrics:

1. **Check logs** - Run with `--log_level debug` for detailed output:
   ```bash
   karaoke-gen --log_level debug "Artist" "Title"
   ```

2. **Verify environment variables** - Ensure API tokens are exported in your shell:
   ```bash
   # Check if set
   printenv | grep -E "(AUDIOSHAKE|RUNPOD|WHISPER)"
   
   # Set in current session
   export AUDIOSHAKE_API_TOKEN="your_token"
   ```

3. **Test API connectivity** - Verify you can reach the transcription service

### "No lyrics found from any source"

This warning means no reference lyrics were fetched from online sources (Genius, Spotify, Musixmatch). The transcription will still work, but auto-correction may be less accurate.

**To fix:**
- Set `GENIUS_API_TOKEN` for Genius lyrics
- Set `SPOTIFY_COOKIE_SP_DC` for Spotify lyrics
- Set `RAPIDAPI_KEY` for Musixmatch lyrics
- Or provide lyrics manually with `--lyrics_file /path/to/lyrics.txt`

### Video Quality Issues

If the output video has quality problems:
- Ensure FFmpeg is properly installed: `ffmpeg -version`
- Check available codecs: `ffmpeg -codecs`
- For 4K output, ensure sufficient disk space (10GB+ per track)

### Local Whisper Issues

#### GPU Out of Memory
If you get CUDA out of memory errors:
```bash
# Use a smaller model
export WHISPER_MODEL_SIZE="small"  # or "tiny"

# Or force CPU mode
export WHISPER_DEVICE="cpu"
```

#### Slow Transcription on CPU
CPU transcription is significantly slower than GPU. For faster processing:
- Use a smaller model (`tiny` or `base`)
- Consider using cloud transcription (AudioShake or RunPod)
- On Apple Silicon, the `small` model offers good speed/quality balance

#### Model Download Issues
Whisper models are downloaded on first use (~1-3GB depending on size). If downloads fail:
- Check your internet connection
- Set a custom cache directory: `export WHISPER_CACHE_DIR="/path/with/space"`
- Models are cached in `~/.cache/whisper/` by default

#### whisper-timestamped Not Found
If you get "whisper-timestamped is not installed":
```bash
pip install "karaoke-gen[local-whisper]"
# Or install directly:
pip install whisper-timestamped
```

#### Disabling Local Whisper
If you want to disable local Whisper (e.g., to force cloud transcription):
```bash
export ENABLE_LOCAL_WHISPER="false"
```

---

## 🧪 Development

### Running Tests

```bash
# Run all tests
pytest tests/ backend/tests/ -v

# Run only unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ -v --cov=karaoke_gen --cov-report=term-missing
```

### Project Structure

```
karaoke-gen/
├── karaoke_gen/           # Core CLI package
│   ├── utils/
│   │   ├── gen_cli.py     # Local CLI (karaoke-gen)
│   │   └── remote_cli.py  # Remote CLI (karaoke-gen-remote)
│   ├── karaoke_finalise/  # Video encoding, packaging, distribution
│   └── style_loader.py    # Unified style configuration
├── backend/               # Cloud backend (FastAPI)
│   ├── api/routes/        # API endpoints
│   ├── workers/           # Background processing workers
│   └── services/          # Business logic services
├── infrastructure/        # Pulumi IaC for GCP
├── docs/                  # Documentation
└── tests/                 # Test suite
```

---

## 📄 License

MIT

---

## 🤝 Contributing

Contributions are welcome! Please see our contributing guidelines.
