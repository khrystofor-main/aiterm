cat > app.py <<'PY'
import socket

server = socket.socket()
server.bind(("127.0.0.1", 8000))
server.listen()
print("Listening on port 8000")
PY
