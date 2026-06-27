FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/

# Install CPU-only PyTorch to avoid a multi-GB CUDA build
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m spacy download en_core_web_sm

# Pre-download the embedding model at build time so first startup is fast.
# Override EMBED_MODEL build arg to cache a different model.
ARG EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "import os; from langchain_huggingface import HuggingFaceEmbeddings; \
    HuggingFaceEmbeddings(model_name=os.environ['EMBED_MODEL'], model_kwargs={'trust_remote_code': True})"

COPY . /app/

EXPOSE 8000
