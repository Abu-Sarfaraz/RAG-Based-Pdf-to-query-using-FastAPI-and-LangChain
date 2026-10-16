FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py api.py styles.css ./
COPY components ./components
COPY .streamlit ./.streamlit
EXPOSE 8501
CMD ["sh", "-c", "exec streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
