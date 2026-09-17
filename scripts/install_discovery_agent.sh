#!/bin/bash
# Install (or reinstall) the launchd agent that runs one discovery batch a day.
#
#   bash scripts/install_discovery_agent.sh                 # 10:15, then 14:15 if that was missed
#   bash scripts/install_discovery_agent.sh 10:15 14:15 20:15   # or any slots you like
#
# launchd never makes up a slot the machine slept through, so more than one is
# listed. The script itself makes sure only one batch runs per day.
#
# To remove it:
#   launchctl bootout gui/$(id -u)/com.aakarner.planning-citations.discovery
#   rm ~/Library/LaunchAgents/com.aakarner.planning-citations.discovery.plist

set -euo pipefail

SLOTS=("$@")
[ ${#SLOTS[@]} -eq 0 ] && SLOTS=("10:15" "14:15")
for s in "${SLOTS[@]}"; do
  [[ "$s" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "bad time '$s'; use HH:MM" >&2; exit 1; }
done
LABEL="com.aakarner.planning-citations.discovery"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/scripts/daily_discovery.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <array>
$(for s in "${SLOTS[@]}"; do
    printf '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>\n' \
      "$((10#${s%%:*}))" "$((10#${s##*:}))"
  done)
  </array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/planning-citations-discovery.launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/planning-citations-discovery.launchd.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLISTEOF

plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf '\ninstalled %s\n' "$LABEL"
printf 'slots: %s  (one batch of 30 per day; later slots skip once a batch has run)\n' "${SLOTS[*]}"
printf 'log: %s\n' "$HOME/Library/Logs/planning-citations-discovery.log"
launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|program|runs" | head -4 || true
