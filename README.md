# PyQuotex

<img src="https://github.com/cleitonleonel/pyquotex/blob/master/pyquotex.gif?raw=true" alt="pyquotex" width="200"/>

### Comunicado Importante
Devido a crescente busca por essa lib, aumentaram também os "espertinhos" que vem clonam o projeto,
pedem melhorias, não ajudam nem financeiramente e muito menos contribuem com algum código, e o pior
ainda vendem o código como se fosse propriedade deles, se está aqui público e nem eu mesmo vendo porque
alguém o deveria fazê-lo ???
Para alertar a galera o sujeito está vendendo a API no [Youtube](https://www.youtube.com/watch?v=5x-da5K-a_4) .
Pois bem, diante disso estou arquivando o projeto e movendo os arquivos para um outro repositório privado.
Agradeço a quem de alguma forma ajudou e vida que segue...

### Important report
Due to the growing search for this lib, there has also been an increase in the number of “smart guys” who have been cloning the project,
they ask for improvements, they don't even help financially, much less contribute any code, and the worst
they still sell the code as if it were their property, if it is public here and I don't even sell it because
Should someone do this???
To alert people, the guy is selling the API on [Youtube](https://www.youtube.com/watch?v=5x-da5K-a_4) .
Well, in light of this I'm archiving the project and moving the files to another private repository.
I thank those who helped in some way and the life that goes on...

### Novas informações:
Voltarei a manter uma versão open-source dessa lib, contando com a colaboração de todos, obviamente
a minha atenção estará mais voltada a versão fechada dessa lib e não teremos aqui as atualizações recorrentes
que são feitas na versão privada, mas poderão desfrutar de uma versão ainda assim funcional e que poderá
atender a bastante de vocês.Contribuam com essa lib seja com código, estrelinhas ou clicando no botão mais a baixo
e pague um café a esse humilde desenvolvedor.
Caso considerem o fato de migrarem para o repositório privado e obterem suporte e atualizações recorrentes
me chamem para uma conversa e vamos alinhar isso.
Aos interessados me procurem no [Telegram](https://t.me/cleitonLC) .

### New information:
I will keep the open source of this lib again, counting on everyone's collaboration, version obviously
my attention will be more focused on the closed version of this lib and we will not have recurring updates here
that are made in the private version, but you will be able to enjoy a version that is still functional and that can
This helps you a lot. Contribute to this library with code, stars or by clicking the button below
and buy this humble developer a coffee.
If you consider moving to the private repository and getting recurring support and updates
Call me for a chat and let's clarify this.
For more details, look for me on [Telegram](https://t.me/cleitonLC) .

### Observação Importante
Por algum motivo a cloudflare acaba identificando o acesso automatizado a api da quotex e nos
aplica um block, o que impede o sucesso ao autenticar na plataforma por meio do uso de usuário 
e senha, recomendo o uso de python 3.8 ou superior para obter sucesso com essa api.
Para usuários windows é necessário instalar openssl mais recente possível, que pode ser obtido
aqui [Openssl-Windows](https://slproweb.com/products/Win32OpenSSL.html) .
Para usuários linux também é recomendada versões mais recentes possíveis do openssl, bastando
apenas executarem ```sudo apt install openssl```.

### Important note
For some reason, cloudflare ends up identifying automated access to the quotex API and we
applies a block, which prevents successful authentication on the platform using a user
and password, I recommend using Python 3.8 or higher to be successful with this API.
For Windows users it is necessary to install the latest possible openssl, which can be obtained
here [Openssl-Windows](https://slproweb.com/products/Win32OpenSSL.html) .
For Linux users, the latest possible versions of openssl are also recommended, simply
just run ```sudo apt install openssl```.

## Let`s Go to the Private Repository
[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cleiton.leonel)

### Install
```shell
git clone https://github.com/cleitonleonel/pyquotex.git
cd pyquotex
pip install -r requirements.txt
python3 main.py <option>
```

### Import
```python
from quotexapi.stable_api import Quotex
```

### Login by email and password
if connect sucess return True,None  

if connect fail return False,None

```python
import os
import sys
from pathlib import Path
from quotexapi.stable_api import Quotex

email = "email"
password = "settings"
email_pass = "settings"
user_data_dir = "user_data_dir"

if not email.strip() or not password.strip():
    print("E-mail e Senha não podem estar em branco...")
    sys.exit()
if user_data_dir.strip():
    user_data_dir = "browser/instance/quotex.default"

client = Quotex(email=email,
                password=password,
                email_pass=email_pass,
                user_data_dir=Path(os.path.join(".", user_data_dir))
                )

```
### All Functions

```python
import os
import sys
import json
import random
import asyncio
from pathlib import Path
from quotexapi.stable_api import Quotex

email = "email"
password = "settings"
email_pass = "settings"
user_data_dir = "user_data_dir"

client = Quotex(email=email,
                password=password,
                email_pass=email_pass,
                user_data_dir=Path(os.path.join(".", user_data_dir))
                )
client.debug_ws_enable = False


def get_all_options():
    return """Opções disponíveis:
    - get_profile
    - get_balance
    - get_signal_data
    - get_payment
    - get_candle
    - get_candle_v2
    - get_realtime_candle
    - get_realtime_sentiment
    - assets_open
    - buy_simple
    - buy_and_check_win
    - buy_multiple
    - balance_refill
    - help
    """


def asset_parse(asset):
    new_asset = asset[:3] + "/" + asset[3:]
    if "_otc" in asset:
        asset = new_asset.replace("_otc", " (OTC)")
    else:
        asset = new_asset
    return asset


async def connect(attempts=5):
    check, reason = await client.connect()
    if not check:
        attempt = 0
        while attempt <= attempts:
            if not client.check_connect():
                check, reason = await client.connect()
                if check:
                    print("Reconectado com sucesso!!!")
                    break
                else:
                    print("Erro ao reconectar.")
                    attempt += 1
                    if Path(os.path.join(".", "session.json")).is_file():
                        Path(os.path.join(".", "session.json")).unlink()
                    print(f"Tentando reconectar, tentativa {attempt} de {attempts}")
            elif not check:
                attempt += 1
            else:
                break
            await asyncio.sleep(5)
        return check, reason
    print(reason)
    return check, reason


async def get_balance():
    check_connect, message = await connect()
    if check_connect:
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def get_profile():
    check_connect, message = await connect()
    if check_connect:
        profile = await client.get_profile()
        description = (f"\nUsuário: {profile.nick_name}\n"
                       f"Saldo Demo: {profile.demo_balance}\n"
                       f"Saldo Real: {profile.live_balance}\n"
                       f"Id: {profile.profile_id}\n"
                       f"Avatar: {profile.avatar}\n"
                       f"País: {profile.country_name}\n"
                       )
        print(description)
    print("Saindo...")
    client.close()


async def balance_refill():
    check_connect, message = await connect()
    if check_connect:
        result = await client.edit_practice_balance(5000)
        print(result)
    client.close()


async def buy_simple():
    check_connect, message = await connect()
    if check_connect:
        amount = 50
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset, direction, duration)
            print(status, buy_info)
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def buy_and_check_win():
    check_connect, message = await connect()
    if check_connect:
        print("Saldo corrente: ", await client.get_balance())
        amount = 50
        asset = "EURJPY_otc"  # "EURUSD_otc"
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


async def buy_multiple(orders=10):
    order_list = [
        {"amount": 5, "asset": "EURUSD_otc", "direction": "call", "duration": 60},
        {"amount": 10, "asset": "AUDCAD_otc", "direction": "put", "duration": 60},
        {"amount": 15, "asset": "AUDJPY_otc", "direction": "call", "duration": 60},
        {"amount": 20, "asset": "AUDUSD_otc", "direction": "put", "duration": 60},
        {"amount": 25, "asset": "CADJPY_otc", "direction": "call", "duration": 60},
        {"amount": 30, "asset": "EURCHF_otc", "direction": "put", "duration": 60},
        {"amount": 35, "asset": "EURGBP_otc", "direction": "call", "duration": 60},
        {"amount": 40, "asset": "EURJPY_otc", "direction": "put", "duration": 60},
        {"amount": 45, "asset": "GBPAUD_otc", "direction": "call", "duration": 60},
        {"amount": 50, "asset": "GBPJPY_otc", "direction": "put", "duration": 60},
    ]
    check_connect, message = await connect()
    for i in range(0, orders):
        print("\n/", 80 * "=", "/", end="\n")
        print(f"ABRINDO ORDEM: {i + 1}")
        order = random.choice(order_list)
        print(order)
        if check_connect:
            asset_query = asset_parse(order["asset"])
            asset_open = client.check_asset_open(asset_query)
            if asset_open[2]:
                print("OK: Asset está aberto.")
                status, buy_info = await client.buy(**order)
                print(status, buy_info)
            else:
                print("ERRO: Asset está fechado.")
            print("Saldo corrente: ", await client.get_balance())
            await asyncio.sleep(2)
    print("\n/", 80 * "=", "/", end="\n")
    print("Saindo...")
    client.close()


async def sell_option():
    check_connect, message = await connect()
    if check_connect:
        amount = 30
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "put"
        duration = 1000  # in seconds
        status, buy_info = await client.buy(amount, asset, direction, duration)
        print(status, buy_info)
        await client.sell_option(buy_info["id"])
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def assets_open():
    check_connect, message = await connect()
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
            print(i)
            print(i, client.check_asset_open(i))
    print("Saindo...")
    client.close()


async def get_candle():
    check_connect, message = await connect()
    if check_connect:
        asset = "AUDCAD_otc"
        # 60 at 86400
        offset = 180  # in seconds
        period = 86400  # in seconds / opcional
        candles = await client.get_candles(asset, offset, period)
        for candle in candles["data"]:
            print(candle)
    print("Saindo...")
    client.close()


async def get_payment():
    check_connect, message = await connect()
    if check_connect:
        all_data = client.get_payment()
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            print(asset_name, asset_data["payment"], asset_data["open"])
    print("Saindo...")
    client.close()


async def get_candle_v2():
    check_connect, message = await connect()
    if check_connect:
        asset = "EURUSD_otc"
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            # 60 at 180 seconds
            candles = await client.get_candle_v2(asset, 60)
            print(candles)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_realtime_candle():
    check_connect, message = await connect()
    if check_connect:
        list_size = 10
        asset = "USDJPY_otc"
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            client.start_candles_stream(asset)
            while True:
                prices = client.get_realtime_candles(asset)
                if len(prices[asset]) == list_size:
                    break
            print(prices)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_realtime_sentiment():
    check_connect, message = await connect()
    if check_connect:
        asset = "EURUSD_otc"
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            client.start_candles_stream(asset)
            while True:
                print(client.get_realtime_sentiment(asset), end="\r")
                await asyncio.sleep(0.5)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_signal_data():
    check_connect, message = await connect()
    if check_connect:
        client.start_signals_data()
        while True:
            signals = client.get_signal_data()
            if signals:
                print(json.dumps(signals, indent=4))
            await asyncio.sleep(1)
    print("Saindo...")
    client.close()


async def main():
    if len(sys.argv) != 2:
        print(f"Uso: {'./main' if getattr(sys, 'frozen', False) else 'python main.py'} <opção>")
        sys.exit(1)

    async def execute(argument):
        match argument:
            case "get_profile":
                return await get_profile()
            case "get_balance":
                return await get_balance()
            case "get_signal_data":
                return await get_signal_data()
            case "get_payment":
                return await get_payment()
            case "get_candle":
                return await get_candle()
            case "get_candle_v2":
                return await get_candle_v2()
            case "get_realtime_candle":
                return await get_realtime_candle()
            case "get_realtime_sentiment":
                return await get_realtime_sentiment()
            case "assets_open":
                return await assets_open()
            case "buy_simple":
                return await buy_simple()
            case "buy_and_check_win":
                return await buy_and_check_win()
            case "buy_multiple":
                return await buy_multiple()
            case "balance_refill":
                return await balance_refill()
            case "help":
                return print(get_all_options())
            case _:
                return print("Opção inválida. Use 'help' para obter a lista de opções.")

    option = sys.argv[1]
    await execute(option)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
        # loop.run_forever()
    except KeyboardInterrupt:
        print("Encerrando o programa.")
    finally:
        loop.close()
```
