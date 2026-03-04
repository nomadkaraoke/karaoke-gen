# Product Vision

## What is Nomad Karaoke?

Nomad Karaoke is a karaoke video generation platform that creates professional-quality karaoke videos from any song in under 10 minutes. Founded by Andrew Beveridge and based in South Carolina, it combines state-of-the-art AI for audio separation and lyrics synchronization with human review to produce accurate, polished results.

**Mission**: Make any song singable by automating the complex, time-consuming process of creating karaoke videos.

## The Problem

Creating high-quality karaoke is surprisingly difficult:

1. **Vocal Removal**: Separating vocals from instrumentals without artifacts requires sophisticated AI models and significant compute power.

2. **Lyrics Accuracy**: Getting the right lyrics, with correct spelling, formatting, and line breaks, is error-prone when scraping from various sources.

3. **Timestamp Synchronization**: Aligning each word or syllable to the exact moment in the audio is the hardest part. No existing tool does this reliably for all songs.

4. **Multiple Formats**: Professional karaoke needs multiple output formats (MP4, MKV, CDG) at various resolutions, with proper encoding.

5. **Distribution**: Uploading to YouTube with proper metadata, sharing to cloud storage, and notifying users adds complexity.

Existing solutions either produce poor quality (DIY tools), cost too much time ($50-100/track from manual services), or don't exist at all for many songs.

## Target Audiences

### 1. Individual Karaoke Enthusiasts
People who want custom karaoke for songs not available in commercial libraries - for home parties, personal practice, or sharing with friends.

### 2. Professional KJs and Venues
Karaoke DJs ("KJs") and bars who need to fulfill on-demand song requests. When a patron asks for a song that isn't in the library, generating it in under 10 minutes means it can be ready within 1-2 performances.

### 3. Commercial Karaoke Producers (B2B)
Companies that produce karaoke tracks at scale for distribution. They have their own instrumentals, branding, and distribution channels - they need automated generation infrastructure that can handle 5,000+ tracks with custom theming.

## Technical Approach

The system uses a multi-stage pipeline:

```
Input (audio + metadata)
        │
        ├──────────────────────────────────────┐
        │                                      │
        v                                      v
┌───────────────────┐              ┌───────────────────┐
│ Audio Separation  │              │ Lyrics Processing │
│ (Modal GPU)       │              │ (AudioShake API)  │
│                   │              │                   │
│ • Clean vocal     │              │ • Fetch reference │
│   removal         │              │   lyrics          │
│ • Backing vocal   │              │ • Transcribe with │
│   options         │              │   timestamps      │
└─────────┬─────────┘              │ • AI correction   │
          │                        │ • HUMAN REVIEW    │
          │                        └─────────┬─────────┘
          │                                  │
          └──────────────┬───────────────────┘
                         v
              ┌───────────────────┐
              │ Video Generation  │
              │ • Title screen    │
              │ • Synced lyrics   │
              │ • End screen      │
              └─────────┬─────────┘
                        │
                        v
              ┌───────────────────┐
              │ Output & Distro   │
              │ • Multiple formats│
              │ • YouTube upload  │
              │ • Cloud storage   │
              └───────────────────┘
```

### Why Human Review?

The hardest unsolved problem in karaoke generation is **reliable lyrics transcription with accurate timestamps**. Current approaches:

- **Whisper (open source)**: Free, but inconsistent for sung audio. Works well ~60% of the time, poorly the rest.
- **AudioShake (paid API)**: Proprietary lyrics transcription model specifically trained for music. Much more accurate/consistent than Whisper.
- **Agentic AI Correction**: LLM-based system that compares transcriptions against reference lyrics, fixing errors and improving sync. Costs tokens but catches many issues.

Even with the best AI, some songs have tricky timing, unusual pronunciations, or ambiguous lyrics. Human review catches these edge cases and ensures quality. The review UI lets users:
- Correct misspelled words
- Adjust word/line timing
- Fix line breaks and formatting
- Preview changes in real-time

## Open Source Philosophy

All code is open source (MIT license) because:

1. **Community**: Others can learn from, contribute to, and build upon the work
2. **Transparency**: Users can see exactly how their data is processed
3. **Self-hosting option**: Technical users can run their own instance

However, reliable fully-automated generation requires:
- **AudioShake API**: ~$0.50-1.00 per track for high-quality transcription
- **LLM tokens**: ~$0.50-1.00 for agentic correction workflow
- **GPU compute**: ~$0.20-0.50 for audio separation on Modal
- **Infrastructure**: Cloud Run, Firestore, GCS, etc.

**Cost per track: ~$2-3** for fully-automated processing, making a paid service necessary to sustain development.

