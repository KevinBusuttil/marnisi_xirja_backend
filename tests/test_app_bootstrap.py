from xirja_marnisi import __version__


def test_app_version_is_defined():
    assert isinstance(__version__, str)
    assert __version__.strip() == "0.0.1"
