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
4. Optionally, while the app answers, it also checks the public address and restarts the tunnel when that is stuck
   (below).

It never prints `.env` values. Alert URLs and tokens reach curl through its config on stdin and the SMTP password
through a private netrc file, so none of them shows in the process list. Its state (up or down, since when) is in
`~/.rag-watchdog-state`: the app's on the first line, the public address's on a second.

It skips its checks while a `.maintenance` file is in the checkout (`RAG_MAINTENANCE_FILE`):

- a deploy sets it (`touch .maintenance` before, `rm .maintenance` after: README.md section 8);
- `scripts/restore.sh` writes its process id into it while it runs, and `hold` when it leaves the app stopped on
  purpose (`--no-start`, or a restore that couldn't put everything back) until you delete the file;
- a file older than 2 hours (`RAG_MAINTENANCE_MAX_MINUTES`) is taken as left behind and ignored, unless it says `hold`
  or names a process that still runs.

`integration/tests/test_16_watchdog.py` runs the script against stand-ins for curl, docker, sleep, launchctl and
systemctl.

`scripts/watchdog/watchdog.sh --test` sends a test alert to every configured channel.

Nothing here is installed for you.

## The public address and the tunnel (optional)

The site is public through a Cloudflare tunnel (`cloudflared`), which can stall while the app runs: on 2026-11-02 its
connections dropped ("no recent network activity") and cloudflared stopped retrying, so the app answered on
`127.0.0.1:8080` while https://asknuvexusdoc.com showed Cloudflare's 530 until the tunnel was restarted by hand. These
settings go in the LaunchAgent or the systemd unit (not in `.env`); unset, nothing changes:

| Setting | What |
|---|---|
| `RAG_PUBLIC_URL` | the public address to check, e.g. `https://asknuvexusdoc.com/api/health` (http or https only) |
| `RAG_TUNNEL_LAUNCHD_LABEL` | macOS: the tunnel's LaunchAgent label, restarted with `launchctl kickstart -k gui/<uid>/<label>` |
| `RAG_TUNNEL_SYSTEMD_UNIT` | Linux: the tunnel's unit, restarted with `systemctl restart` |

- It asks the public address only when the app answered here, so a failure there is the tunnel's or Cloudflare's. A
  status other than 2xx or 3xx (Cloudflare's error pages: 530 with error 1033, 502) or no answer within 10 s is a
  failure.
- One failure restarts nothing: it asks again 30 s later. Still failing (and the app still answering here), it
  restarts the tunnel and waits up to 5 minutes for the public address to answer. It never restarts Docker or the stack
  for this. While the public address stays down, every run restarts the tunnel again.
- The label or unit must be a plain name (letters, digits, `.`, `-`, `_` and `@`); one that isn't is refused (logged
  on every run). Without a usable one, a public failure only alerts.
- Alerts: once when the public address went down, saying whether the tunnel restart worked; every
  `ALERT_REPEAT_HOURS` while it stays down; once when it answers again. Its state is separate from the app's, so one
  never clears the other.
- `.maintenance` keeps it away just like the app's check (a deploy also takes the public address down).
- Point it at `/api/health`, not `/api/health/ready`: readiness also fails for a stale worker, which a tunnel restart
  doesn't fix.

## macOS (this Mac): a LaunchAgent

A LaunchAgent runs in your login session, which Docker Desktop needs. It doesn't run while the Mac sleeps: set System
Settings → Energy (or Battery → Options) to keep the Mac awake ("Prevent automatic sleeping when the display is
off"), and enable Docker Desktop → Settings → General → "Start Docker Desktop when you sign in".

Without the public check:

```bash
cd ~/rag-app                        # your checkout
sed -e "s|__RAG_APP_DIR__|$PWD|g" -e "s|__HOME__|$HOME|g" scripts/watchdog/local.rag-assistant.watchdog.plist \
  > ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
scripts/watchdog/watchdog.sh --test   # check the alert channels
tail -f ~/Library/Logs/rag-assistant-watchdog.log
```

On this Mac, instead, with the public address and the tunnel's LaunchAgent (`com.asknuvexusdoc.tunnel`): the same
steps, with the installed copy's two empty values filled before `launchctl bootstrap`:

```bash
cd ~/rag-app
curl -sS -o /dev/null -w '%{http_code}\n' https://asknuvexusdoc.com/api/health   # 200, or don't set RAG_PUBLIC_URL
launchctl print "gui/$(id -u)/com.asknuvexusdoc.tunnel" > /dev/null && echo "tunnel job found"
sed -e "s|__RAG_APP_DIR__|$PWD|g" -e "s|__HOME__|$HOME|g" scripts/watchdog/local.rag-assistant.watchdog.plist \
  > ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
plutil -replace EnvironmentVariables.RAG_PUBLIC_URL -string https://asknuvexusdoc.com/api/health \
  ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
plutil -replace EnvironmentVariables.RAG_TUNNEL_LAUNCHD_LABEL -string com.asknuvexusdoc.tunnel \
  ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
plutil -lint ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist
scripts/watchdog/watchdog.sh --test   # check the alert channels
```

A 403 from the first `curl` means a Cloudflare security setting challenges curl; the watchdog would then restart the
tunnel on every run, so fix that first. To check it once it runs:

```bash
plutil -p ~/Library/LaunchAgents/local.rag-assistant.watchdog.plist | grep RAG_     # the settings it runs with
launchctl print "gui/$(id -u)/local.rag-assistant.watchdog" | grep -E '^[[:space:]](state|runs|last exit code) ='
cat ~/.rag-watchdog-state           # "up <time> 0", then "public up <time> 0"
tail -n 20 ~/Library/Logs/rag-assistant-watchdog.log   # quiet while both answer
```

A run logs nothing while both answer; it exits 0 when all is well and 1 while the app or the public address is down.
To change a setting, edit the installed copy with `plutil -replace`, then `launchctl bootout` and `launchctl
bootstrap` it again.

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

For the public address, uncomment the two `Environment=` lines of `rag-watchdog.service` (cloudflared installed as a
service is `cloudflared.service`). The watchdog's user can't restart a system unit on its own; allow that one restart
with a polkit rule (polkit 0.106 or later, e.g. Debian 12 or Ubuntu 24.04):

```bash
sudo tee /etc/polkit-1/rules.d/50-rag-watchdog.rules > /dev/null <<'EOF'
polkit.addRule(function (action, subject) {
  if (action.id == "org.freedesktop.systemd1.manage-units" && subject.user == "rag" &&
      action.lookup("unit") == "cloudflared.service" && action.lookup("verb") == "restart") {
    return polkit.Result.YES;
  }
});
EOF
```

To remove it: `sudo systemctl disable --now rag-watchdog.timer`, then delete the two files (and the polkit rule) and
run `sudo systemctl daemon-reload`.

The public check runs on the same machine, so it can't report the machine itself going away. An external uptime check
(for example a free plan of an uptime service calling `https://asknuvexusdoc.com/api/health/ready`) can.
