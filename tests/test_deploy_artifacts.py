"""
Структурные проверки артефактов деплоя: то, что ломается молча и выясняется
только на сервере. Каждая проверка — след конкретной находки аудита или выкладки.
"""
import io
import pathlib
import re
import shutil
import subprocess
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
NGINX_SITE = DEPLOY / "nginx" / "market-bot.conf.template"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def nginx_directives(path):
    """Текст конфига nginx без комментариев: проверяем директивы, а не пояснения к ним."""
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def step_body(script, name):
    """Тело функции шага из deploy.sh."""
    return script.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


# ---------------------------------------------------------------------------
# Образ
# ---------------------------------------------------------------------------

def test_dockerignore_keeps_scripts_needed_in_the_image():
    """
    M-8: "scripts/" с "!scripts/healthcheck.py" старый билдер не распознаёт —
    файл внутри целиком исключённого каталога вернуть нельзя, healthcheck
    пропадал из образа, и контейнер вечно считался unhealthy.
    """
    lines = [ln.strip() for ln in read(".dockerignore").splitlines()]
    assert "scripts/" not in lines, "исключение каталогом целиком ломает исключения-исключения"
    assert "scripts/*" in lines
    for needed in ("healthcheck.py", "backup_sqlite.py", "smoke_auth.py"):
        assert f"!scripts/{needed}" in lines
        assert (ROOT / "scripts" / needed).is_file()


def test_dockerignore_keeps_secrets_and_bytecode_out():
    lines = read(".dockerignore").splitlines()
    for pattern in (".env", ".env.*", "*.pyc", "__pycache__/", "*.db"):
        assert pattern in lines


def test_image_does_not_write_bytecode():
    """Токен однажды утёк через закоммиченный .pyc — в образе байт-код не нужен."""
    assert "PYTHONDONTWRITEBYTECODE=1" in read("Dockerfile")


def test_image_user_has_fixed_uid():
    """H-19: системный UID + /data от root на хосте = PermissionError на первой записи."""
    assert re.search(r"useradd\s+-u\s+10001", read("Dockerfile"))


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------

def test_prod_compose_commands_point_to_existing_files():
    compose = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for entry in ("runner.py", "run_api.py", "scripts/healthcheck.py"):
        assert entry in compose
        assert (ROOT / entry).is_file()


def test_prod_compose_does_not_take_occupied_host_port():
    """Порт 8000 на прод-хосте занят; API публикуется только на localhost:8100."""
    compose = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8100:8000"' in compose
    assert not re.search(r'["\s-]8000:8000', compose)


def test_prod_compose_routes_telegram_via_host_gateway():
    compose = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert compose.count("host.docker.internal:host-gateway") == 2, "и боту, и API нужен выход к прокси"


# ---------------------------------------------------------------------------
# nginx
# ---------------------------------------------------------------------------

def test_nginx_template_replaces_forwarded_for_instead_of_appending():
    """C-7: $proxy_add_x_forwarded_for дописывает, и первый элемент остаётся подделкой клиента."""
    directives = nginx_directives(NGINX_SITE)
    assert "$proxy_add_x_forwarded_for" not in directives
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in directives


def test_nginx_locations_with_own_headers_reinclude_security_headers():
    """
    add_header внутри location отменяет все add_header уровня server. Каждый
    location со своим add_header обязан подключить snippet заголовков заново.
    """
    blocks = re.findall(r"location[^{]*\{([^}]*)\}", nginx_directives(NGINX_SITE))
    for body in blocks:
        if "add_header" in body:
            assert "include snippets/market-bot-headers.conf;" in body, body


def test_nginx_index_is_never_cached():
    index_block = re.search(r"location = /index\.html \{([^}]*)\}", nginx_directives(NGINX_SITE)).group(1)
    assert "no-store" in index_block


def test_nginx_template_has_no_hardcoded_host():
    """
    Имя прод-хоста в публичном репозитории не нужно — его подставляет deploy.sh.
    Loopback (proxy_pass на локальный API) — не адрес хоста, он допустим.
    """
    for name in ("market-bot.conf.template", "market-bot-bootstrap.conf.template"):
        path = DEPLOY / "nginx" / name
        text = path.read_text(encoding="utf-8")
        assert "__DOMAIN__" in text
        assert not re.search(r"\d+-\d+-\d+-\d+\.sslip\.io", text)
        addresses = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", nginx_directives(path))
        assert [a for a in addresses if not a.startswith("127.")] == []


