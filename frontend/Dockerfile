FROM node:22-alpine AS build

WORKDIR /app
# El lock TAMBIÉN, y `npm ci` en vez de `npm install`: sin el lock, `vue: ^3.4.0`
# resuelve a cualquier 3.x y dos builds del mismo commit pueden diferir por el
# árbol de dependencias. `npm ci` lo fija.
#
# ⚠️ Pero esto NO alcanza para comparar el bundle byte a byte contra un build hecho
# en Windows, y conviene saberlo antes de intentarlo: con `core.autocrlf=true` el
# working tree tiene CRLF mientras el blob del commit (y el checkout del VPS) tiene
# LF, así que el INPUT del build ya es distinto. Medido: el mismo commit produce
# `index-BE_0ZAc_.js` desde LF y `index-B4SeJq98.js` desde CRLF.
# Para verificar que el VPS sirve lo mismo que hay en el repo, comparar los FUENTES
# contra el blob (`git show HEAD:<f> | sha256sum`) y buscar marcadores literales en
# el bundle — no el hash del asset. Un `.gitattributes` con `eol=lf` lo resolvería
# de raíz, pero reescribe los finales de línea de todo el working tree.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
