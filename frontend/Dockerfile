FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app.py .

EXPOSE 8501

# Streamlit needs these flags behind a container/proxy.
CMD ["sh", "-c", "streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