# ---------------------------------------------------------------------------
# Скрипты деплоя
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", ["deploy.sh", "backup.sh", "ship.sh", "watchdog.sh", "notify.sh"])
def test_shell_scripts_parse(script):
    sh = shutil.which("bash") or shutil.which("sh")
    if not sh:
        pytest.skip("нет sh для проверки синтаксиса")
    result = subprocess.run([sh, "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_deploy_never_overwrites_existing_env():
    """.env на сервере содержит секреты, созданные один раз; шаг env его не трогает."""
    env_step = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_env")
    assert 'if [ -f "$APP/.env" ]' in env_step
    assert env_step.index('if [ -f "$APP/.env" ]') < env_step.index('cat > "$APP/.env"')


def test_deploy_checks_nginx_before_reload():
    apply_fn = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "nginx_apply")
    assert apply_fn.index("nginx -t") < apply_fn.index("systemctl reload nginx")


def test_deploy_updates_itself_by_rename_not_overwrite():
    """Перезапись работающего sh-скрипта на ходу рвёт его посередине; только mv."""
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert 'mv -f "$APP/deploy.sh.next" "$APP/deploy.sh"' in script
    assert not re.search(r'cp [^\n]*deploy\.sh"? "\$APP/deploy\.sh"\s*$', script, re.M)


def test_smoke_catches_a_crash_looping_bot():
    """
    healthcheck бота проверяет только базу и не отличает живой бот от падающего
    по кругу: между перезапусками он успевает стать healthy. smoke обязан
    смотреть на счётчик перезапусков.
    """
    smoke = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_smoke")
    assert "RestartCount" in smoke


def test_status_step_redacts_tokens_from_log_tail():
    """
    Шаг status выводит хвост лога бота. 10.09.2026 в этом хвосте был токен (httpx
    писал URL запросов к Telegram), и он ушёл в вывод сессии деплоя. Выражение
    вычистки выполняется настоящим sed — тем же, что стоит на сервере.
    """
    status = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_status")
    assert "docker logs" in status
    match = re.search(r"sed -E '([^']+)'", status)
    assert match, "в status нет sed-вычистки"

    sed = shutil.which("sed")
    if not sed:
        pytest.skip("нет sed, чтобы выполнить выражение")
    fake = "message=HTTP Request: POST https://api.telegram.org/bot123456789:XYZtestonlynotarealtoken_0000000000/getUpdates"
    result = subprocess.run([sed, "-E", match.group(1)], input=fake + "\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "XYZtestonly" not in result.stdout
    assert "bot123456789:***" in result.stdout


# ---------------------------------------------------------------------------
# Локальная сторона и окончания строк
# ---------------------------------------------------------------------------

def test_ship_gives_native_path_to_native_programs():
    """
    git.exe и scp.exe в Git Bash — нативные Windows-программы. При
    MSYS_NO_PATHCONV=1 путь "/tmp/…" доходит до них как есть и читается от корня
    диска, а не как /tmp Git Bash. Первый прогон упал именно так: git archive —
    «could not open '/tmp/market-bot-ship/release.tar.gz' for writing».
    """
    script = (DEPLOY / "ship.sh").read_text(encoding="utf-8")
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    archive_lines = [line for line in code if re.search(r"\bgit\b.*\barchive\b", line)]
    assert archive_lines, "в ship.sh не найден вызов git archive"
    assert all("WORK_LOCAL" in line for line in archive_lines), archive_lines
    assert all("core.autocrlf=false" in line for line in archive_lines), (
        "без core.autocrlf=false git на Windows отдаёт в архиве CRLF"
    )
    for line in code:
        if '"$SCP"' in line:
            assert "WORK_LOCAL" in line, line


LINUX_FILES = [
    "deploy/deploy.sh",
    "deploy/backup.sh",
    "deploy/ship.sh",
    "deploy/systemd/market-bot-backup.service",
    "deploy/systemd/market-bot-backup.timer",
    "deploy/docker-compose.prod.yml",
    "deploy/nginx/market-bot.conf.template",
    "Dockerfile",
]


@pytest.mark.parametrize("path", LINUX_FILES)
def test_linux_files_are_checked_out_with_lf(path):
    """
    10.09.2026 на прод уехал deploy.sh с CR в строке shebang: git на Windows с
    core.autocrlf=true отдаёт CRLF и в git archive. .gitattributes задаёт eol=lf.
    """
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", path], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("eol: lf"), result.stdout


def test_archive_ships_lf_even_with_autocrlf():
    """
    Сквозная проверка того, что реально уезжает на сервер: архив, собранный при
    core.autocrlf=true (как на Windows), не содержит CR в скрипте деплоя.
    """
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=true", "archive", "--worktree-attributes",
         "--format=tar", "HEAD", "deploy/deploy.sh"],
        cwd=ROOT, capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        content = archive.extractfile("deploy/deploy.sh").read()
    assert content.startswith(b"#!/bin/sh\n")
    assert b"\r" not in content


def test_nginx_step_sizes_server_names_hash_before_applying():
    """
    10.09.2026: nginx -t упал «could not build server_names_hash, you should
    increase server_names_hash_bucket_size: 32» — имя поддомена длиной 30
    символов не влезло в корзину по умолчанию. Шаг nginx обязан задать размер
    корзины ДО первой проверки конфига — своим файлом в conf.d, а не правкой
    общего nginx.conf, который принадлежит соседям по хосту.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    step = step_body(script, "step_nginx")
    assert "ensure_server_names_bucket" in step
    assert step.index("ensure_server_names_bucket") < step.index("nginx_apply")

    ensure = step_body(script, "ensure_server_names_bucket")
    assert "server_names_hash_bucket_size 64;" in ensure
    assert "/etc/nginx/conf.d/" in ensure
    assert "> /etc/nginx/nginx.conf" not in ensure and ">> /etc/nginx/nginx.conf" not in ensure


def test_token_step_validates_before_editing_and_never_prints_token():
    """
    Ротация токена 10.09.2026: шаг env существующий .env не трогает, поэтому
    замене токена нужен свой шаг. Он обязан проверить новый токен у Telegram ДО
    правки .env (иначе опечатка оставит бота без связи) и не выводить сам токен.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    step = step_body(script, "step_token")
    assert step.index("getMe") < step.index('cp -p "$APP/.env"'), "проверка у Telegram должна идти до правки .env"
    assert step.index('cp -p "$APP/.env"') < step.index('mv -f "$APP/.env.new" "$APP/.env"'), "копия .env — до замены"
    assert "--force-recreate" in step, "env_file читается только при создании контейнера"
    # $new и $old — сами токены; $old_code — только HTTP-код, его печатать можно
    token_var = re.compile(r"\$\{?(new|old)(?![A-Za-z0-9_])")
    for line in step.splitlines():
        if line.strip().startswith("echo"):
            assert not token_var.search(line), f"токен уходит в вывод: {line.strip()}"
    assert "token)   step_token" in script



def test_ai_key_step_validates_before_editing_and_never_exposes_key():
    """
    Ключ OpenRouter 11.09.2026: проверить у OpenRouter через прокси хоста (напрямую
    с этого IP — 403) ДО правки .env, вписать вместе с AI_PROXY_URL, пересоздать
    бот и API. Ключ не выводится и не передаётся аргументом процесса.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    step = step_body(script, "step_ai_key")
    assert step.index("/api/v1/key") < step.index('cp -p "$APP/.env"'), "проверка у OpenRouter — до правки .env"
    assert step.index('cp -p "$APP/.env"') < step.index('mv -f "$APP/.env.new" "$APP/.env"'), "копия .env — до замены"
    assert "-x http://127.0.0.1:12334" in step and "AI_PROXY_URL" in step
    assert "curl -s -K -" in step, "ключ — конфигом через stdin, не аргументом curl"
    assert 'ENVIRON["AI_KEY"]' in step and "-v k=" not in step, "ключ — через окружение, не аргументом awk"
    assert "--force-recreate" in step, "env_file читается только при создании контейнера"
    key_var = re.compile(r"\$\{?key(?![A-Za-z0-9_])")
    for line in step.splitlines():
        if line.strip().startswith("echo"):
            assert not key_var.search(line), f"ключ уходит в вывод: {line.strip()}"
    assert "ai-key)  step_ai_key" in script


def test_menu_step_points_bot_button_at_current_domain():
    """
    10.09.2026 мини-апп «не работал» при исправном сервере: кнопка меню бота
    вела на адрес прежнего развёртывания, и из Telegram не пришло ни одного
    запроса. Адрес живёт у Telegram, поэтому его установка — шаг деплоя: ставит
    кнопку на текущий домен, читает обратно и падает, если адрес не тот.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    step = step_body(script, "step_menu")
    assert 'url="https://$DOMAIN/"' in step
    assert step.index("setChatMenuButton") < step.index("getChatMenuButton"), "после установки — чтение обратно"
    assert "exit 1" in step, "несовпадение адреса обязано ронять шаг"
    secret_var = re.compile(r"\$\{?(token|api)(?![A-Za-z0-9_])")
    for line in step.splitlines():
        if line.strip().startswith("echo"):
            assert not secret_var.search(line), f"токен уходит в вывод: {line.strip()}"
    assert "menu)    step_menu" in script


def test_index_has_a_single_cache_control_header():
    """
    После выкладки curl показал у index.html два заголовка Cache-Control: `expires`
    добавляет свой (no-cache) рядом с add_header (no-store). Браузер применит
    строгий, но двойной заголовок — источник путаницы при разборе кэша.
    """
    index_block = re.search(r"location = /index\.html \{([^}]*)\}", nginx_directives(NGINX_SITE)).group(1)
    assert "expires" not in index_block
    assert index_block.count("Cache-Control") == 1


# ---------------------------------------------------------------------------
# Алерты и копия базы вне сервера
# ---------------------------------------------------------------------------

def test_backup_sends_only_a_copy_that_decrypts_back():
    """
    Зашифрованная копия уходит в Telegram только после того, как расшифровалась
    обратно в тот же архив; отметка об успехе — только после отправки.
    """
    script = (DEPLOY / "backup.sh").read_text(encoding="utf-8")
    body = step_body(script, "offsite")
    assert body.index('-d "$enc"') < body.index("tg_file"), "сначала проверка расшифровки, потом отправка"
    assert body.index("tg_file") < body.index(".offsite_ok"), "отметка об успехе — после отправки"
    assert 'rm -f "$src"' not in body, "сбой вывоза не должен трогать локальную копию"
    assert 'offsite "$DIR/$name.gz" || exit 1' in script, "сбой вывоза роняет юнит — его видит сторож"


def test_support_files_install_watchdog_and_keep_the_backup_key():
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    body = step_body(script, "install_support_files")
    for name in ("market-bot-watchdog.service", "market-bot-watchdog.timer", "watchdog.sh", "notify.sh"):
        assert name in body
        assert (DEPLOY / name).is_file() or (DEPLOY / "systemd" / name).is_file()
    assert 'if [ ! -s "$APP/backup.passphrase" ]' in body, \
        "ключ создаётся один раз: им зашифрованы уже отправленные копии"
    assert "enable --now market-bot-backup.timer market-bot-watchdog.timer" in body


def test_watchdog_timer_waits_before_first_run():
    timer = (DEPLOY / "systemd" / "market-bot-watchdog.timer").read_text(encoding="utf-8")
    assert "OnActiveSec=" in timer and "OnBootSec" not in timer, \
        "первый запуск — не сразу: иначе при установке сторож начинает с ложной тревоги"


def test_smoke_checks_timers_and_backup_key():
    body = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_smoke")
    assert "market-bot-watchdog.timer" in body
    assert "backup.passphrase" in body


def test_notify_passes_token_via_stdin_config():
    text = (DEPLOY / "notify.sh").read_text(encoding="utf-8")
    assert "-K -" in text
    curl_lines = [ln for ln in text.splitlines() if "curl " in ln and not ln.lstrip().startswith("#")]
    assert curl_lines and all("TG_TOKEN" not in ln for ln in curl_lines)


def test_release_installs_support_files_with_the_new_script():
    """
    Релиз выполняет прежняя копия deploy.sh. 10.09.2026 новый backup.sh уже
    скопировался, а notify.sh, сторож и ключ — нет: прежний скрипт о них не знал.
    После самообновления вспомогательные файлы ставит новый скрипт.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    body = step_body(script, "step_release")
    assert body.index('mv -f "$APP/deploy.sh.next" "$APP/deploy.sh"') < body.index('"$APP/deploy.sh" support')
    assert "support) step_support ;;" in script


def test_web_switch_is_atomic_and_keeps_previous_assets():
    """
    5.4: фронт подменялся двумя переименованиями — мгновение без каталога, а клиент
    со старым index.html не находил свои ассеты. Теперь ссылка переключается
    атомарно, ассеты прошлого выпуска переносятся в новый без перезаписи.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    switch = step_body(script, "switch_web")
    assert 'ln -sfn "$1" "$APP/web.next"' in switch and 'mv -T "$APP/web.next" "$APP/web"' in switch
    web = step_body(script, "step_web")
    assert web.index('cp -rn "$APP/web/assets/." "$new/assets/"') < web.index('switch_web "$new"'), \
        "ассеты прошлого выпуска переносятся до переключения"
    assert "web-rollback) step_web_rollback ;;" in script


def test_smoke_checks_web_is_a_release_link():
    body = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_smoke")
    assert '[ -L "$APP/web" ]' in body


def test_web_release_carries_only_the_previous_build_not_its_inheritance():
    """
    11.09.2026: выпуск фронта переносил в себя все ассеты прошлого — вместе с тем,
    что тот унаследовал сам, и мусор копился без конца (8 из 16 — со старым SDK).
    Теперь выпуск записывает список своих ассетов, а следующий переносит только его.
    """
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    web = step_body(script, "step_web")
    own_list = web.index('> "$new/.build-assets"')
    carry_by_list = web.index('done < "$APP/web/.build-assets"')
    carry_all = web.index('cp -rn "$APP/web/assets/." "$new/assets/"')
    assert own_list < carry_by_list, "свой список — до переноса чужих, иначе в него попадёт унаследованное"
    assert carry_by_list < carry_all, "полный перенос — только запасной путь для выпусков без списка"
    assert carry_all < web.index('switch_web "$new"')
