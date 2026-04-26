# 🌐 PyQuotex Documentation | Documentação | Documentación

## 🇺🇸 English

Welcome to the official PyQuotex API documentation. This comprehensive guide covers all aspects of the PyQuotex API usage, from basic setup to advanced implementations.

### Documentation Structure

The documentation is organized in the following sections:
- Installation and Configuration
- Connection and Authentication
- Trading Operations
- Market Data Retrieval
- Account Management
- Technical Indicators
- WebSocket
- Utilities and Helpers
- Basic Examples
- Technical Aspects
- Considerations and Warnings

📚 Documentation is available in here [English](en/index.md).

## 🇪🇸 Español

Bienvenido a la documentación oficial de la API PyQuotex. Esta guía completa cubre todos los aspectos del uso de la API PyQuotex, desde la configuración básica hasta implementaciones avanzadas.

### Estructura de la Documentación

La documentación está organizada en las siguientes secciones:
- Instalación y Configuración
- Conexión y Autenticación
- Operaciones de Trading
- Obtención de Datos del Mercado
- Gestión de Cuenta
- Indicadores Técnicos
- WebSocket
- Utilidades y Helpers
- Ejemplos Básicos
- Aspectos Técnicos
- Consideraciones y Advertencias

📚 La documentación está disponible aquí [Español](es/index.md).

## 🇧🇷 Português

Bem-vindo à documentação oficial da API PyQuotex. Este guia abrangente cobre todos os aspectos do uso da API PyQuotex, desde a configuração básica até implementações avançadas.

### Estrutura da Documentação

A documentação está organizada nas seguintes seções:
- Instalação e Configuração
- Conexão e Autenticação
- Operações de Trading
- Recuperação de Dados do Mercado
- Gerenciamento de Conta
- Indicadores Técnicos
- WebSocket
- Utilitários e Helpers
- Exemplos Básicos
- Aspectos Técnicos
- Considerações e Avisos

📚 A documentação está disponível aqui [Português](pt/index.md).

### Obter Histórico Profundo (Deep History)

Diferente do `get_candles` padrão que é limitado pelo broker, este método permite buscar grandes quantidades de velas
retrocedendo no tempo recursivamente.

```python
# Busca 1 hora de histórico (3600 segundos) para EURUSD (1 min)
candles = await client.get_historical_candles(
    asset="EURUSD", 
    amount_of_seconds=3600, 
    period=60
)

for candle in candles:
    print(f"Tempo: {candle['time']} | Close: {candle['close']}")

### Torneios (Tournaments)

Para participar de torneios, use o método `change_account` informando o `tournament_id`:

```python
# Mudar para conta de torneio / Switch to tournament account
await client.change_account("PRACTICE", tournament_id=12345)
```
```

---

### 🌟 Attribution | Atribución | Atribuição

- Original API by [Cleiton Leonel Creton](https://github.com/cleitonleonel)
- Documentation contributor: [Victalejo](https://github.com/victalejo)

### 📱 Support | Soporte | Suporte

Join our [Telegram group](https://t.me/+Uzcmc-NZvN4xNTQx) for support and discussions.

### 📄 License | Licencia | Licença

MIT License