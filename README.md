# pyquotex
PyQuotex API

### Comunicado Importante
Devido a crescente busca por essa lib, aumentaram também os "espertinhos" que vem clonam o projeto,
pedem melhorias, não ajudam nem financeiramente e muito menos contribuem com algum código, e o pior
ainda vendem o código como se fosse propriedade deles, se está aqui público e nem eu mesmo vendo porque
alguém o deveria fazê-lo ???
Para alertar a galera o sujeito está vendendo a API no [Youtube](https://www.youtube.com/watch?v=5x-da5K-a_4) .
Pois bem, diante disso estou arquivando o projeto e movendo os arquivos para um outro repositório privado.
Agradeço a quem de alguma forma ajudou e vida que segue...

### Novidades Sobre o Projeto
Após alguns dias de muitas solicitações pela volta do projeto e diante da demanda e busca pela API
resolvi retomar os trabalhos, porém em um novo repositório, privado.
Onde irei manter sempre atualizado e dar algum suporte, obviamente para ter acesso ao novo serviço,
terá que pagar um valor "simbólico", se pagam ao cidadão que vende o que copia aqui por que não podem
pagar a mim ? não é mesmo...
Aos interessados me procurem no [Telegram](https://t.me/cleitonLC) .

After a few days of many requests for feedback from the project and given the demand and search for the API,
I decided to resume work, but in a new private repository. Where I will always keep you updated 
and provide some support, obviously to have access to the new service, you will have to pay a "symbolic" amount,
if they pay the citizen who sells what they copy here why can't they pay me? this is not right... 
For those interested, look for me on [Telegram](https://t.me/cleitonLC) .

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
