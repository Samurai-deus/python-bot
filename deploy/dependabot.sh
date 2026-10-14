#!/bin/sh
# Слияние PR Dependabot по одному (с чистого main):
#
#     sh deploy/dependabot.sh 235 236 237
#
# Для каждого PR: его правка (от точки ответвления) поверх свежего main → замки (deploy/relock.sh), если затронуты
# requirements*.txt → быстрые проверки → push в ветку PR → CI по этому коммиту → слияние с удалением ветки.
# Любой сбой — стоп, следующие PR не трогаются. Так 30.09 и 14.10.2026 вливались PR, которые Dependabot открыл на
# старой базе и без пересборки замка (pip-PR всегда красные на tests/test_lock.py).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
. deploy/pr_chain.sh
LOG="${PR_CHAIN_LOG:-/tmp}"

one() {
  pr_num="$1"
  br=$(gh pr view "$pr_num" --json headRefName,author --jq 'select(.author.login == "app/dependabot") | .headRefName')
  [ -n "$br" ] || { echo "#$pr_num — не PR Dependabot или не найден"; return 1; }
  echo "=== #$pr_num $br"
  git fetch -q origin || return 1
  git checkout -q -B dependabot-work origin/main || return 1
  patch="$LOG/dependabot-$pr_num.patch"
  git diff "$(git merge-base origin/main "origin/$br")" "origin/$br" > "$patch" || return 1
  git apply --3way "$patch" || { echo "правка #$pr_num не легла на main — разобрать руками"; return 1; }
  if git diff --name-only HEAD | grep -q '^requirements'; then
    sh deploy/relock.sh || return 1
  fi
  git diff --stat HEAD | cat
  py -m pytest -q -p no:cacheprovider tests/test_lock.py tests/test_deploy_artifacts.py || { echo "проверки упали"; return 1; }
  msg="$LOG/dependabot-$pr_num.msg"
  printf '%s (Dependabot #%s)\n\nПоверх свежего main; замки — deploy/relock.sh, если затронуты requirements.\n' \
    "$(gh pr view "$pr_num" --json title --jq .title)" "$pr_num" > "$msg"
  git add -A || return 1
  git commit -q -F "$msg" || return 1
  old=$(git rev-parse "origin/$br")
  retry git push -q --force-with-lease="$br:$old" origin "HEAD:$br" || return 1
  wait_ci "$br" "$(git rev-parse HEAD)" || return 1
  retry gh pr merge "$pr_num" --rebase --delete-branch || return 1
  git checkout -q main && retry git pull -q --ff-only && git branch -D dependabot-work >/dev/null
  git log --oneline -1 | cat
}

[ "$(git branch --show-current)" = main ] && [ -z "$(git status --porcelain)" ] || { echo "нужен чистый main"; exit 1; }
[ $# -gt 0 ] || { echo "использование: sh deploy/dependabot.sh <номер PR>…"; exit 2; }
for n in "$@"; do
  one "$n" || { echo "стоп на #$n"; git checkout -q main; exit 1; }
done
echo "влиты: $*"
