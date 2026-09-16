#!/bin/bash
# Install (or reinstall) the launchd agent that runs one discovery batch a day.
#
#   bash scripts/install_discovery_agent.sh          # install, run daily at 10:15
#   bash scripts/install_discovery_agent.sh 21 30    # or at a time you pick
#
# To remove it:
#   launchctl bootout gui/$(id -u)/com.aakarner.planning-citations.discovery
#   rm ~/Library/LaunchAgents/com.aakarner.planning-citations.discovery.plist

set -euo pipefail

HOUR="${1:-10}"
MINUTE="${2:-15}"
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
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
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
printf 'runs daily at %02d:%02d, one batch of 30\n' "$HOUR" "$MINUTE"
printf 'log: %s\n' "$HOME/Library/Logs/planning-citations-discovery.log"
launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|program|runs" | head -4 || true
