import sys
import locale
import pyfiglet

__author__ = "Cleiton Leonel Creton"
__version__ = "1.0.3"

__message__ = f"""
Use com moderação, pois gerenciamento é tudo!
suporte: cleiton.leonel@gmail.com ou +55 (27) 9 9577-2291
"""

LANGUAGE_MESSAGES = {
    "pt_BR": {
        "private_version_ad": (
            "🌟✨ Esta é a versão COMUNITÁRIA da PyQuotex! ✨🌟\n"
            "🔐  Desbloqueie todo o poder e recursos extras com a nossa versão PRIVADA.\n"
            "📤  Para mais funcionalidades e suporte exclusivo, considere uma doação ao projeto.\n"
            "➡️ Contato para doações e acesso à versão privada: https://t.me/pyquotex/852"
        )
    },
    "en_US": {
        "private_version_ad": (
            "🌟✨ This is the COMMUNITY version of PyQuotex! ✨🌟\n"
            "🔐  Unlock full power and extra features with our PRIVATE version.\n"
            "📤  For more functionalities and exclusive support, please consider donating to the project.\n"
            "➡️ Contact for donations and private version access: https://t.me/pyquotex/852"
        )
    }
}


def detect_user_language() -> str:
    """Attempts to detect the user's system language."""
    try:
        system_lang = locale.getlocale()[0]
        if system_lang and system_lang.startswith("pt"):
            return "pt_BR"
        return "en_US"
    except Exception:
        return "en_US"


def display_banner():
    """Displays the application banner, including the private version ad."""
    custom_font = pyfiglet.Figlet(font="ansi_shadow")
    ascii_art = custom_font.renderText("PyQuotex")

    user_lang = detect_user_language()
    ad_message = LANGUAGE_MESSAGES.get(user_lang, LANGUAGE_MESSAGES["en_US"])["private_version_ad"]

    banner = f"""{ascii_art}
    Author: {__author__} | Version: {__version__}
    Use with moderation, because management is everything!
    Support: cleiton.leonel@gmail.com or +55 (27) 9 9577-2291

    {ad_message}

    """
    print(banner)


def main():
    if (not getattr(sys, 'frozen', False)
            and not hasattr(sys, '_MEIPASS')):
        display_banner()


if __name__ == "__main__":
    main()
