import asyncio
import os
from pathlib import Path
from singleton_decorator import SingletonDecorator

@SingletonDecorator
class MyConnection:
    """
    This class represents a connection object and provides methods for connecting to a client.
    """

    def __init__(self, client_instance):
        self.client = client_instance

    async def connect(self, attempts=5):
        check, reason = await self.client.connect()
        if not check:
            attempt = 0
            while attempt <= attempts:
                if not self.client.check_connect():
                    check, reason = await self.client.connect()
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
    
    def close(self):
        """
        Closes the client connection.
        """
        self.client.close()
