# quotexapi
Quotex API

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

client = Quotex(email="user@gmail.com", password="pwd")
client.debug_ws_enable = False
check_connect, message = client.connect()
print(check_connect, message)
```
### Check_win & buy sample

```python
from quotexapi.stable_api import Quotex

client = Quotex(email="user@gmail.com", password="pwd")
client.debug_ws_enable = False
check_connect, message = client.connect()
print(check_connect, message)
if check_connect:
    client.change_account("PRACTICE")
    amount = 30
    asset = "EURUSD_otc"  # "EURUSD_otc"
    direction = "call"
    duration = 10  # in seconds
    status, buy_info = client.buy(amount, asset, direction, duration)
    print(status, buy_info)
    print("Saldo corrente: ", client.get_balance())
    print("Saindo...")
client.close()
```
