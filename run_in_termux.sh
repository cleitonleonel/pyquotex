#!/data/data/com.termux/files/usr/bin/bash
set -e

# Cores e emojis
GREEN="\033[1;32m"
RED="\033[1;31m"
YELLOW="\033[1;33m"
CYAN="\033[1;36m"
RESET="\033[0m"

echo -e "${CYAN}🚀 Iniciando setup do projeto ${YELLOW}PyQuotex${CYAN} no Termux...${RESET}"

# Diretório atual
CURRENT_DIR=$(basename "$PWD")

# Atualizando Termux
echo -e "${CYAN}📦 Atualizando pacotes...${RESET}"
pkg update -y && pkg upgrade -y
pkg autoclean -y && pkg clean -y

# Instalando dependências
echo -e "${CYAN}🔧 Instalando dependências necessárias...${RESET}"
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

# Clonando o repositório se necessário
if [ "$CURRENT_DIR" = "pyquotex" ]; then
    echo -e "${GREEN}📁 Já estamos no diretório ${YELLOW}pyquotex${RESET}"
else
    echo -e "${CYAN}📥 Clonando o repositório do PyQuotex...${RESET}"
    git clone https://github.com/cleitonleonel/pyquotex.git
    cd pyquotex || exit 1
fi

# Instalando dependências Python
echo -e "${CYAN}🐍 Instalando dependências do projeto com pip...${RESET}"
pip install -r requirements.txt

# Executando app.py
echo -e "${CYAN}🧪 Testando conexão com Quotex...${RESET}"
python app.py get-profile < /dev/tty || {
    echo -e "${RED}❌ Falha ao executar a função 'get-profile'. Verifique credenciais no código.${RESET}"
    exit 1
}

echo -e "${GREEN}✅ Projeto instalado com sucesso!${RESET}"

echo -e "${CYAN}✨ Feliz automação com PyQuotex!${RESET}"

