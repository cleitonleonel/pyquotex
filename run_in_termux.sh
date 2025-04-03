#!/bin/bash
set -e

CURRENT_DIR=$(basename "$PWD")

pkg update -y \
  && pkg upgrade -y \
  && pkg autoclean -y \
  && pkg clean -y

pkg install \
  git \
  build-essential \
  cmake \
  ninja \
  libopenblas \
  libandroid-execinfo \
  patchelf \
  binutils-is-llvm \
  openssl \
  python-numpy -y

if [ "$CURRENT_DIR" = "pyquotex" ]; then
    echo "You're already in the pyquotex directory."
else
    git clone https://github.com/cleitonleonel/pyquotex.git -o pyquotex
    cd pyquotex || exit
fi

pip install poetry

poetry install

NUMPY_INFO=$(pip show numpy)

SITE_PACKAGES_PATH=$(poetry run python -c "import site; print(site.getsitepackages()[0])")
cp -r "${NUMPY_INFO}/numpy" "${SITE_PACKAGES_PATH}"

poetry run python app.py get_profile
