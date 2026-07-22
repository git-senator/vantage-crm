"""Job functions.

Every job here follows the same three steps: resolve the tenant, bind it, then
call a service. None of them reimplement business logic — see the package
docstring in `app/workers/__init__.py` for why that rule exists and what it
costs to break it.
"""
