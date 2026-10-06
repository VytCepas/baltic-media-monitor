FROM python:3.12-slim
ENV HF_HOME=/models PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install sentence-transformers==3.3.1 fastapi==0.115.6 uvicorn==0.32.1
# download the model once at build time: no network needed at runtime, cold start only loads from disk
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
COPY app.py /app/app.py
WORKDIR /app
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
