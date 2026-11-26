# CareerPilot 后端 API — local-first 单机部署
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

# rapidocr(opencv) 和离线普通话播客运行所需的系统库
# 国内网络: deb.debian.org 不稳定, 切换到阿里云镜像源(失败自动回落官方源)
RUN (sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true) \
    && (apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl espeak-ng espeak-ng-data \
    && rm -rf /var/lib/apt/lists/*) \
    || (sed -i 's|mirrors.aliyun.com|deb.debian.org|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl espeak-ng espeak-ng-data \
    && rm -rf /var/lib/apt/lists/*)

WORKDIR /app

COPY server/requirements.txt .
# pip 同理走清华镜像(失败回落官方源)
RUN (pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt \
    || pip install -r requirements.txt)

COPY server/app ./app
COPY server/migrations ./migrations
COPY server/migrate.py server/bootstrap.py ./
COPY deploy/tenant/model_relay.py deploy/tenant/repo_relay.py ./deploy/tenant/

# Native guide helpers resolve this path when looking for legacy bank assets.
RUN printf '%s\n' 'from pathlib import Path' 'BANK_PATH = Path("/web/src/data/bank.json")' > app/seed.py \
    && install -d -m 0700 -o 65532 -g 65532 /personal /personal/assessment-outbox
