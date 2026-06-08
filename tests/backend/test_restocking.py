"""
Tests for restocking API endpoints.
"""
import pytest
from datetime import datetime

import main


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Reset submitted orders between tests.

    restock_orders is module-level in-memory state, so without this fixture
    orders created in one test would leak into the next within a pytest run.
    """
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()


class TestRestockingRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_get_recommendations_structure(self, client):
        """Test recommendations response structure and constraints."""
        response = client.get("/api/restocking/recommendations?budget=20000")
        assert response.status_code == 200

        data = response.json()
        assert data["budget"] == 20000
        assert isinstance(data["recommendations"], list)
        assert len(data["recommendations"]) > 0

        for rec in data["recommendations"]:
            assert "sku" in rec
            assert "name" in rec
            assert "category" in rec
            assert rec["trend"] in ["increasing", "stable", "decreasing"]
            assert isinstance(rec["gap"], int)
            assert rec["gap"] > 0
            assert rec["gap"] == rec["forecasted_demand"] - rec["quantity_on_hand"]
            assert abs(rec["line_total"] - rec["gap"] * rec["unit_cost"]) < 0.01

        assert data["total_cost"] <= data["budget"]
        assert abs(data["remaining_budget"] - (data["budget"] - data["total_cost"])) < 0.01

    def test_recommendation_skus_exist_in_inventory(self, client):
        """Test that every recommended SKU is a real inventory item."""
        inventory_skus = {item["sku"] for item in client.get("/api/inventory").json()}

        response = client.get("/api/restocking/recommendations?budget=1000000")
        for rec in response.json()["recommendations"]:
            assert rec["sku"] in inventory_skus

    def test_zero_budget_returns_empty(self, client):
        """Test that a zero budget yields no recommendations."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["recommendations"] == []
        assert data["total_cost"] == 0
        assert data["remaining_budget"] == 0

    def test_large_budget_includes_all_items(self, client):
        """Test that a huge budget includes every positive-gap item."""
        response = client.get("/api/restocking/recommendations?budget=1000000")
        assert response.status_code == 200

        data = response.json()
        assert len(data["recommendations"]) > 0
        assert data["skipped_unaffordable"] == 0

    def test_recommendations_ordering(self, client):
        """Test increasing-trend items come first, gaps descending within groups."""
        response = client.get("/api/restocking/recommendations?budget=1000000")
        recs = response.json()["recommendations"]

        # No increasing item may appear after a non-increasing one
        seen_non_increasing = False
        for rec in recs:
            if rec["trend"] != "increasing":
                seen_non_increasing = True
            else:
                assert not seen_non_increasing

        # Within each trend group, gaps are descending
        increasing = [r["gap"] for r in recs if r["trend"] == "increasing"]
        others = [r["gap"] for r in recs if r["trend"] != "increasing"]
        assert increasing == sorted(increasing, reverse=True)
        assert others == sorted(others, reverse=True)

    def test_mid_budget_skips_unaffordable(self, client):
        """Test that a constrained budget skips items but still fills with cheaper ones."""
        # Establish the full cost of all recommendations
        full = client.get("/api/restocking/recommendations?budget=1000000").json()
        full_cost = full["total_cost"]

        # A budget below the full cost must skip at least one item
        response = client.get(f"/api/restocking/recommendations?budget={full_cost / 2}")
        data = response.json()
        assert data["skipped_unaffordable"] > 0
        assert data["total_cost"] <= full_cost / 2

    def test_missing_budget_returns_422(self, client):
        """Test that a missing budget param is a validation error."""
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 422

    def test_negative_budget_returns_422(self, client):
        """Test that a negative budget is a validation error."""
        response = client.get("/api/restocking/recommendations?budget=-100")
        assert response.status_code == 422


class TestRestockOrders:
    """Test suite for POST/GET /api/restocking/orders."""

    def test_create_restock_order(self, client):
        """Test submitting a restocking order happy path."""
        # Sensors lead time 10, Actuators 21 -> consolidated order takes the max
        payload = {"items": [
            {"sku": "TMP-201", "quantity": 75},
            {"sku": "SRV-301", "quantity": 35}
        ]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert order["lead_time_days"] == 21
        assert len(order["items"]) == 2

        # Total recomputed server-side from inventory unit costs
        expected_total = 75 * 89.5 + 35 * 445.0
        assert abs(order["total_cost"] - expected_total) < 0.01

        # expected_delivery = submitted_date + lead_time_days
        submitted = datetime.fromisoformat(order["submitted_date"])
        delivery = datetime.fromisoformat(order["expected_delivery"])
        assert (delivery - submitted).days == order["lead_time_days"]

    def test_create_order_line_items_structure(self, client):
        """Test that order line items carry pricing and lead time."""
        payload = {"items": [{"sku": "MCU-401", "quantity": 300}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        item = response.json()["items"][0]
        assert item["sku"] == "MCU-401"
        assert item["category"] == "Controllers"
        assert item["quantity"] == 300
        assert abs(item["line_total"] - 300 * item["unit_cost"]) < 0.01
        assert item["lead_time_days"] == 14

    def test_create_order_empty_items_returns_400(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restocking/orders", json={"items": []})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_create_order_unknown_sku_returns_400(self, client):
        """Test that an unknown SKU is rejected."""
        payload = {"items": [{"sku": "FAKE-999", "quantity": 10}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        assert "FAKE-999" in response.json()["detail"]

    def test_create_order_zero_quantity_returns_400(self, client):
        """Test that a non-positive quantity is rejected."""
        payload = {"items": [{"sku": "TMP-201", "quantity": 0}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400

    def test_post_then_get_roundtrip(self, client):
        """Test that a submitted order appears in the orders list."""
        assert client.get("/api/restocking/orders").json() == []

        payload = {"items": [{"sku": "PCB-002", "quantity": 80}]}
        created = client.post("/api/restocking/orders", json=payload).json()

        orders = client.get("/api/restocking/orders").json()
        assert len(orders) == 1
        assert orders[0]["id"] == created["id"]
        assert orders[0]["order_number"] == "RST-0001"

    def test_order_numbers_increment(self, client):
        """Test that consecutive orders get sequential numbers."""
        payload = {"items": [{"sku": "PCB-002", "quantity": 10}]}
        first = client.post("/api/restocking/orders", json=payload).json()
        second = client.post("/api/restocking/orders", json=payload).json()
        assert first["order_number"] == "RST-0001"
        assert second["order_number"] == "RST-0002"
