"""Operating costs and runway.

The agent is allowed to *know* it is running out of money. It is not allowed to
*act differently* because of it. Nothing in this package is an input to sizing,
to the edge threshold, or to the policy guardian — see
``tests/test_survival.py::test_accrued_operating_cost_does_not_change_sizing``.
"""

from ai_trader.costs.ledger import (
    CostCategory,
    CostLedger,
    TokenPrice,
    XAI_PRICES,
)

__all__ = ["CostCategory", "CostLedger", "TokenPrice", "XAI_PRICES"]
