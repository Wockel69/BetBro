cd /var/www/Betbot

cat > run_live.sh << 'EOF'
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
# venv aktivieren
source .venv/bin/activate
# Live-Monitor starten
python3 live_monitor.py
EOF

chmod +x run_live.sh
