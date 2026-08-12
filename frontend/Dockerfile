FROM node:22-alpine AS build

WORKDIR /app
# El lock TAMBIÉN, y `npm ci` en vez de `npm install`: sin el lock, `vue: ^3.4.0`
# resuelve a cualquier 3.x y dos builds del mismo commit pueden diferir. Medido:
# el mismo fuente daba `index-B4SeJq98.js` local y `index-BE_0ZAc_.js` en el VPS,
# así que el bundle desplegado no se podía comparar por hash contra uno local —
# solo por marcadores. Con `npm ci` el árbol de dependencias queda fijado y el
# build vuelve a ser verificable byte a byte.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
