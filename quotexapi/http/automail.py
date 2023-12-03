import imaplib
import email
import asyncio
from bs4 import BeautifulSoup


async def get_pin(email_address,
                  email_pass,
                  quotex_email="noreply@qxbroker.com",
                  attempts=5):
    pin_code = None
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(email_address, email_pass)
    mail.select("inbox")
    while count := 0 <= attempts:
        status, email_ids = mail.search(None, f'(FROM "{quotex_email}")')
        email_id_list = email_ids[0].split()
        status, email_data = mail.fetch(email_id_list[-1], "(RFC822)")
        raw_email = email_data[0][1]
        msg = email.message_from_bytes(raw_email)
        if msg.is_multipart():
            for part in msg.walk():
                # content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition:
                    body = part.get_payload(decode=True).decode()
                    if ("PIN" in body
                            or "Your authentication PIN-code:" in body
                            or "Seu código PIN de autenticação" in body):
                        soup = BeautifulSoup(body, "html.parser")
                        pin_code = soup.find("b").get_text()
        else:
            body = msg.get_payload(decode=True).decode()
            if ("PIN" in body
                    or "Your authentication PIN-code:" in body
                    or "Seu código PIN de autenticação" in body):
                soup = BeautifulSoup(body, "html.parser")
                pin_code = soup.find("b").get_text()
        if pin_code:
            return pin_code
        count += 1
        if count > attempts:
            print("Nenhum email da Quotex...")
            mail.logout()
        await asyncio.sleep(1)
    return pin_code
