# 🚀 PyQuotex

---
<p align="center">
  <a href="https://github.com/iahmedani/pyquotex">
    <img src="pyquotex.png" alt="pyquotex" width="350" height="auto" title="PyQuotex"/>
  </a>
</p>
<p align="center">
  <i>Unofficial Quotex Library API Client written in Python!</i>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13-green" alt="Python Versions"/>
</p>

---

## 🍴 About this Fork

This repository (`iahmedani/pyquotex`) is a **fork** of the original
[`cleitonleonel/pyquotex`](https://github.com/cleitonleonel/pyquotex) by
Cleiton Leonel Creton. It continues development with additional fixes,
performance work, and a bundled REST + WebSocket API server.

What's added in this fork on top of upstream:

- **v1.2** — Multilogin profiles (v1 agent + v3 cloud), proxy + DNS
  overrides, browser-grade TLS via `curl_cffi`, sentiment monitor with
  spike + divergence detection, sentiment persistence (SQLite),
  cross-asset sentiment correlation, auto-reconnect with subscription
  replay, verified pending-order wire format + lifecycle bridge.
- **v1.3** — Latency + robustness audit (event-driven streams, O(1)
  data structures, concurrent-connect lock, heartbeat error signal).
- **v1.4** — Bundled FastAPI + WebSocket relay (`pyquotex.webapi`),
  Docker image, two-step `POST /auth/otp` PIN flow, auth-race fix in
  `send_ssid()` so successful logins are no longer reported as 502.

All public API additions are additive — code that worked on upstream
continues to work here. See [`CHANGELOG.md`](CHANGELOG.md) for the
version-by-version history.

---

## 📘 Sobre o projeto (PT-BR)

O **PyQuotex** nasceu como uma biblioteca open-source para facilitar a comunicação com a plataforma Quotex via WebSockets. Com o tempo e devido ao uso indevido, uma versão privada mais segura e robusta foi criada.

---

## 📘 About the Project (EN)

**PyQuotex** started as an open-source library to make it easier to communicate with the Quotex platform using WebSockets. Due to misuse, a more robust private version was later introduced.

---

## 📘 Sobre el Proyecto (ES)

**PyQuotex** nació como una biblioteca de código abierto para facilitar la comunicación con la plataforma Quotex a
través de WebSockets. Con el tiempo y debido al uso indebido, se creó una versión privada más segura y robusta.

---

## 🎯 Objetivo da Biblioteca / Library Goal / Objetivo

Prover ferramentas para desenvolvedores integrarem seus sistemas com a plataforma Quotex, permitindo operações automatizadas de forma segura e eficiente.

> ⚠️ Esta biblioteca **não é um robô de operações** e não toma decisões por conta própria.

---

# 📚 Documentação Completa

The [`docs/`](docs/) folder in this repo holds the full reference (EN /
PT / ES). Start with [docs/en/12. Advanced Features.md](docs/en/12.%20Advanced%20Features.md)
or [docs/en/API_REFERENCE.md](docs/en/API_REFERENCE.md).


## 🛠 Instalação

### 1. Clone o repositório:

```bash
git clone https://github.com/iahmedani/pyquotex.git
cd pyquotex
poetry install

# Run the end-to-end library demo:
poetry run python examples/private_features.py

# …or launch the bundled REST + WebSocket server (v1.4.0+):
poetry install --extras webapi
poetry run python -m pyquotex.webapi          # serves http://localhost:8000
```

> There is no `app.py` entry point. Use `pyquotex` as a library (see the
> [usage example](#-exemplo-de-uso) below), run a script from
> [`examples/`](examples/), or start the bundled server with
> `python -m pyquotex.webapi` — see [Web API](docs/en/13.%20Web%20API.md).

Or add it as a dependency of your own project:

```bash
poetry add git+https://github.com/iahmedani/pyquotex.git
```

### 3. Otimização de Performance (Opcional) / Optional Extras

Para melhor performance no processamento de dados (recomendado para uso em servidores), você pode instalar a biblioteca
com suporte ao `orjson`:

```bash
poetry add "pyquotex[fast] @ git+https://github.com/iahmedani/pyquotex.git"
```

Outros extras opcionais:

| Extra | Adds |
| --- | --- |
| `fast` | `orjson` for faster JSON parsing |
| `socks` | `httpx[socks]` for `socks5://` proxy URLs |
| `stealth` | `curl_cffi` for browser-grade TLS fingerprints (Chrome/Firefox) |
| `webapi` | `fastapi` + `uvicorn` for the bundled REST + WebSocket API server (v1.4.0+) |

```bash
pip install 'pyquotex[fast,socks,stealth,webapi]'
```

*Nota: No Termux (Android), recomendamos usar a instalação padrão sem `orjson` para evitar erros de compilação.*

### 2.1. Instale com um comando no Termux (Android):

```shell
curl -sSL https://raw.githubusercontent.com/iahmedani/pyquotex/refs/heads/master/run_in_termux.sh | sh
```


## 🧪 Exemplo de uso

```python
from pyquotex.stable_api import Quotex

client = Quotex(
  email="your_email",
  password="your_password",
  lang="pt"  # ou "en", "es"
)

await client.connect()
print(await client.get_balance())

# Usar conta de torneio / Use tournament account
from pyquotex.utils.account_type import AccountType
await client.change_account(AccountType.DEMO, tournament_id=1)

# Buscar histórico profundo paralelo / Fetch parallel deep history
# ⚠️ CUIDADO: O uso excessivo de workers (> 10) pode causar banimento!
# ⚠️ WARNING: Excessive workers (> 10) may lead to a ban!
# ⚠️ ADVERTENCIA: ¡El uso excesivo de workers (> 10) puede causar baneo!
# Recomendado: 2-5 workers.
candles = await client.get_historical_candles("EURUSD", amount_of_seconds=86400, period=60, max_workers=5)

await client.close()
```

---

## 💡 Recursos Principais / Main Features / Funciones Principales

| Função                     | PT-BR                           | EN                        | ES                              |
|----------------------------|---------------------------------|---------------------------|---------------------------------|
| `connect()`                | Conecta via WebSocket           | Connects via WebSocket    | Conecta vía WebSocket           |
| `get_balance()`            | Retorna o saldo da conta        | Returns account balance   | Retorna el saldo                |
| `buy()`                    | Realiza uma operação            | Places a trade            | Realiza una operación           |
| `get_candles()`            | Retorna candles recentes        | Returns recent candles    | Retorna velas recientes         |
| `get_historical_candles()` | **Histórico profundo paralelo** | **Parallel deep history** | **Historial profundo paralelo** |
| `get_realtime_sentiment()` | Sentimento em tempo real        | Real-time sentiment       | Sentimiento en tiempo real      |
| `change_account()`         | Alterna entre Real e Demo       | Switch Real/Demo          | Cambiar entre Real/Demo         |
| `state.status`             | Status do WebSocket (Enum)      | WebSocket Status (Enum)   | Estado del WebSocket (Enum)     |
| `state.auth_status`        | Status da Autenticação (Enum)   | Auth Status (Enum)        | Estado de Autenticación (Enum)  |

---

## 🆕 Advanced Features (v1.2 + v1.3)

Functionality that used to be private-only is now in the open-source build.
*Funcionalidades antes exclusivas da versão privada agora disponíveis no open-source.*
*Funcionalidades antes exclusivas de la versión privada ahora disponibles en el OSS build.*

```python
from pyquotex import (
    Quotex, ProxyConfig, MultiloginConfig,
    SentimentMonitor, SentimentStore, SentimentCorrelationAnalyzer,
    ReconnectPolicy,
)

client = Quotex(
    email="…", password="…",
    # Proxy + DNS overrides + browser-impersonating TLS
    proxy_config=ProxyConfig(
        url="http://user:pass@proxy:8080",
        dns_overrides={"qxbroker.com": "1.2.3.4"},
        use_browser_tls=True,                            # requires pip install pyquotex[stealth]
    ),
    # Multilogin profile bootstrap (v1 agent or v3 cloud API)
    multilogin=MultiloginConfig(profile_id="…", folder_id="…", token="…", api="v3"),
    # Sentiment monitor with anomaly + divergence detection
    enable_sentiment_monitor=True,
    # Auto-reconnect with exponential backoff and subscription replay
    reconnect_policy=ReconnectPolicy(initial_delay=1.0, max_delay=60.0),
)
```

### What's new in v1.3.0

A robustness + speed audit added nine fixes on top of v1.2.x. **All additive — no public-API breakage.**

| Area | Highlights |
| --- | --- |
| **Latency** | `check_connect()` no longer sleeps 2 s on every call (hits 9+ public methods) · `realtime_price` is `deque(maxlen=1000)` (O(n)→O(1) eviction) · profile UTC offset cached per session · `start_realtime_sentiment` / `start_candles_one_stream` / `start_candles_all_size_stream` are event-driven (was 200 ms polls) · `pending_ticket_map` close mirror is O(1) via reverse index |
| **Reliability** | Concurrent `connect()` serialised by `asyncio.Lock` · heartbeat fires `status_changed=ERROR` on send failure (was silent) · `stop_candles_stream` cleans up reconnect-replay lists · pending lifecycle bridge state cleared on every reconnect path |
| **Tests** | 12 new regression tests (`tests/test_v13_robustness_speed.py`); full suite **138/138** |

Typical hot-asset numbers measured in the in-sandbox simulation:

- `check_connect()`: **2000 ms → 0.0 ms**
- `open_pending()` confirm: **up to 200 ms poll → ~5 ms event**
- `check_win()` resolve: **up to 200 ms poll → ~10 ms event**

📖 Full reference (English):
- [Advanced Features](docs/en/12.%20Advanced%20Features.md) — start here
- [API Reference](docs/en/API_REFERENCE.md) — every public method's signature + behaviour
- [Trading Operations](docs/en/3.%20Trading%20Operations.md) — buy / pending / result tracking
- [Web API (REST + WebSocket, v1.4.0+)](docs/en/13.%20Web%20API.md) — `pip install pyquotex[webapi]`, then `python -m pyquotex.webapi` or `docker compose up`
- [CHANGELOG](CHANGELOG.md) — version-by-version history
- 🧪 [`examples/private_features.py`](examples/private_features.py) — end-to-end library demo
- 🌐 [`examples/webapi_demo.py`](examples/webapi_demo.py) — REST + WebSocket client demo

---

## 🏗️ Gestão de Estado e Eventos / State & Event Management

O PyQuotex utiliza um sistema moderno de Enums e Eventos para controle de conexão:

```python
from pyquotex.global_value import WebsocketStatus, AuthStatus

# Verificar status via Enum
if client.api.state.status == WebsocketStatus.CONNECTED:
    print("Conectado!")

# Aguardar eventos de mudança de estado
await client.api.event_registry.wait_event("status_changed")
await client.api.event_registry.wait_event("auth_changed")
```

---

### 💥 Comparativo de Versões / Version Comparison

| Feature                                                       | Open Source ✅                       | Private ✨            |
|---------------------------------------------------------------|--------------------------------------|-----------------------|
| Multilogin support                                            | ✅ v1 agent + v3 cloud API           | ✅                    |
| Custom proxy / DNS                                            | ✅ HTTP / SOCKS + DNS overrides      | ✅                    |
| Real-browser TLS (curl_cffi)                                  | ✅ optional `[stealth]` extra        | ✅                    |
| Sentiment monitoring                                          | ✅ + spike + divergence detection    | ✅                    |
| Sentiment persistence (SQLite)                                | ✅                                   | ✅                    |
| Cross-asset sentiment correlation                             | ✅                                   | ✅                    |
| Auto-reconnect + subscription replay                          | ✅                                   | ✅                    |
| Pending orders (verified wire spec, lifecycle bridge)         | ✅ v1.2 + v1.3                       | ✅                    |
| Event-driven streams (`buy`, `check_win`, `start_*`, pending) | ✅ v1.3                              | ✅                    |
| Concurrent-connect lock + heartbeat error signal              | ✅ v1.3                              | ✅                    |
| Robustness & reliability                                      | ✅                                   | ✨ enterprise tier    |
| Execution speed                                               | ✅ low-millisecond hot paths         | ⚡ ultra-fast          |
| Support                                                       | ❌ best-effort community             | ✅                    |

---

## 🙏 Credits

Original `PyQuotex` library by **Cleiton Leonel Creton**
([github.com/cleitonleonel](https://github.com/cleitonleonel)). This
fork builds on that foundation — the WebSocket protocol work,
documentation skeleton, and Termux installer all come from upstream.

## 🐛 Issues / Contributions

Please open issues and pull requests against this fork at
[`iahmedani/pyquotex`](https://github.com/iahmedani/pyquotex/issues).
Bugs that also reproduce against the upstream repo are welcome to be
reported there as well.

---
