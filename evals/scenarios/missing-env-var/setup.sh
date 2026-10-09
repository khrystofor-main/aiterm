printf 'API_URL=http://localhost:9000\n' > .env.example
cat > fetch.py <<'PY'
import os
import sys

url = os.environ.get("API_URL")
if not url:
    sys.exit("error: API_URL is not set (see .env.example)")
print(f"Would fetch from {url}")
PY
