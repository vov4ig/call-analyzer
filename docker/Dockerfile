# CUDA 12.8 runtime + Python 3.12 (Ubuntu 24.04) + pinned whisperX stack.
# Build from the repository root:
#   docker build -f docker/Dockerfile -t call-analyzer:1.0.0 .
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        ffmpeg git curl ca-certificates \
        libsndfile1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install CUDA wheels first; requirements.txt resolves the same version.
RUN pip install --no-cache-dir \
        torch==2.8.0 torchaudio==2.8.0 \
        --index-url https://download.pytorch.org/whl/cu128

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cc_paths.py run_call.py analyze_call.py webapp.py ./
COPY install ./install
COPY shim ./shim
# PyTorch >= 2.6 breaks pyannote 3.3 checkpoints unless torch.load is relaxed.
RUN python -c "import site, shutil; shutil.copy('/app/shim/sitecustomize.py', site.getsitepackages()[0]); print('torch.load shim installed')"

ENV CALL_ANALYZER_HOME=/data \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/data/models/hf \
    TORCH_HOME=/data/models/torch \
    NLTK_DATA=/data/models/nltk \
    OLLAMA_HOST=http://ollama:11434 \
    OLLAMA_MODELS=/ollama-models

RUN mkdir -p /data /ollama-models
VOLUME ["/data", "/ollama-models"]
EXPOSE 8080

CMD ["python", "webapp.py", "--host", "0.0.0.0", "--port", "8080"]
