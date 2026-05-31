# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3.

This is a fork of [averygan/reclip](https://github.com/averygan/reclip) with additional features: persistent download jobs, progress tracking, file management, and a concurrent download system.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

https://github.com/user-attachments/assets/419d3e50-c933-444b-8cab-a9724986ba05

![ReClip MP3 Mode](assets/preview-mp3.png)

## Features

- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4 video or MP3 audio extraction
- Quality/resolution picker
- **Bulk downloads** — paste multiple URLs at once
- **Persistent jobs** — download state survives page reloads and server restarts
- **Progress tracking** — live progress bar with percentage and estimated size
- **Cancel downloads** — stop an active download at any time
- **Downloaded files list** — browse completed downloads directly in the UI
- **Concurrent downloads** — multiple downloads can run in parallel
- Clean, responsive UI — no frameworks, no build step

## Quick Start

### With Docker Compose (recommended)

```bash
git clone https://github.com/papulo79/reclip.git
cd reclip
docker compose up -d
```

Open **http://localhost:8899**.

For development (uses local code with live reload):

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

### Without Docker

```bash
git clone https://github.com/papulo79/reclip.git
cd reclip
./reclip.sh
```

This creates a virtual environment, installs Flask and yt-dlp, and starts the app on port **8899**.

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

Downloaded files are saved to the `downloads/` directory.

## API Endpoints

The backend exposes a small REST API used by the frontend:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/info` | POST | Fetch metadata for a URL |
| `/api/download` | POST | Start a download job |
| `/api/status/<job_id>` | GET | Poll job progress |
| `/api/jobs` | GET | List persisted jobs |
| `/api/files` | GET | List downloaded files |
| `/api/cancel/<job_id>` | POST | Cancel an active download |

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask (~400 lines)
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Job persistence:** JSON file (`jobs.json`)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
