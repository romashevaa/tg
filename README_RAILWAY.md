# Signalbot Railway (shadow-only test)

Deploy the **contents** of this ZIP as the root of a GitHub repository. No nested signalbot/signalbot directories.

## Railway configuration

- New Project > Deploy from GitHub Repo, choose this repo. Dockerfile detected automatically.
- Create a **persistent Volume**, mount path **`/data`** (512 MB Free limit; verify your actual database fits first).
- Environment variables (Railway Variables): `TG_API_ID`, `TG_API_HASH`, `BOT_TOKEN`, `OWNER_ID`, `GEMINI_API_KEY`, `TRADING_MODE=shadow`, `TV_POLL_SECONDS=600`.
- **Important**: This project's Telegram reader uses Telethon session (`signalbot.session`); existing authorized session and `signalbot.db` must both exist on `/data` **before starting**. Railway web interface does not automatically upload your Mac files into a volume. Use a one-off SSH deployment session or another authenticated method to copy files securely. Do not commit the `.session`, `.db`, `.env` or API keys to GitHub.
- **Do not run both local and cloud listener concurrently** with the same Telegram bot token/session, as conflicts can occur.
- This release does **not** place real orders. `TRADING_MODE` other than `shadow` is rejected on startup.

## Monitoring

The main Telegram bot runs in the foreground. A background thread fetches timeline entries for **already registered** ideas in the SQLite `tv_ideas` table using public HTML at 10 minute intervals, storing deduplicated events in `/data/tv_updates_monitor.sqlite`. Logs report errors. At first pass, existing timeline events establish a baseline: they are not Telegram notifications. Subsequent events are recorded but **not yet interpreted as trading actions or sent as Telegram notifications**.

Polling 50 pages/10 minutes may exceed Railway Free credits and could be rate limited. Reduce frequency or tracked ideas based on actual resource usage and response codes. HTTP 429/403 must be treated as a failure, not as a valid no-updates result. Updates without explicit events on the page cannot be guaranteed.

## Local checks (no secrets)

`python -m compileall -q signalbot railway_start.py tv_update_monitor.py`

`python -c "import railway_start; print(railway_start.DATA)"`

## State migration

Need: `signalbot.db`, `signalbot.session` and any external files configured for the bot. Check the local DB size; Free persistent volume is only 0.5 GB. Migration not automated in this ZIP.
