import os
import re
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from ..http.automail import get_pin
from playwright_stealth import stealth_async
from ..utils.playwright_install import install
from playwright.async_api import Playwright, async_playwright, expect


class Browser(object):
    user_data_dir = None
    base_url = 'qxbroker.com'
    https_base_url = f'https://{base_url}'
    email = None
    password = None
    email_pass = None
    args = [
        '--disable-web-security',
        '--no-sandbox'
    ]

    def __init__(self, api):
        self.api = api
        self.html = None

    async def run(self, playwright: Playwright) -> None:
        if self.user_data_dir:
            browser = playwright.firefox
            context = await browser.launch_persistent_context(
                self.user_data_dir,
                args=self.args,
                user_agent="Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
                headless=True,
                viewport={
                    "width": 1280,
                    "height": 720,
                }
            )
            page = context.pages[0]
        else:
            browser = await playwright.firefox.launch(
                headless=True,
                args=self.args,
                ignore_default_args=False,
            )
            context = await browser.new_context(
                viewport={
                    "width": 1280,
                    "height": 720
                },
                user_agent='Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0',
            )
            page = await context.new_page()
        await stealth_async(page)
        await page.goto(url=f"{self.https_base_url}/pt/sign-in")
        if page.url != f"{self.https_base_url}/pt/trade":
            await page.wait_for_timeout(5000)
            await page.get_by_role("textbox", name="E-mail").click()
            await page.get_by_role("textbox", name="E-mail").fill(self.email)
            await page.get_by_role("textbox", name="Senha").click()
            await page.get_by_role("textbox", name="Senha").fill(self.password)
            await page.get_by_role("button", name="Entrar").click()
            async with page.expect_navigation():
                await page.wait_for_timeout(5000)
                soup = BeautifulSoup(await page.content(), "html.parser")
                if "Insira o código PIN que acabamos de enviar para o seu e-mail" in soup.get_text():
                    pin_code = await get_pin(self.email, self.email_pass)
                    if pin_code:
                        code = pin_code
                    else:
                        code = input("Insira o código PIN que acabamos de enviar para o seu e-mail: ")
                    try:
                        await page.get_by_placeholder("Digite o código de 6 dígitos...").click()
                        await page.get_by_placeholder("Digite o código de 6 dígitos...").fill(code)
                        await page.get_by_role("button", name="Entrar").click()
                    except:
                        await page.get_by_placeholder("Digite o código de 6 dígitos...").click()
                        await page.get_by_placeholder("Digite o código de 6 dígitos...").fill(code)
                        await page.get_by_role("button", name="Entrar").click()
        await page.wait_for_timeout(5000)
        cookies = await context.cookies()
        source = await page.content()
        self.html = BeautifulSoup(source, "html.parser")
        user_agent = await page.evaluate("() => navigator.userAgent;")
        self.api.session_data["user_agent"] = user_agent
        script = self.html.find_all("script", {"type": "text/javascript"})
        status, message = self.success_login()
        if not status:
            print(message)
            await context.close() if self.user_data_dir else await browser.close()
            return
        settings = script[1].get_text().strip().replace(";", "")
        match = re.sub("window.settings = ", "", settings)
        token = json.loads(match).get("token")
        self.api.session_data["token"] = token
        output_file = Path(os.path.join(self.api.resource_path, "session.json"))
        output_file.parent.mkdir(exist_ok=True, parents=True)
        cookiejar = requests.utils.cookiejar_from_dict({c['name']: c['value'] for c in cookies})
        cookies_string = '; '.join([f'{c.name}={c.value}' for c in cookiejar])
        self.api.session_data["cookies"] = cookies_string
        output_file.write_text(
            json.dumps({"cookies": cookies_string, "token": token, "user_agent": user_agent}, indent=4)
        )
        await context.close() if self.user_data_dir else await browser.close()

    def success_login(self):
        match = self.html.find(
            "div", {"class": "hint -danger"}
        )
        if match is None:
            return True, "Login successful."

        return False, f"Login failed. {match.text.strip()}"

    async def main(self) -> None:
        async with async_playwright() as playwright:
            # install(playwright.firefox, with_deps=True)
            await self.run(playwright)

    async def get_cookies_and_ssid(self):
        await self.main()
