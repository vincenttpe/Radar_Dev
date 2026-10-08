FROM nvcr.io/nvidia/l4t-jetpack:r36.4.0

WORKDIR /app

RUN apt-get update && \
    apt-get install -y \
      python3-pip \
      python3-dev \
      build-essential \
      libopenblas-dev \
      libopenmpi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install \
      torch==2.8.0 \
      torchvision==0.23.0 \
      --index-url=https://pypi.jetson-ai-lab.io/jp6/cu126 && \
    python3 -m pip install --no-cache-dir -r requirements.txt

COPY . .

ENV NEO4J_URI=bolt://host.docker.internal:7687
ENV NEO4J_USER=neo4j
# NEO4J_PASSWORD 請在執行時以 -e 或 --env-file 傳入，不寫在映像裡
ENV USE_LLM=0
ENV ENABLE_LIVE_SEARCH=1
ENV STRICT_PROFESSIONAL_SOURCES=1
ENV CHINESE_ONLY_EVIDENCE=1

CMD ["python3", "Project1.py"]
