# CareerPilot 前端 — Node 构建 + Nginx 静态托管
# build context = 仓库根(docker-compose 已配置)
# 题库数据 web/src/data/bank.json 已随仓库入库, 无需根目录 data.js
FROM node:20-alpine AS build

WORKDIR /build

# 先装依赖(利用层缓存)
COPY web/package.json web/package-lock.json ./
RUN npm ci

# 源码(含 src/data/bank.json)
COPY web/ ./

RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
