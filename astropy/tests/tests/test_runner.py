import pytest

from astropy.tests.helper import _skip_docstring_tests_with_optimized_python

# Renamed these imports so that them being in the namespace will not
# cause pytest 3 to discover them as tests and then complain that
# they have __init__ defined.
from astropy.tests.runner import TestRunner as _TestRunner
from astropy.tests.runner import TestRunnerBase as _TestRunnerBase
from astropy.tests.runner import keyword


def test_disable_kwarg():
    class no_remote_data(_TestRunner):
        @keyword()
        def remote_data(self, remote_data, kwargs):
            return NotImplemented

    with pytest.deprecated_call(match="The TestRunner"):
        r = no_remote_data(".")
    with pytest.raises(TypeError), pytest.deprecated_call(match="The test runner"):
        r.run_tests(remote_data="bob")


def test_wrong_kwarg():
    with pytest.deprecated_call(match="The TestRunner"):
        r = _TestRunner(".")
    with pytest.raises(TypeError), pytest.deprecated_call(match="The test runner"):
        r.run_tests(spam="eggs")


def test_invalid_kwarg():
    class bad_return(_TestRunnerBase):
        @keyword()
        def remote_data(self, remote_data, kwargs):
            return "bob"

    with pytest.deprecated_call(match="The TestRunner"):
        r = bad_return(".")
    with pytest.raises(TypeError), pytest.deprecated_call(match="The test runner"):
        r.run_tests(remote_data="bob")


def test_new_kwarg():
    class Spam(_TestRunnerBase):
        @keyword()
        def spam(self, spam, kwargs):
            return [spam]

    with pytest.deprecated_call(match="The TestRunner"):
        r = Spam(".")

    args = r._generate_args(spam="spam")

    assert ["spam"] == args


def test_priority():
    class Spam(_TestRunnerBase):
        @keyword()
        def spam(self, spam, kwargs):
            return [spam]

        @keyword(priority=1)
        def eggs(self, eggs, kwargs):
            return [eggs]

    with pytest.deprecated_call(match="The TestRunner"):
        r = Spam(".")

    args = r._generate_args(spam="spam", eggs="eggs")

    assert ["eggs", "spam"] == args


@_skip_docstring_tests_with_optimized_python
def test_docs():
    class Spam(_TestRunnerBase):
        @keyword()
        def spam(self, spam, kwargs):
            """
            Spam Spam Spam
            """
            return [spam]

        @keyword()
        def eggs(self, eggs, kwargs):
            """
            eggs asldjasljd
            """
            return [eggs]

    with pytest.deprecated_call(match="The TestRunner"):
        r = Spam(".")
    assert "deprecated" in r.run_tests.__doc__
    assert "eggs" in r.run_tests.__doc__
    assert "Spam Spam Spam" in r.run_tests.__doc__
