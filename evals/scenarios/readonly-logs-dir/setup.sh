mkdir -p logs
cat > worker.py <<'PY'
with open("logs/worker.log", "a") as log:
    log.write("worker started\n")
print("Worker done.")
PY
chmod 555 logs
