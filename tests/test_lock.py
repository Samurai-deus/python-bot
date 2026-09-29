"""
Замок зависимостей (аудит 29.09.2026): образ и CI ставят пакеты из requirements.lock / requirements-dev.lock
с хэшами (--require-hashes). Версии верхнего уровня обязаны совпадать с requirements.txt: иначе обновление
Dependabot в requirements.txt выглядело бы применённым, а образ собирался бы со старой версией из замка.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pins(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)", line.strip())
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def test_runtime_lock_matches_requirements():
    want, got = pins(ROOT / "requirements.txt"), pins(ROOT / "requirements.lock")
    assert want, "в requirements.txt нет точных версий"
    diff = {k: (v, got.get(k)) for k, v in want.items() if got.get(k) != v}
    assert not diff, f"замок разошёлся с requirements.txt — пересобрать (шапка requirements.lock): {diff}"


def test_dev_lock_contains_the_runtime_lock():
    runtime, dev = pins(ROOT / "requirements.lock"), pins(ROOT / "requirements-dev.lock")
    diff = {k: (v, dev.get(k)) for k, v in runtime.items() if dev.get(k) != v}
    assert not diff, f"тесты и образ стоят на разных версиях: {diff}"


def test_every_locked_package_has_a_hash():
    for name in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / name).read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
        missing = [b.split("==")[0] for b in blocks if "==" in b and "--hash=sha256:" not in b]
        assert not missing, (name, missing)


def test_image_and_ci_install_from_the_lock_with_hashes():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in docker
    assert re.search(r"^FROM python:[\d.]+-slim@sha256:[0-9a-f]{64}", docker, re.M), "базовый образ закреплён по хэшу"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements-dev.lock" in ci
    assert not re.search(r"uses: [\w\-/]+@v\d", ci), "действия закреплены по SHA, а не по тегу"


def test_ci_steps_are_well_formed():
    """29.09.2026: замена в ci.yml склеила две команды в одну строку run — тесты в CI не поставились."""
    import yaml
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    for name, job in ci["jobs"].items():
        for step in job["steps"]:
            run = step.get("run")
            if isinstance(run, str) and run.startswith("pip install"):
                assert run.count("pip install") == 1, (name, run)
