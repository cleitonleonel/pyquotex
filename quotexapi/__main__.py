import sys
import pyfiglet

__author__ = "Cleiton Leonel Creton"
__version__ = "1.0.2"

__message__ = f"""
Use com moderação, pois gerenciamento é tudo!
suporte: cleiton.leonel@gmail.com ou +55 (27) 9 9577-2291
"""

if not getattr(sys, 'frozen', False) and not hasattr(sys, '_MEIPASS'):
    custom_font = pyfiglet.Figlet(font="ansi_shadow")
    ascii_art = custom_font.renderText("PyQuotex")
    art_effect = f"""{ascii_art}

            author: {__author__} versão: {__version__}
            {__message__}"""


def main():
    print(art_effect)


if __name__ == "__main__":
    main()
