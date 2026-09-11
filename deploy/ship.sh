#!/usr/bin/env bash
# Локальная сторона деплоя: собрать артефакты из ЗАКОММИЧЕННОГО состояния,
# отправить на сервер и запустить шаги deploy.sh.
#
# Использование (Git Bash на Windows, из корня репозитория):
#   DEPLOY_HOST=root@<ip> deploy/ship.sh release web smoke
#
# Адреса хоста в git нет — только в переменной окружения.
# ssh/scp — Windows OpenSSH: MSYS-версии на этой машине не читают ~/.ssh по
# кириллическому пути и не видят known_hosts.
set -euo pipefail

HOST="${DEPLOY_HOST:?DEPLOY_HOST=root@<ip> не задан}"
SSH="${DEPLOY_SSH:-/c/Windows/System32/OpenSSH/ssh.exe}"
SCP="${DEPLOY_SCP:-/c/Windows/System32/OpenSSH/scp.exe}"
STAGE=/tmp/market-bot-deploy

# Git Bash (MSYS) переписывает POSIX-пути в аргументах внешних программ: "/tmp/…"
# уехал бы на сервер как "C:/Program Files/Git/tmp/…". Преобразование отключаем,
# а локальные пути для Windows-scp передаём явно, в коротком виде 8.3: полный путь
# профиля с кириллицей Windows-scp из Git Bash не читает.
export MSYS_NO_PATHCONV=1
WORK=/tmp/market-bot-ship
mkdir -p "$WORK"
if command -v cygpath >/dev/null 2>&1; then
  WORK_LOCAL="$(cygpath -sw "$WORK")\\"
else
  WORK_LOCAL="$WORK/"
fi

cd "$(git rev-parse --show-toplevel)"

# Только закоммиченное: git archive берёт HEAD, а незакоммиченная правка в дереве
# означала бы, что на прод уезжает не то, что проверяли.
if ! git diff --quiet HEAD -- . ':!miniapp/package-lock.json'; then
  echo "в рабочем дереве есть незакоммиченные изменения — сначала коммит" >&2
  git status --short >&2
  exit 1
fi

SHA=$(git rev-parse --short=12 HEAD)
"$SSH" "$HOST" "mkdir -p $STAGE && chmod 700 $STAGE"

for step in "$@"; do
  case "$step" in
    release)
      # git.exe в Git Bash — нативная Windows-программа: при MSYS_NO_PATHCONV=1
      # путь "/tmp/…" доходит до неё как есть и читается от корня диска (C:\tmp),
      # а не как /tmp Git Bash, — git archive падал «could not open … for writing».
      # Поэтому ей, как и scp, — нативный путь.
      # core.autocrlf=false: иначе git на Windows отдаёт в архиве CRLF, и на
      # сервер уезжает deploy.sh с "#!/bin/sh\r" — «required file not found».
      # .gitattributes (eol=lf) закрывает то же самое; здесь — вторая страховка.
      git -c core.autocrlf=false archive --format=tar.gz -o "${WORK_LOCAL}release.tar.gz" HEAD
      "$SCP" "${WORK_LOCAL}release.tar.gz" "$HOST:$STAGE/release.tar.gz"
      "$SSH" "$HOST" "/opt/market-bot/deploy.sh release $SHA"
      ;;
    web)
      # dist чистится явно: emptyOutDir у vite на Windows молча не удаляет старые
      # файлы, и в архив уезжали ассеты прошлых сборок (11.09 — восемь штук, со старым SDK).
      (cd miniapp && rm -rf dist && npm ci --no-audit --no-fund && npm run build)
      tar -czf "$WORK/web.tar.gz" -C miniapp/dist .
      "$SCP" "${WORK_LOCAL}web.tar.gz" "$HOST:$STAGE/web.tar.gz"
      "$SSH" "$HOST" "/opt/market-bot/deploy.sh web"
      ;;
    nginx|smoke|status|backup)
      "$SSH" "$HOST" "/opt/market-bot/deploy.sh $step"
      ;;
    *)
      echo "неизвестный шаг: $step (release | web | nginx | smoke | status | backup)" >&2
      exit 2
      ;;
  esac
done
