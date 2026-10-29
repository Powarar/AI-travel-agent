FROM node:22-alpine

WORKDIR /app

ENV npm_config_fund=false \
    npm_config_audit=false

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./

EXPOSE 5173

# /app монтируется с хоста, а /app/node_modules — отдельным volume. При первом создании
# volume он пустой и скрывает зависимости, положенные в образ на этапе build. Устанавливаем
# их один раз прямо в volume, чтобы dev-server не работал с частично доступным Vite.
CMD ["sh", "-c", "if [ ! -x node_modules/.bin/vite ]; then npm ci; fi; exec npm run dev -- --host 0.0.0.0 --port 5173"]
