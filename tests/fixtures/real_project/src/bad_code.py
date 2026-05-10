"""Sample Python file with intentional violations for integration testing."""

from os import *  # noqa — wildcard import violation

API_KEY = "abc123secret"  # hardcoded credential  # guideline: disable


def fetch_data(query: str) -> str:
    # TODO: add proper pagination
    result = eval(query)  # dangerous eval call
    try:
        return str(result)
    except:  # bare except
        pass
    print("failed to fetch")  # debug print
    return ""


def process(data: str) -> str:
    # FIXME: this logic is wrong
    return data.upper()


global_counter = 0


def increment() -> None:
    global global_counter  # global statement
    global_counter += 1
