from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from market_intelligence.models.products import Product


class ProductConnector:
    def __init__(self, fetch: Callable[[datetime], Iterable[Mapping[str, Any]]]) -> None:
        self.fetch = fetch

    def get_products(self, as_of: datetime) -> list[Product]:
        products = [Product.model_validate(row) for row in self.fetch(as_of)]
        skus = [product.sku for product in products]
        if not products or len(skus) != len(set(skus)):
            raise ValueError("Product connector returned no products or duplicate SKUs")
        return products
