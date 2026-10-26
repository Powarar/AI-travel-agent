FROM node:22-alpine

WORKDIR /app

ENV npm_config_fund=false \
    npm_config_audit=false

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
