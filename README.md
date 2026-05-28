# 🤖 Torrent Search Bot

A powerful, production-ready async **Python Telegram bot** that searches **5 torrent websites simultaneously**, extracts magnet links, and delivers beautifully formatted results with pagination, filtering, admin controls, and full Docker support.

---

## ✨ Features

| Feature | Details |
|---|---|
| 🔍 Multi-site search | TamilMV · Pirate Bay · YTS · TorrentGalaxy · Nyaa |
| ⚡ Async architecture | `asyncio` + `aiohttp` for maximum concurrency |
| 📊 Real-time progress | Animated progress bar via Telegram message edits |
| 🎯 Smart filtering | Movie · TV · Anime · Game · 4K · x265 · size filters |
| 📑 Pagination | Next / Prev buttons with result counter |
| 🧲 Magnet actions | Open · Copy · Export all as TXT file |
| ⭐ Favorites | Save and export your favorite torrents |
| ⏱️ Caching | TTL cache (in-memory + disk) for instant re-searches |
| 🚫 Rate limiting | Per-user sliding-window rate limiter |
| 🛡️ Admin tools | Broadcast · Blacklist · Analytics · Cache clear |
| 🏥 Health scoring | Seeder-based health: Dead / Fair / Good / Excellent |
| 🐳 Docker ready | Multi-stage Dockerfile + Docker Compose |

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/yourname/torrent-bot.git
cd torrent-bot
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `ADMIN_IDS` — your Telegram user ID(s), comma-separated

### 3. Run

```bash
python -m bot.main
```

---

## 🐳 Docker Deployment

```bash
# Build and start
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

---

## ☁️ VPS Deployment (Ubuntu)

```bash
# Install dependencies
sudo apt update && sudo apt install -y python3.12 python3-pip git

# Clone and configure
git clone https://github.com/yourname/torrent-bot.git
cd torrent-bot
pip3 install -r requirements.txt
cp .env.example .env
nano .env   # fill in BOT_TOKEN and ADMIN_IDS

# Run with auto-restart via systemd
sudo nano /etc/systemd/system/torrent-bot.service
```

Paste this into the service file:

```ini
[Unit]
Description=Torrent Search Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/torrent-bot
ExecStart=/usr/bin/python3 -m bot.main
Restart=always
RestartSec=5
EnvironmentFile=/path/to/torrent-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable torrent-bot
sudo systemctl start torrent-bot
sudo systemctl status torrent-bot
```

---

## 🚂 Railway / Render Deployment

1. Push your code to GitHub
2. Create a new service from your repo
3. Set environment variables from `.env.example` in the dashboard
4. Set start command: `python -m bot.main`

---

## 📖 Bot Commands

| Command | Description |
|---|---|
| `/search <query>` | Search torrents |
| `/search 2026 movie 4k` | Search with filters |
| `/search gta v game` | Category filter |
| `/search ubuntu iso software` | Software filter |
| `/top` | Trending torrents |
| `/latest` | Latest uploads |
| `/stats` | Bot statistics |
| `/myfavs` | Your saved torrents |
| `/export` | Export favorite magnets as TXT |
| `/history` | Your recent searches |
| `/cancel` | Cancel current search |
| `/help` | Show all commands |

### Admin Only

| Command | Description |
|---|---|
| `/broadcast <msg>` | Send message to all users |
| `/blacklist <id> [reason]` | Ban a user |
| `/unblacklist <id>` | Unban a user |
| `/blist` | List blacklisted users |
| `/analytics` | View usage dashboard |
| `/clearcache` | Clear search cache |

---

## 🔍 Search Filters

Append filters to your `/search` command:

```
/search Inception movie 1080p x265
/search One Piece anime
/search ubuntu software iso
/search 2026 4k min:2 max:20
```

| Filter | Values |
|---|---|
| Category | `movie` `tv` `anime` `game` `software` `ebook` `music` |
| Resolution | `4k` `1080p` `720p` `480p` |
| Codec | `x265` `x264` `hevc` `h264` `av1` |
| Size | `min:X` `max:X` (in GB) |

---

## 📨 Example Output

```
🎬 Avengers Endgame 2026 [4K] [x265]

📦 Size: 8.2 GB  |  🌐 Source: YTS
🌱 Seeders: 1,243   📥 Leechers: 87
⭐ Health: ⭐⭐⭐ Excellent
📅 2026-01-15

━━━━━━━━━━━━━━━━━━
🔗 Result 1 of 23

🧲 magnet:?xt=urn:btih:abc123...

[⬅️ Prev] [1/23] [Next ➡️]
[🧲 Open Magnet] [📋 Copy Magnet]
[📤 Export All Magnets] [⭐ Save]
```

---

## 🏗️ Project Structure

```
torrent-bot/
├── bot/
│   ├── main.py              # Entry point
│   ├── config.py            # Settings
│   ├── models.py            # Data models
│   ├── handlers/
│   │   ├── search.py        # /search command
│   │   ├── pagination.py    # Inline buttons
│   │   ├── info.py          # /help /stats /top /latest
│   │   ├── favorites.py     # /myfavs /export
│   │   └── admin.py         # Admin commands
│   ├── scrapers/
│   │   ├── base.py          # Abstract base
│   │   ├── tamilmv.py       # TamilMV
│   │   ├── nyaa.py          # Nyaa.si
│   │   ├── yts.py           # YTS API
│   │   ├── torrentgalaxy.py # TorrentGalaxy
│   │   ├── piratebay.py     # Pirate Bay
│   │   └── manager.py       # Orchestrator
│   ├── utils/
│   │   ├── cache.py         # TTL cache
│   │   ├── dedup.py         # Deduplication
│   │   ├── health.py        # Health scoring
│   │   ├── rate_limiter.py  # Rate limiting
│   │   ├── formatter.py     # Message formatting
│   │   ├── progress.py      # Progress bar
│   │   └── logger.py        # Logging
│   └── db/
│       └── models.py        # SQLite database
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 🔒 Security & Stability

- **Rate limiting**: 5 requests per 60 seconds per user (configurable)
- **Cloudflare detection**: Gracefully skips CF-protected pages
- **Rotating user agents**: Randomized on every request
- **Retry with backoff**: Exponential backoff on failures
- **Timeout handling**: Per-request timeouts (default 15s)
- **Non-root Docker**: Runs as `botuser` in container
- **Ban system**: Persistent blacklist via SQLite

---

## 📄 License

MIT License — use freely, credit appreciated.
