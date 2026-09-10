"""
Настоящий IP клиента за обратным прокси — единственная реализация.

До 10.09.2026 одна и та же функция жила копией в api/ip_whitelist.py и
api/rate_limit.py и в обеих брала ПЕРВЫЙ элемент X-Forwarded-For, если прямое
соединение пришло от доверенного прокси. Это ровно тот элемент, который
контролирует клиент.

nginx настроен так:
    proxy_set_header X-Real-IP       $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

$proxy_add_x_forwarded_for ДОПИСЫВАЕТ настоящий адрес в конец того, что прислал
клиент, а не заменяет. Запрос `curl -H "X-Forwarded-For: 127.0.0.1"` приезжает в
приложение как "127.0.0.1, <настоящий IP>", и первый элемент — подделка. Пример
белого списка из .env.example как раз содержал 127.0.0.1, так что подделка
открывала /api/settings/keys и /metrics; а лимитер при ротации значения видел
бесконечно много «разных клиентов».

Теперь, если соединение пришло от доверенного прокси:
  1. X-Real-IP — nginx ставит его через proxy_set_header, то есть ЗАМЕНЯЕТ
     присланное клиентом значение на $remote_addr. Подделать нельзя.
  2. иначе ПОСЛЕДНИЙ элемент X-Forwarded-For — его дописал наш прокси.
  3. иначе адрес прямого соединения.
От недоверенного соединения заголовкам не верим вовсе.
"""
import ipaddress
import logging

logger = logging.getLogger(__name__)

# Откуда может прийти запрос через наш собственный прокси: loopback (nginx на
# том же хосте) и приватные сети docker (API в контейнере видит шлюз 172.x).
TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "::1/128", "172.16.0.0/12", "10.0.0.0/8")
)


def _parse_ip(value):
    try:
        return ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError):
        return None


def is_trusted_proxy(ip_str: str) -> bool:
    addr = _parse_ip(ip_str)
    return addr is not None and any(addr in net for net in TRUSTED_PROXY_NETWORKS)


def client_ip(request) -> str:
    """
    IP клиента. Принимает starlette Request или любой объект с .client.host
    и .headers.get() — последнее ради тестов без поднятого сервера.
    """
    client = getattr(request, "client", None)
    direct = client.host if client else "unknown"

    if not is_trusted_proxy(direct):
        return direct

    headers = request.headers

    real = headers.get("x-real-ip")
    if real and _parse_ip(real) is not None:
        return real.strip()

    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if _parse_ip(last) is not None:
            return last

    return direct
