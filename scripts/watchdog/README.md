# Host watchdog (optional)

The `monitor` service watches everything inside Docker and sends alerts, but it can't notice Docker itself stopping:
Docker Desktop quitting, the Mac rebooting or a server losing its Docker daemon. `watchdog.sh` runs outside Docker
every 5 minutes:

1. It asks `http://127.0.0.1:8080/api/health` (`RAG_APP_URL`). When nothing answers, it asks again 30 s later
   (`RAG_WATCHDOG_RETRY_SECONDS`): one slow answer restarts nothing.
2. Still nothing: it starts Docker (Docker Desktop on macOS; `systemctl start docker` when it runs as root on Linux),
   then the stack's containers as they are (`docker compose start` in the checkout, then `docker compose restart` if
   that wasn't enough). It never builds or recreates a container, so it can't deploy a checkout that wasn't deployed.
   Whether that worked is decided by the app answering within 5 minutes (`RAG_WATCHDOG_WAIT_SECONDS`).
3. It alerts through the monitor's channels, read from the checkout's `.env` (`ALERT_WEBHOOK_URL`, `ALERT_NTFY_URL`,
   `ALERT_NTFY_TOKEN`, `ALERT_SMTP_*`, `ALERT_NAME`, `ALERT_REPEAT_HOURS`):
   - once when the app went down, saying whether the restart worked;
   - every `ALERT_REPEAT_HOURS` while it stays down;
   - once when it answers again.

It never prints `.env` values. Alert URLs and tokens reach curl through its config on stdin and the SMTP password
through a private netrc file, so none of them shows in the process list. Its state (up or down, since when) is in
`~/.rag-watchdog-state`.

It skips its checks while a `.maintenance` file is in the checkout (`RAG_MAINTENANCE_FILE`):

- a deploy sets it (`touch .maintenance` before, `rm .maintenance` after: README.md section 8);
- `scripts/restore.sh` writes its process id into it while it runs, and `hold` when it leaves the app stopped on
  purpose (`--no-start`, or a restore that couldn't put everything back) until you delete the file;
- a file older than 2 hours (`RAG_MAINTENANCE_MAX_MINUTES`) is taken as left behind and ignored, unless it says `hold`
  or names a process that still runs.

`integration/tests/test_16_watchdog.py` runs the script against stand-ins for curl, docker and sleep.

`scripts/watchdog/watchdog.sh --test` sends a test alert to every configured channel.

Nothing here is installed for you.

## macOS (this Mac): a LaunchAgent

A LaunchAgent runs in your login session, which Docker Desktop needs. It doesn't run while the Mac sleeps: set System
Settings → Energy (or Battery → Options) to keep the Mac awake ("Prevent automatic sleeping when the display is
off"), and enable Docker Desktop → Settings → General → "Start Docker Desktop when you sign in".

```bash
cd ~/rag-app                        # your checkout
sed -e "s|__RAG_APP_DIR__|$PWD|g" -e "s|__HOME__|$HOME|g" scripts/watchdog/local.rag-assistant.watchdog.plist \
  > ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
scripts/watchdog/watchdog.sh --test   # check the alert channels
tail -f ~/Library/Logs/rag-assistant-watchdog.log
```

To remove it:

```bash
launchctl bootout "gui/$(id -u)/local.rag-assistant.watchdog"
rm ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
```

## Linux server: a systemd timer

Run it as a user in the `docker` group who owns the checkout (here `rag` and `/opt/rag-app`; change both in
`rag-watchdog.service`). systemd restarts the Docker daemon on its own when it is enabled
(`systemctl enable docker`), so the watchdog only needs to bring the stack back.

```bash
sudo cp scripts/watchdog/rag-watchdog.service scripts/watchdog/rag-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rag-watchdog.timer
systemctl list-timers rag-watchdog.timer
journalctl -u rag-watchdog.service -n 50
```

To remove it: `sudo systemctl disable --now rag-watchdog.timer`, then delete the two files and run
`sudo systemctl daemon-reload`.

Once the app is behind HTTPS on its own domain, an external uptime check (for example a free plan of an uptime service
calling `https://<your domain>/api/health/ready`) also catches the whole machine going away, which no watchdog on it
can report.
