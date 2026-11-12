# Сборка фронта и раздача через nginx. Запуск в составе стенда: docker compose up --build
# Интерфейс: http://localhost:3000, запросы /api/* проксируются на gateway.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# Адрес шлюза внутри сети Docker; подставляется в шаблон конфига при старте контейнера
ENV API_UPSTREAM=http://gateway:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/health || exit 1
