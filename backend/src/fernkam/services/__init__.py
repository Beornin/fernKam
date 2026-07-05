"""Service layer: reusable business logic + query builders.

Routers should stay thin and delegate to these services so the same logic can
be reused across endpoints (e.g. /photos, /search, smart albums) and tested in
isolation.
"""
