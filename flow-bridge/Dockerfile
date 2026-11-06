FROM python:3.12-slim
WORKDIR /app
COPY flow-bridge/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY flow-bridge/server.py ./server.py
ENV FLOW_BRIDGE_DB=/var/data/flow_bridge.sqlite3
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
