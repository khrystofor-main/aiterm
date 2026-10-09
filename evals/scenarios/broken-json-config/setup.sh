cat > config.json <<'JSON'
{
  "port": 8080,
  "debug": false,
  "name": "shop",
}
JSON
cat > server.py <<'PY'
import json

with open("config.json") as f:
    config = json.load(f)
print(f"Serving {config['name']} on port {config['port']}")
PY
