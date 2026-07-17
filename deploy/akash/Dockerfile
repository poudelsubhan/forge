# Forge headless on Akash (Track C). Build from repo root:
#   docker build -f deploy/akash/Dockerfile -t <image> .
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir anthropic beautifulsoup4 httpx pydantic python-dotenv rich textual
COPY forge/ forge/
COPY main.py .
COPY scripts/ scripts/
COPY demo/ demo/
# --fresh: never trust a baked toolbox; every run re-earns its tools.
# JSONL is emitted between markers so `lease-logs` retrieval is a plain grep.
CMD ["/bin/sh", "-c", "python main.py \"${FORGE_TASK:-demo/task.md}\" --fresh --no-tui; echo FORGE_JSONL_BEGIN; cat runs/*.jsonl; echo FORGE_JSONL_END; tail -f /dev/null"]
