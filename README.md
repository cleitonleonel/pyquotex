# 🚀 PyQuotex

---
<p align="center">
  <a href="https://github.com/cleitonleonel/pyquotex">
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
https://cleitonleonel.github.io/pyquotex/


## 🛠 Instalação

### 1. Clone o repositório:

```bash
git clone https://github.com/cleitonleonel/pyquotex.git
cd pyquotex
poetry install
poetry run python app.py
```

```bash
poetry add git+https://github.com/cleitonleonel/pyquotex.git
```

### 3. Otimização de Performance (Opcional) / Optional Extras

Para melhor performance no processamento de dados (recomendado para uso em servidores), você pode instalar a biblioteca
com suporte ao `orjson`:

```bash
poetry add "pyquotex[fast] @ git+https://github.com/cleitonleonel/pyquotex.git"
```

Outros extras opcionais:

| Extra | Adiciona / Adds / Añade |
| --- | --- |
| `fast` | `orjson` para parse JSON mais rápido |
| `socks` | `httpx[socks]` para URLs `socks5://` |
| `stealth` | `curl_cffi` para fingerprint TLS de navegador real (Chrome/Firefox) |

```bash
pip install 'pyquotex[fast,socks,stealth]'
```

*Nota: No Termux (Android), recomendamos usar a instalação padrão sem `orjson` para evitar erros de compilação.*

### 2.1. Instale com um comando no Termux (Android):

```shell
curl -sSL https://raw.githubusercontent.com/cleitonleonel/pyquotex/refs/heads/master/run_in_termux.sh | sh
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

## 🆕 Recursos Avançados / Advanced Features / Características Avanzadas

Funcionalidades antes exclusivas da versão privada agora disponíveis no open-source.
*Previously private-only features now available in the OSS build.*

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
        use_browser_tls=True,         # requires pip install pyquotex[stealth]
    ),
    # Multilogin profile bootstrap (v1 agent or v3 cloud API)
    multilogin=MultiloginConfig(profile_id="…", folder_id="…", token="…", api="v3"),
    # Sentiment monitor with anomaly + divergence detection
    enable_sentiment_monitor=True,
    # Auto-reconnect with exponential backoff and subscription replay
    reconnect_policy=ReconnectPolicy(initial_delay=1.0, max_delay=60.0),
)
```

📖 Documentação completa / Full guide / Guía completa:
- 🇬🇧 [Advanced Features](docs/en/12.%20Advanced%20Features.md)
- 🇧🇷 [Recursos Avançados](docs/pt/12.%20Recursos%20Avançados.md)
- 🇪🇸 [Características Avanzadas](docs/es/12.%20Características%20Avanzadas.md)
- 🧪 Exemplo: [`examples/private_features.py`](examples/private_features.py)

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

## 🔒 Versão Privada Disponível

Uma versão privada está disponível com recursos adicionais, estabilidade aprimorada e melhor suporte.

👉 [Acesse a versão privada](https://t.me/pyquotex/852) para desbloquear o máximo do PyQuotex!

### 💥 Comparativo de Versões / Version Comparison

| Recurso / Feature                              | Open Source ✅                 | Versão Privada ✨              |
|------------------------------------------------|--------------------------------|--------------------------------|
| Suporte a Multilogin                           | ✅ v1 agent + v3 cloud API     | ✅                             |
| Proxy/DNS Customizado                          | ✅ HTTP/SOCKS + DNS overrides  | ✅                             |
| TLS de navegador real (curl_cffi)              | ✅ Optional `[stealth]` extra  | ✅                             |
| Monitoramento de Sentimentos                   | ✅ + spike + divergência       | ✅                             |
| Persistência de Sentimentos (SQLite)           | ✅                             | ✅                             |
| Correlação Cross-Asset de Sentimentos          | ✅                             | ✅                             |
| Auto-reconexão + replay de subscrições         | ✅                             | ✅                             |
| Robustez e Alta Confiabilidade                 | ✅                             | ✨ Nível enterprise            |
| Velocidade de Execução                         | ✅                             | ⚡ Ultra rápido                |
| Suporte                                        | ❌                             | ✅                             |

---

## 🤝 Apoie este projeto

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cleiton.leonel)

### 💸 Criptomoedas

* **Dogecoin (DOGE)**: `DMwSPQMk61hq49ChmTMkgyvUGZbVbWZekJ`
* **Bitcoin (BTC)**: `bc1qtea29xkpyx9jxtp2kc74m83rwh93vjp7nhpgkm`
* **Ethereum (ETH)**: `0x20d1AD19277CaFddeE4B8f276ae9f3E761523223`
* **Solana (SOL)**: `4wbE2FVU9x4gVErVSsWwhcdXQnDBrBVQFvbMqaaykcqo`

---

## 📞 Contato

* Telegram: [cleitonlc](https://t.me/cleitonlc)
* GitHub: [cleitonleonel](https://github.com/cleitonleonel)
* LinkedIn: [Cleiton Leonel](https://www.linkedin.com/in/cleiton-leonel-creton-331138167/)

---
