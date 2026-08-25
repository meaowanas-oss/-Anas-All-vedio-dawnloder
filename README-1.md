# Social Media Downloader Bot
**Dev: Anas**

A Telegram bot that downloads videos from social media platforms (Facebook, Instagram, TikTok, Twitter/X, and 1000+ more) — **YouTube is intentionally excluded**.

## Supported Platforms
- Facebook
- Instagram
- TikTok
- Twitter / X
- Pinterest
- Reddit
- Dailymotion
- Vimeo
- Snapchat
- LinkedIn
- Twitch Clips
- SoundCloud
- 1000+ more sites via yt-dlp

## Deploy on Railway

1. Push this repo to GitHub
2. Create a new Railway project → "Deploy from GitHub repo"
3. Add these environment variables in Railway's **Variables** tab:

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Your Telegram bot token from @BotFather |
| `ADMIN_CODE` | ✅ | Secret code users redeem for unlimited access (default: `1169`) |
| `SUPPORT_URL` | ❌ | Your support Telegram link (default: `https://t.me/DevAnas`) |

4. Deploy — Railway auto-detects `nixpacks.toml` and installs ffmpeg + Python.

## Features
- ✅ Multi-platform support (Facebook, Instagram, TikTok, Twitter/X, etc.)
- ✅ ⛔ YouTube blocked with friendly message
- ✅ Free quota: 2 downloads per 24 hours
- ✅ Unlimited access via `/redeem <code>`
- ✅ Live status inline button during download
- ✅ Auto file cleanup after upload
- ✅ SQLite for user/quota tracking (no external DB needed)
- ✅ Max 720p, 50MB, 30min — safe for Railway free tier

## Commands
| Command | Description |
|---|---|
| `/start` | Welcome message + platform list |
| `/help` | Same as /start |
| `/status` | Check your remaining daily quota |
| `/redeem <code>` | Unlock unlimited downloads |

## Notes
- Private/login-required posts (Instagram private accounts, etc.) may not work
- Some platforms (Instagram, TikTok) may occasionally block yt-dlp — keep yt-dlp updated
