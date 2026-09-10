# 느링고 역할극 PoC — 콘솔
#
# 빌드 컨텍스트는 저장소 루트다(web/ 이 아니다). 파이프라인 페이지가 server/ 의
# 파이썬 소스를 ?raw 로 잘라 싣기 때문에(src/snippets.ts) web/ 만으로는 빌드가 안 된다.
# 그 파일들은 "콘솔에 노출되는 구간"이라 손으로 베끼지 않고 실제 소스에서 가져온다.
#
# vite preview 로 띄우지 않는다 — 개발 서버를 그대로 내보내는 셈이고, /api 프록시가
# vite.config.ts 의 127.0.0.1:8000 에 묶여 있어서 컨테이너 사이에서는 닿지 않는다.
FROM node:22-alpine AS build
WORKDIR /repo/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# ?raw 로 읽히는 것만 넣는다. 서버 전체를 넣으면 .env 나 DB 가 함께 들어갈 수 있다.
COPY server/pipeline/ /repo/server/pipeline/
COPY server/safety/ /repo/server/safety/

RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /repo/web/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
