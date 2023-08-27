# pyquotex
PyQuotex API

### Comunicado Importante
Devido a crescente busca por essa lib, aumentaram também os "espertinhos" que vem clonam o projeto,
pedem melhorias, não ajudam nem financeiramente e muito menos contribuem com algum código, e o pior
ainda vendem o código como se fosse propriedade deles, se está aqui público e nem eu mesmo vendo porque
alguém o deveria fazê-lo ???
Pois bem, diante disso estou arquivando o projeto e movendo os arquivos para um outro repositório privado
Agradeço a quem de alguma forma ajudou e vida que segue...

### Observação Importante
Por algum motivo a cloudflare acaba identificando o acesso automatizado a api da quotex e nos
aplica um block, o que impede o sucesso ao autenticar na plataforma por meio do uso de usuário 
e senha, recomendo o uso de python 3.8 ou superior para obter sucesso com essa api.
Para usuários windows é necessário instalar openssl mais recente possível, que pode ser obtido
aqui [Openssl-Windows](https://slproweb.com/products/Win32OpenSSL.html) .
Para usuários linux também é recomendada versões mais recentes possíveis do openssl, bastando
apenas executarem ```sudo apt install openssl```.

### Install
````shell
git clone https://github.com/cleitonleonel/pyquotex.git
cd pyquotex
pip install -r requirements.txt
````

### Import
```python
from quotexapi.stable_api import Quotex
```

### Login by email and password
if connect sucess return True,None  

if connect fail return False,None  
```python
from quotexapi.stable_api import Quotex

client = Quotex(email="aminfx1400@gmail.com", password="Amin1761")
client.debug_ws_enable = False
check_connect, message = client.connect()
print(check_connect, message)
```
### Check_win & buy sample

```python
import os
import asyncio
from quotexapi.stable_api import Quotex

client = Quotex(email="aminfx1400@gmail.com", password="Amin1761")
client.debug_ws_enable = False


def asset_parse(asset):
    new_asset = asset[:3] + "/" + asset[3:]
    if "_otc" in asset:
        asset = new_asset.replace("_otc", " (OTC)")
    else:
        asset = new_asset
    return asset


async def login(attempts=5):
    check, reason = await client.connect()
    print("Start your robot")
    attempt = 1
    while attempt <= attempts:
        if not client.check_connect():
            print(f"Tentando reconectar, tentativa {attempt} de {attempts}")
            check, reason = await client.connect()
            if check:
                print("Reconectado com sucesso!!!")
                break
            else:
                print("Erro ao reconectar.")
                attempt += 1
                if os.path.isfile("session.json"):
                    os.remove("session.json")
        elif not check:
            attempt += 1
        else:
            break
        await asyncio.sleep(5)
    return check, reason


async def buy_and_check_win():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        print("Saldo corrente: ", await client.get_balance())
        amount = 5
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset, direction, duration)
            print(status, buy_info)
            if status:
                print("Aguardando resultado...")
                if await client.check_win(buy_info["id"]):
                    print(f"\nWin!!! \nVencemos moleque!!!\nLucro: R$ {client.get_profit()}")
                else:
                    print(f"\nLoss!!! \nPerdemos moleque!!!\nPrejuízo: R$ {client.get_profit()}")
            else:
                print("Falha na operação!!!")
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo Atual: ", await client.get_balance())
        print("Saindo...")
    client.close()
```
