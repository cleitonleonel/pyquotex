import os
from pathlib import Path

class SingletonDecorator:
    """
    A decorator that turns a class into a singleton.
    """
    def __init__(self, cls):
        self.cls = cls
        self.instance = None

    def __call__(self, *args, **kwargs):
        if self.instance is None:
            self.instance = self.cls(*args, **kwargs)
        return self.instance