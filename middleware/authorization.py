"""Role-based authorization logic (generic).

Public classes:
- ``RoleAuthorizationChecker``: generic RBAC for any vendor's routes.

Site-specific job authorization (portal / billing / accounting checks)
lives in the site plugin (e.g. ``middleware.sites.spark.authorization``).
"""

import logging

from middleware.authentication import User

logger = logging.getLogger(__name__)


class RoleAuthorizationChecker:
    """Checks if a user has required roles to access an endpoint.

    This class encapsulates role-based access control logic that was previously
    embedded in the middleware. It can be used independently in different contexts.

    Usage:
        checker = RoleAuthorizationChecker(role_routes)
        is_allowed = checker.check(path, method, user)
    """

    def __init__(self, role_routes: dict[tuple[str, str], list[str]]):
        """Initialize with role route configuration.

        Args:
            role_routes: Dict mapping (path, method) tuples to lists of allowed roles.
                        Method can be '*' to match all methods. Path is matched with startswith.
        """
        self.role_routes = role_routes

    def _path_matches(self, route_pattern: str, actual_path: str) -> bool:
        """Match a ROLE_ROUTES pattern against an actual request path.

        - If the configured `route_pattern` contains path parameters in the
          form `{name}` they match exactly one path segment (no slashes).
        - Otherwise fall back to the previous `startswith` behaviour so
          existing prefix-style rules continue to work.
        Examples:
          '/api/v1/jobs/{job_id}/cancel' matches '/api/v1/jobs/123/cancel'
          '/api/v1/jobs' matches '/api/v1/jobs/123/cancel' (prefix)
        """
        # Normalize trailing slashes for comparison
        rp = route_pattern.rstrip("/")
        ap = actual_path.rstrip("/")

        # Parameterized pattern: compare segment-by-segment
        if "{" in rp and "}" in rp:
            rp_segs = rp.strip("/").split("/") if rp.strip("/") else []
            ap_segs = ap.strip("/").split("/") if ap.strip("/") else []
            if len(rp_segs) != len(ap_segs):
                return False
            for rseg, aseg in zip(rp_segs, ap_segs, strict=False):
                if rseg.startswith("{") and rseg.endswith("}"):
                    # matches any non-empty single segment
                    if aseg == "":
                        return False
                    continue
                if rseg != aseg:
                    return False
            return True

        # Fallback: keep prefix matching for backwards compatibility
        return ap.startswith(rp)

    def check(self, path: str, method: str, user: User) -> bool:
        """Check if user has required roles for the given path and method.

        This supports parameterized ROLE_ROUTES (e.g. containing `{param}`)
        and preserves previous prefix-style matching for non-parameter routes.
        """
        user_roles = getattr(user, "roles", [])

        # Check each role route
        for (role_path, role_method), allowed_roles in self.role_routes.items():
            if self._path_matches(role_path, path) and (role_method in (method, "*")):
                # Found a matching route, check if user has required role
                if not any(role in user_roles for role in allowed_roles):
                    logger.info(
                        f"User {getattr(user, 'username', None)} lacks required roles {allowed_roles} for {method} {path}"
                    )
                    return False
                logger.info(
                    f"User {getattr(user, 'username', None)} has required roles for {method} {path}"
                )
                return True

        # No role restriction applies, allow
        return True

    def is_route_configured(self, path: str, method: str) -> bool:
        """Return True if the path+method matches any configured ROLE_ROUTES entry.

        Uses the same matching semantics as `check()` so parameterized routes are
        recognised by the middleware whitelist.
        """
        for role_path, role_method in self.role_routes:
            if self._path_matches(role_path, path) and (role_method in ("*", method)):
                return True
        return False


