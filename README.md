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

### 2. Ou instale diretamente no seu projeto com Poetry:

```bash
poetry add git+https://github.com/cleitonleonel/pyquotex.git
```

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

### 💥 Comparativo de Versões

| Recurso                        | Open Source ✅ | Versão Privada ✨      |
|--------------------------------| ------------- | --------------------- |
| Suporte a Multilogin           | ❌             | ✅                     |
| Monitoramento de Sentimentos   | ✅             | ✅ + detecção avançada |
| Proxy/DNS Customizado          | ❌             | ✅                     |
| Robustez e Alta Confiabilidade | ✅             | ✨ Nível enterprise    |
| Velocidade de Execução         | ✅             | ⚡ Ultra rápido        |
| Suporte                        | ❌             | ✅                     |

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
