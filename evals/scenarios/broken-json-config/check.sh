# Solved: the config parses, with the user's settings kept
python3 - <<'PY'
import json, sys
config = json.load(open("config.json"))
sys.exit(0 if config == {"port": 8080, "debug": False, "name": "shop"} else 1)
PY
