from abc import ABC, abstractmethod
from typing import Optional


class BaseBroker(ABC):

    @abstractmethod
    def place_order(self, order_params: dict) -> dict:
        """
        Place an order with the broker.

        Input keys: trade_id, symbol, action, quantity, order_type,
                    price, stop_loss, target
        Output keys: order_id, broker_order_id, status, failure_reason
        """
        ...

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> dict:
        """
        Query fill status.

        Output keys: broker_order_id, status, filled_quantity,
                     filled_price, failure_reason
        """
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        ...

    @abstractmethod
    def get_ltp(self, symbol: str) -> Optional[float]:
        """Return last traded price for symbol, or None on failure."""
        ...

    @abstractmethod
    def get_portfolio(self) -> dict:
        """
        Return live holdings and positions from the broker.

        Output keys: holdings (list), positions (list), error (str | None)
        """
        ...
