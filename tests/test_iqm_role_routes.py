"""The IQM route table has to cover what a current client actually calls.

A path with no entry in `DEFAULT_ROLE_ROUTES` is not merely ungated — the
middleware answers it 403 "path not allowed" before routing, so a missing
entry is an outage for whichever client needs that path, not a loosening.
These tests pin the two metadata endpoints a modern qiskit-iqm fetches while
constructing a backend, long before it submits anything.
"""

import pytest

from middleware.authorization import RoleAuthorizationChecker
from middleware.vendors.iqm.config import DEFAULT_ROLE_ROUTES


class FakeUser:
    def __init__(self, roles: list[str]):
        self.username = "someone"
        self.roles = roles


@pytest.fixture
def checker() -> RoleAuthorizationChecker:
    return RoleAuthorizationChecker(DEFAULT_ROLE_ROUTES)


ARCHITECTURE_ROUTES = [
    # Fetched by qiskit-iqm 15.x and later.
    "/api/v1/calibration/default/gates",
    # Fetched by clients paired with this generation of the machine API.
    "/api/v1/quantum-architecture",
]


@pytest.mark.parametrize("path", ARCHITECTURE_ROUTES)
def test_architecture_routes_are_configured(checker, path):
    """Unconfigured means 403 at the middleware, before any role check."""
    assert checker.is_route_configured(path, "GET")


@pytest.mark.parametrize("path", ARCHITECTURE_ROUTES)
def test_a_cortex_user_may_read_the_architecture(checker, path):
    assert checker.check(path, "GET", FakeUser(["cortex_user"]))


@pytest.mark.parametrize("path", ARCHITECTURE_ROUTES)
def test_the_architecture_is_not_open_to_anyone(checker, path):
    """Read-only metadata, but still behind the same role as the calibration set."""
    assert not checker.check(path, "GET", FakeUser([]))
    assert not checker.check(path, "GET", FakeUser(["pulla_user"]))


@pytest.mark.parametrize("path", ARCHITECTURE_ROUTES)
def test_the_architecture_is_read_only(checker, path):
    """No write method is configured, so the middleware refuses one outright."""
    assert not checker.is_route_configured(path, "POST")
