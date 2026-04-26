#!/data/data/com.termux/files/usr/bin/bash
set -e

GREEN="\033[1;32m"
RED="\033[1;31m"
YELLOW="\033[1;33m"
CYAN="\033[1;36m"
RESET="\033[0m"


LANG_CODE=$(getprop persist.sys.locale | cut -d'-' -f1)
IS_PT=false
if [[ "$LANG_CODE" == "pt" ]]; then
    IS_PT=true
fi


msg() {
    if $IS_PT; then
        echo -e "$1"
    else
        echo -e "$2"
    fi
}


msg "${CYAN}🚀 Iniciando setup do projeto ${YELLOW}PyQuotex${CYAN} no Termux...${RESET}" \
    "${CYAN}🚀 Starting setup for ${YELLOW}PyQuotex${CYAN} project on Termux...${RESET}"

CURRENT_DIR=$(basename "$PWD")

msg "${CYAN}📦 Atualizando pacotes...${RESET}" \
    "${CYAN}📦 Updating packages...${RESET}"
pkg update -y && pkg upgrade -y
pkg autoclean -y && pkg clean -y

msg "${CYAN}🔧 Instalando dependências necessárias...${RESET}" \
    "${CYAN}🔧 Installing necessary dependencies...${RESET}"
pkg install -y \
  git \
  build-essential \
  cmake \
  ninja \
  libopenblas \
  libandroid-execinfo \
  patchelf \
  llvm lld binutils \
  openssl \
  python-numpy \
  python

if [ "$CURRENT_DIR" = "pyquotex" ]; then
    msg "${GREEN}📁 Já estamos no diretório ${YELLOW}pyquotex${RESET}" \
        "${GREEN}📁 We are already in the ${YELLOW}pyquotex${RESET} directory"
else
    msg "${CYAN}📥 Clonando o repositório do PyQuotex...${RESET}" \
        "${CYAN}📥 Cloning PyQuotex repository...${RESET}"
    git clone https://github.com/cleitonleonel/pyquotex.git
    cd pyquotex || exit 1
fi

msg "${CYAN}🐍 Instalando dependências do projeto com pip...${RESET}" \
    "${CYAN}🐍 Installing project dependencies with pip...${RESET}"
pip install -r requirements.txt

msg "${CYAN}🧪 Testando conexão com Quotex...${RESET}" \
    "${CYAN}🧪 Testing connection with Quotex...${RESET}"
python app.py login --demo < /dev/tty || {
    msg "${RED}❌ Falha ao executar a função 'login'. Verifique credenciais no código.${RESET}" \
        "${RED}❌ Failed to execute 'login' function. Check credentials in the code.${RESET}"
    exit 1
}

msg "${GREEN}✅ Projeto instalado com sucesso!${RESET}" \
    "${GREEN}✅ Project installed successfully!${RESET}"

msg "${CYAN}✨ Feliz automação com PyQuotex!${RESET}" \
    "${CYAN}✨ Happy automating with PyQuotex!${RESET}"