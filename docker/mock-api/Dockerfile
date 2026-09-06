FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force

COPY server.js middleware.js db.json ./

EXPOSE 8080
USER node
CMD ["node", "server.js"]
