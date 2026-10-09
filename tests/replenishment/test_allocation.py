from __future__ import annotations

from market_intelligence.replenishment.allocation import (
    allocate_product_requirement,
    summarize_dc_allocations,
)


def _dc(dc_id: str, name: str, atp: float, eta: float, eligible: bool = True) -> dict:
    return {
        "dc_id": dc_id,
        "dc_name": name,
        "atp": atp,
        "eta_hours": eta,
        "eligible": eligible,
    }


def test_products_select_different_backups_from_product_inventory() -> None:
    """Catches a Store-wide backup DC being reused for every product."""
    primary_a = _dc("TX1", "Dallas DC", 10, 2)
    primary_b = _dc("TX1", "Dallas DC", 5, 2)

    product_a = allocate_product_requirement(
        store_id="108",
        sku="A",
        required_quantity=60,
        primary=primary_a,
        alternates=[
            _dc("TX2", "Fort Worth DC", 60, 4),
            _dc("TX3", "Austin DC", 100, 7),
        ],
    )
    product_b = allocate_product_requirement(
        store_id="108",
        sku="B",
        required_quantity=70,
        primary=primary_b,
        alternates=[
            _dc("TX2", "Fort Worth DC", 20, 4),
            _dc("TX3", "Austin DC", 70, 7),
        ],
    )

    assert product_a.backup_dc_id == "TX2"
    assert product_b.backup_dc_id == "TX3"
    assert product_a.primary_supply == 0
    assert product_a.backup_supply == 60
    assert product_b.primary_supply == 0
    assert product_b.backup_supply == 70


def test_no_split_when_neither_dc_can_individually_cover_requirement() -> None:
    """Current policy (Sep 29 review reinforcement, allocation.py:105-119): if neither
    the primary DC nor any single alternate can cover the full requirement, do NOT
    split partial quantities across them. The full requirement remains as Remaining
    Gap for planner escalation. Replaces a stale Sep-24-review expectation that a
    40/60 split was allowed as a last resort -- that fallback was deliberately
    removed; the code's own comment at allocation.py:105-109 documents this.
    """
    allocation = allocate_product_requirement(
        store_id="108",
        sku="FLASHLIGHT",
        required_quantity=100,
        primary=_dc("TX1", "Dallas DC", 40, 2),
        alternates=[_dc("TX2", "Fort Worth DC", 70, 4)],
    )

    assert allocation.primary_supply == 0
    assert allocation.backup_supply == 0
    assert allocation.remaining_gap == 100
    assert "no split shipment" in allocation.allocation_reason
    assert "escalate" in allocation.allocation_reason


def test_ineligible_dc_is_not_allocated_and_residual_is_explicit() -> None:
    """Catches inventory being allocated from a DC that cannot arrive in time."""
    allocation = allocate_product_requirement(
        store_id="108",
        sku="WATER",
        required_quantity=50,
        primary=_dc("TX1", "Dallas DC", 20, 2, eligible=False),
        alternates=[_dc("TX2", "Fort Worth DC", 80, 4, eligible=False)],
    )

    assert allocation.primary_supply == 0
    assert allocation.backup_supply == 0
    assert allocation.remaining_gap == 50


def test_summary_groups_actual_product_dc_allocations() -> None:
    """Catches recommendation text that names only the first product's DC."""
    rows = [
        allocate_product_requirement(
            store_id="108",
            sku="A",
            required_quantity=60,
            primary=_dc("TX1", "Dallas DC", 0, 2),
            alternates=[_dc("TX2", "Fort Worth DC", 60, 4)],
        ),
        allocate_product_requirement(
            store_id="108",
            sku="B",
            required_quantity=70,
            primary=_dc("TX1", "Dallas DC", 0, 2),
            alternates=[_dc("TX3", "Austin DC", 70, 7)],
        ),
    ]

    summary = summarize_dc_allocations(rows)

    assert summary == {
        "Fort Worth DC": 60.0,
        "Austin DC": 70.0,
    }


def test_primary_complete_and_partial_single_source_paths() -> None:
    """Current policy: partial ATP (primary or the sole alternate) never earns partial
    credit -- the full gap remains for escalation rather than shipping what's available.
    """
    complete = allocate_product_requirement(
        store_id="108",
        sku="A",
        required_quantity=20,
        primary=_dc("TX1", "Dallas DC", 20, 2),
        alternates=[],
    )
    partial = allocate_product_requirement(
        store_id="108",
        sku="B",
        required_quantity=20,
        primary=_dc("TX1", "Dallas DC", 10, 2),
        alternates=[],
    )
    backup_only = allocate_product_requirement(
        store_id="108",
        sku="C",
        required_quantity=20,
        primary=_dc("TX1", "Dallas DC", 0, 2),
        alternates=[_dc("TX2", "Fort Worth DC", 5, 4)],
    )
    invalid = allocate_product_requirement(
        store_id="108",
        sku="D",
        required_quantity="bad",  # type: ignore[arg-type]
        primary=_dc("TX1", "Dallas DC", 0, 2),
        alternates=[],
    )

    assert complete.primary_supply == 20
    assert complete.remaining_gap == 0

    # Primary has partial ATP only (10 of 20): no partial credit, full gap remains.
    assert partial.primary_supply == 0
    assert partial.backup_supply == 0
    assert partial.remaining_gap == 20
    assert partial.allocation_reason.startswith("Primary DC has partial ATP only")

    # The sole alternate has partial ATP only (5 of 20): no partial credit, full gap remains.
    assert backup_only.primary_supply == 0
    assert backup_only.backup_supply == 0
    assert backup_only.remaining_gap == 20
    assert backup_only.allocation_reason.startswith("Alternate DC has partial ATP only")

    assert invalid.required_quantity == 0
