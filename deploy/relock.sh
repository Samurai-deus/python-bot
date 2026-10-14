#!/bin/sh
# Пересборка замков зависимостей с хэшами (uv) с сохранением шапки-комментария.
#
#     sh deploy/relock.sh            # из корня репозитория, после правки requirements*.txt
#
# Прежний замок — предпочтение для uv: сдвигается только то, что требует правка, а не всё дерево. До 14.10.2026
# шапку «приклеивали обратно» руками, а PR Dependabot по pip всегда падали на tests/test_lock.py — замок им не
# пересобирается (deploy/dependabot.sh зовёт этот скрипт). UV — команда uv (по умолчанию «py -m uv»).
set -eu
UV="${UV:-py -m uv}"

lock() {
  out="$1"; shift
  hdr="$out.header"
  awk '/^#/ { print; next } { exit }' "$out" > "$hdr"
  # shellcheck disable=SC2086
  $UV pip compile "$@" --python-version 3.14 --python-platform x86_64-manylinux_2_28 \
      --generate-hashes --no-header -q -o "$out"
  cat "$hdr" "$out" > "$out.new"
  mv -f "$out.new" "$out"
  rm -f "$hdr"
}

lock requirements.lock requirements.txt
lock requirements-dev.lock requirements.txt requirements-dev.txt
echo "замки пересобраны: requirements.lock, requirements-dev.lock"
