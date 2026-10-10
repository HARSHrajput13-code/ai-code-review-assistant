"""Inventory bookkeeping for a small shop."""

from dataclasses import dataclass, field


@dataclass
class Inventory:
    """Item counts by SKU."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, sku: str, quantity: int) -> None:
        """Add stock; the quantity must be positive."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.counts[sku] = self.counts.get(sku, 0) + quantity

    def remove(self, sku: str, quantity: int) -> None:
        """Remove stock; fails if there is not enough."""
        available = self.counts.get(sku, 0)
        if quantity <= 0 or quantity > available:
            raise ValueError("invalid quantity")
        self.counts[sku] = available - quantity

    def total(self) -> int:
        """The number of items across all SKUs."""
        return sum(self.counts.values())
