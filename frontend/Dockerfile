FROM node:18-alpine

WORKDIR /app

# Copy package files first for better caching
COPY package.json package-lock.json* ./

RUN npm install

COPY . .

EXPOSE 3000

# Bind to 0.0.0.0 to allow access from host
CMD ["npm", "run", "dev", "--", "--host"]