The open source CLI (`karaoke-gen`) works with local processing and free APIs for users willing to accept lower reliability and do more manual correction. The hosted service (`gen.nomadkaraoke.com`) provides the premium, reliable experience.

## Business Model

### Credit-Based Pricing

Users purchase credits to generate karaoke videos:

| Credits | Price | Per Track | Savings |
|---------|-------|-----------|---------|
| 1       | $5    | $5.00     | -       |
| 3       | $12   | $4.00     | 20%     |
| 5       | $18   | $3.50     | 30%     |
| 10      | $30   | $3.00     | 40%     |

This is significantly cheaper than:
- Manual services: $10-50 per track
- Hiring a studio: $100+ per track
- DIY tools + your time: Hours of frustration

### B2B / White-Label

Commercial producers pay for API access or custom integration to generate tracks at scale with their own branding, instrumentals, and distribution.

## Current State (December 2025)

### What Works
- Audio upload and separation (Modal API)
- Lyrics transcription (AudioShake API)
- Agentic AI correction (Gemini 3 Flash via Vertex AI)
- Human lyrics review (React UI)
- Preview video generation
- Instrumental selection (clean vs. with backing vocals)
- Multi-format encoding (4K, 720p, lossless, lossy)
- Token-based authentication
- Magic link authentication
- Payment flow (Stripe)
- YouTube upload integration
- Dropbox/Google Drive distribution

### Known Limitations
- CDG format needs additional style configuration
- Very long audio (>10 min) may timeout
- Some edge cases in lyrics timing still need manual correction

## Product Ecosystem

### Active Products

1. **gen.nomadkaraoke.com** - Web application for generating karaoke videos (includes landing page, pricing, and payment)
2. **karaoke-gen CLI** - Local command-line tool for power users
3. **karaoke-gen-remote CLI** - CLI that uses the cloud backend
4. **Nomad Karaoke YouTube Channel** - Hosts generated videos for users

### Future Products

1. **KaraokeHunt** (revival planned) - Song discovery app that analyzes streaming history to suggest songs you'd enjoy singing. Previously launched as a separate product; will be rebranded and integrated with the karaoke generator.

2. **New nomadkaraoke.com Homepage** - Replace the legacy static site with a modern landing page showcasing the generator (currently deployed from `nomadkaraoke/public-website` repo).

## Differentiators

### vs. DIY Tools (Whisper + Demucs + manual editing)
- **Quality**: AudioShake's proprietary model beats open source transcription
- **Speed**: 10 minutes vs. hours of manual work
- **Convenience**: One-click generation with human review UI

### vs. Manual Services ($10-50/track)
- **Price**: $3-5 per track
- **Speed**: Minutes instead of days
- **Scale**: Generate as many as you need, when you need them

### vs. Other Automated Tools
- **Accuracy**: Human review catches AI errors
- **Output quality**: 4K video, multiple formats, proper encoding
- **Distribution**: YouTube upload, cloud storage, notifications built in
- **Open source**: Transparency and community contributions

## Technical Stack

- **Backend**: FastAPI on Google Cloud Run
- **Frontend**: Next.js on Cloudflare Pages
- **Database**: Firestore
- **Storage**: Google Cloud Storage
- **Secrets**: Google Secret Manager
- **Audio Separation**: Modal (GPU compute)
- **Lyrics Transcription**: AudioShake API
- **AI Correction**: Vertex AI (Gemini 3 Flash)
- **Payments**: Stripe
- **Email**: SendGrid
- **IaC**: Pulumi
- **CI/CD**: GitHub Actions (self-hosted runner on GCP)

## Roadmap

### Near-term
- Public launch of gen.nomadkaraoke.com
- New marketing homepage at nomadkaraoke.com
- B2B API for commercial producer integration
- Bulk generation features for high-volume clients

### Medium-term
- KaraokeHunt revival and integration
- Mobile-optimized experience
- Real-time generation status notifications
- Additional output format support

### Long-term
- Karaoke Eternal integration (community request)
- On-premises deployment option for venues
- Live karaoke event features

## Links

- **Web App**: https://gen.nomadkaraoke.com
- **API**: https://api.nomadkaraoke.com
- **GitHub**: https://github.com/nomadkaraoke/karaoke-gen
- **YouTube Channel**: https://www.youtube.com/@nomadkaraoke
- **Updates Signup**: https://nomadkaraoke.kit.com/
- **Contact**: https://cal.com/beveradb

## About the Founder

Andrew Beveridge is a software engineer based in South Carolina who runs a weekly karaoke night and built Nomad Karaoke to solve his own problem: wanting to sing songs that don't exist as karaoke. What started as a personal tool evolved into a platform that can help anyone create professional karaoke for any song.
