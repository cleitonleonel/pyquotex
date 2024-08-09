from os import path
from distutils.core import setup
from setuptools import find_packages

this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='pyquotex',
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    version='1.0.0',
    license='MIT',
    description='Quotex Api Client written in python.',
    author='Cleiton Leonel Creton',
    author_email='cleiton.leonel@gmail.com',
    url='https://github.com/cleitonleonel/pyquotex.git',
    # download_url='https://github.com/cleitonleonel/pyquotex/archive/v_1.0.0.tar.gz',
    keywords=['SOME', 'MEANINGFULL', 'KEYWORDS'],
    install_requires=[
        "certifi",
        "greenlet",
        "pyOpenSSL",
        "pytz",
        "requests-toolbelt",
        'requests',
        'beautifulsoup4',
        'websocket-client',
        'playwright',
        'playwright-stealth',
        'pyfiglet',
        'numpy'
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
    ],
)
