FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m spacy download en_core_web_sm

COPY . /app/

EXPOSE 8000
