# CareerPilot 云服务指标汇总 —— 独立后端容器服务
#
# 只读同一个 PostgreSQL, 向所有者暴露指标汇总(不对终端用户体现)。
# 构建/运行:
#   docker build -t careerpilot-ops .
#   docker run -d --name careerpilot-ops -p 8099:8099 \
#     -e DATABASE_URL="postgresql://user:pass@host:5432/careerpilot" \
#     -e OPS_TOKEN="<你的内部令牌>" careerpilot-ops
# 访问: http://<host>:8099/          (HTML 仪表盘, 需 ?token= 或 X-Ops-Token)
#       http://<host>:8099/api/v1/ops/metrics   (JSON)
FROM python:3.12-slim

WORKDIR /srv
COPY app.py study_chat_trace.py /srv/
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

ENV DATABASE_URL="" OPS_TOKEN=""
EXPOSE 8099
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8099", "--no-access-log"]
