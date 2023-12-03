import sys
import pyfiglet
from pathlib import Path

__author__ = "Cleiton Leonel Creton"
__version__ = "1.0.0"

__message__ = f"""
Use com moderação, pois gerenciamento é tudo!
suporte: cleiton.leonel@gmail.com ou +55 (27) 9 9577-2291
"""


def resource_path(relative_path: str | Path) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    base_dir = Path(__file__).parent
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = Path(sys._MEIPASS)
    return base_dir / relative_path


BASE_DIR = resource_path("../")

if not getattr(sys, 'frozen', False) and not hasattr(sys, '_MEIPASS'):
    custom_font = pyfiglet.Figlet(font="ansi_shadow")
    ascii_art = custom_font.renderText("PyQuotex")
    art_effect = f"""{ascii_art}

            author: {__author__} 
            versão: {__version__}
            {__message__}"""


def main():
    print(art_effect)


if __name__ == "__main__":
    main()
