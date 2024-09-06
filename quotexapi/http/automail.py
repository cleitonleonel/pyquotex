import re
import imaplib
import email
import asyncio


async def get_pin(email_address, email_pass, quotex_email="noreply@qxbroker.com", attempts=5):
    pin_code = None
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_address, email_pass)
        mail.select("inbox")
    except imaplib.IMAP4.error as e:
        print(f"Erro ao conectar: {e}")
        return None

    while attempts > 0:
        status, email_ids = mail.search(None, f'(FROM "{quotex_email}")')
        email_id_list = email_ids[0].split()

        if not email_id_list:
            print("Nenhum e-mail encontrado")
            mail.logout()
            return None

        status, email_data = mail.fetch(email_id_list[-1], "(RFC822)")
        raw_email = email_data[0][1]
        msg = email.message_from_bytes(raw_email)

        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition:
                    body = part.get_payload(decode=True).decode()
                    match = re.search(r'<b>(\d+)</b>', body)
                    if match:
                        pin_code = match.group(1)
                        break
        else:
            body = msg.get_payload(decode=True).decode()
            match = re.search(r'<b>(\d+)</b>', body)
            if match:
                pin_code = match.group(1)

        if pin_code:
            mail.logout()
            return pin_code

        attempts -= 1
        await asyncio.sleep(1)

    print("Nenhum e-mail da Quotex...")
    mail.logout()
    return pin_code
