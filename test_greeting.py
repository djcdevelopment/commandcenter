import pytest
from greeting import greet

def test_greet():
    assert greet("World") == "Hello, World!"
