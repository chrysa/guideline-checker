"""``sync-fastapi-route`` must read the body, not just the ``def`` keyword.

The standard says *do not block the event loop*, not *declare handlers async*.
Those coincide only when the body does no blocking work: FastAPI runs a plain
``def`` handler in a threadpool, so a handler that writes a file is correctly
synchronous — and ``async def`` there would stall the loop until the write returns.
"""

from __future__ import annotations

from guideline_checker.core.detection.ast_python import run_ast_checks


def test_a_handler_doing_nothing_blocking_is_flagged() -> None:
    src = '@app.get("/x")\ndef handler():\n    return {"ok": True}\n'

    assert run_ast_checks(["sync-fastapi-route"], src)


def test_a_handler_that_writes_a_file_is_left_alone() -> None:
    """The live false positive: /api/interpret/persist calls write_derived_ruleset."""
    src = '@app.post("/x")\ndef persist(req):\n    target.write_text(body, encoding="utf-8")\n    return 1\n'

    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_a_handler_shelling_out_is_left_alone() -> None:
    src = '@app.post("/x")\ndef run_it():\n    subprocess.run(["ls"], check=False)\n    return 1\n'

    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_a_handler_calling_a_sync_http_client_is_left_alone() -> None:
    src = '@app.get("/x")\ndef fetch():\n    return requests.get("http://h").json()\n'

    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_an_async_handler_is_never_flagged() -> None:
    """Whatever it does — async def is the compliant declaration."""
    src = '@app.get("/x")\nasync def handler():\n    return 1\n'

    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_the_message_says_why_it_fired() -> None:
    """ "sync route handler" alone sent someone converting a blocking handler."""
    _lineno, message = run_ast_checks(["sync-fastapi-route"], '@app.get("/x")\ndef h():\n    return 1\n')[0]

    assert "non-blocking" in message
