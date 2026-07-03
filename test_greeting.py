from greeting import greet


def test_greet_world():
    assert greet("World") == "Hello, World!"
