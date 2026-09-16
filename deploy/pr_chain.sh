#!/bin/sh
# Общие шаги цепочки «ветка → PR → CI → слияние» для скриптов выкладки (source deploy/pr_chain.sh).
# Уроки 14.09.2026: (1) `gh pr view <ветка>` «видит» и ЗАКРЫТЫЙ PR — после сброса ветки до main GitHub
# закрывает PR, а цепочка ждёт CI вечно → создавать новый, если state != OPEN; (2) итог CI брать по
# прогону для КОММИТА, а не через `gh pr checks --watch` (виснет на незакрытом статусе).
# Уроки 15–16.09.2026: (3) pytest.ini уже даёт -q, второй -q прячет строку «N passed» — прогон выглядит пустым;
# (4) код возврата брать у pytest, а не у tail/grep после пайпа; (5) имя ветки со слэшем — не имя файла.
retry() {
  n=0
  until "$@"; do
    n=$((n + 1))
    [ "$n" -lt 6 ] || { echo "не прошло за 6 попыток: $*"; return 1; }
    echo "сбой (попытка $n): $* — повтор через 30 с"
    sleep 30
  done
}

# wait_ci <ветка> <sha>: 0 — success, 1 — любой другой итог (печатает его).
wait_ci() {
  sleep 20
  while true; do
    id=$(gh run list --branch "$1" --commit "$2" --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)
    if [ -n "$id" ]; then
      c=$(gh run view "$id" --json status,conclusion --jq '.status + " " + (.conclusion // "")' 2>/dev/null || true)
      case "$c" in
        "completed success") echo "CI: success (run $id)"; return 0 ;;
        completed*) echo "CI: $c (run $id)"; return 1 ;;
      esac
    fi
    sleep 20
  done
}

# run_full_tests: весь pytest, сводка на экран, код возврата — pytest'а. Лог — $PR_CHAIN_LOG (по умолчанию /tmp).
run_full_tests() {
  out="${PR_CHAIN_LOG:-/tmp}/pytest-$(git branch --show-current | tr / _).txt"
  PYTHONIOENCODING=utf-8 py -m pytest tests/ -p no:cacheprovider > "$out" 2>&1
  rc=$?
  grep -E "passed|failed|error" "$out" | tail -3
  [ "$rc" -eq 0 ] || { echo "полный прогон упал (rc=$rc), лог $out"; return 1; }
}

# ship_branch <ветка> <файл коммита> <файл PR> <заголовок PR> <файлы...>: правки уже в дереве на main →
# полный прогон → ветка → коммит только этих файлов → PR → CI → слияние.
ship_branch() {
  br="$1"; msg="$2"; body="$3"; title="$4"; shift 4
  test "$(git branch --show-current)" = "main" || { echo "не на main"; return 1; }
  run_full_tests || return 1
  git checkout -q -b "$br"
  git add -- "$@"
  git commit -q -F "$msg" -- "$@" || return 1
  git show --stat HEAD | cat | head -20
  finish_branch "$br" "$title" "$body"
}

# start_branch <ветка>: с чистого main; если уже на ветке — продолжаем.
start_branch() {
  if [ "$(git branch --show-current)" != "$1" ]; then
    test "$(git branch --show-current)" = "main" || { echo "не на main: $(git branch --show-current)"; return 1; }
    retry git pull -q --ff-only
    test -z "$(git status --short)" || { echo "дерево грязное"; return 1; }
    git checkout -q -b "$1"
  fi
}

# finish_branch <ветка> <заголовок PR> <файл описания>: push → PR (новый, если нет ОТКРЫТОГО) → CI → merge --rebase → main.
finish_branch() {
  retry git push -q -u origin "$1"
  state=$(gh pr view "$1" --json state --jq .state 2>/dev/null || echo NONE)
  [ "$state" = "OPEN" ] || retry gh pr create --base main --head "$1" --title "$2" --body-file "$3"
  wait_ci "$1" "$(git rev-parse HEAD)" || return 1
  retry gh pr merge "$1" --rebase
  git checkout -q main
  retry git pull -q --ff-only
  git branch -D "$1"
  git log --oneline -1 | cat
}
