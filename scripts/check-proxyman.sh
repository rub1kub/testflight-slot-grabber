#!/bin/bash
set -u

if [[ -d /Applications/Proxyman.app ]]; then
  VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/Proxyman.app/Contents/Info.plist 2>/dev/null || true)
  echo "proxyman: installed ($VERSION)"
else
  echo "proxyman: not installed"
fi
if /usr/bin/pgrep -x Proxyman >/dev/null 2>&1; then
  echo "proxyman_process: running"
else
  echo "proxyman_process: stopped"
fi
echo "system_proxy_flags:"
/usr/sbin/scutil --proxy | /usr/bin/grep -E 'HTTPEnable|HTTPSEnable|ProxyAutoConfigEnable|SOCKSEnable' || true
echo "testflight_process:"
TESTFLIGHT_PID=$(/usr/bin/pgrep -x TestFlight | /usr/bin/head -1 || true)
if [[ -n "$TESTFLIGHT_PID" ]]; then
  /bin/ps -p "$TESTFLIGHT_PID" -o pid=,command=
else
  echo "stopped"
fi
