"""
Printify API client — step 81-2c.

Base URL: https://api.printify.com/v1/
Auth: Bearer token (PRINTIFY_API_KEY)
User-Agent header required on every request.

Key endpoints used:
  GET  /v1/catalog/blueprints.json
  GET  /v1/catalog/blueprints/{bp}/print_providers.json
  GET  /v1/catalog/blueprints/{bp}/print_providers/{pp}/variants.json
  POST /v1/uploads/images.json          (file_name + contents in base64)
  POST /v1/shops/{shop_id}/products.json
  POST /v1/shops/{shop_id}/orders.json
  GET  /v1/shops/{shop_id}/orders/{order_id}.json

Shipping address fields (from Printify OpenAPI spec):
  address_to: {first_name, last_name, address1, address2, city, region,
               country, zip, email, phone, company}

Order status is in the `status` field; tracking lives in `shipments[]` with
  carrier (string) and number (tracking number string).
"""
import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings

PRINTIFY_API_BASE = "https://api.printify.com/v1"
_USER_AGENT = "AI-Factory/1.0"


class PrintifyClient:
    def __init__(self, api_key: Optional[str] = None, shop_id: Optional[str] = None):
        self._api_key = api_key or getattr(settings, "PRINTIFY_API_KEY", None) or os.getenv("PRINTIFY_API_KEY")
        self._shop_id = shop_id or getattr(settings, "PRINTIFY_SHOP_ID", None) or os.getenv("PRINTIFY_SHOP_ID")
        if not self._api_key:
            raise RuntimeError("PRINTIFY_API_KEY is not set")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        url = f"{PRINTIFY_API_BASE}{path}"
        from app.core.http_backoff import request_with_backoff_sync  # DEEP AUDIT V3 #12
        with httpx.Client(timeout=30) as client:
            resp = request_with_backoff_sync(client, "GET", url, headers=self._headers(), params=params)
        if resp.status_code >= 400:
            raise RuntimeError(f"Printify GET {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _post(self, path: str, payload: Dict) -> Any:
        url = f"{PRINTIFY_API_BASE}{path}"
        from app.core.http_backoff import request_with_backoff_sync  # DEEP AUDIT V3 #12
        with httpx.Client(timeout=30) as client:
            # POST retries only on 429 (never double-submit an order on a 5xx).
            resp = request_with_backoff_sync(client, "POST", url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Printify POST {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ── Catalog ──────────────────────────────────────────────────────────────

    def list_blueprints(self) -> List[Dict]:
        """Return the full Printify blueprint catalog (id + title at minimum)."""
        return self._get("/catalog/blueprints.json")

    def list_print_providers(self, blueprint_id: int) -> List[Dict]:
        return self._get(f"/catalog/blueprints/{blueprint_id}/print_providers.json")

    def list_variants(self, blueprint_id: int, print_provider_id: int) -> Dict:
        """Return variants response dict (contains 'variants' key with list)."""
        return self._get(
            f"/catalog/blueprints/{blueprint_id}/print_providers/{print_provider_id}/variants.json"
        )

    # ── Image upload ─────────────────────────────────────────────────────────

    def upload_image(self, image_path: str) -> str:
        """
        Upload a local image file to Printify.

        Uses base64 contents upload (POST /v1/uploads/images.json with
        file_name + contents). Returns the Printify image ID string.
        """
        path = Path(image_path)
        contents_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "file_name": path.name,
            "contents": contents_b64,
        }
        result = self._post("/uploads/images.json", payload)
        return str(result["id"])

    # ── Products ─────────────────────────────────────────────────────────────

    def create_product(
        self,
        blueprint_id: int,
        print_provider_id: int,
        variant_ids: List[int],
        image_id: str,
        title: str,
        description: str = "",
        price_cents: int = 2999,
    ) -> Dict:
        """
        Create a Printify product and return the full product dict (includes `id`).

        Builds a single front-facing print area covering all variants.
        price_cents is in USD cents (Printify stores prices as integers).
        """
        variants = [
            {"id": vid, "price": price_cents, "is_enabled": True}
            for vid in variant_ids
        ]
        print_areas = [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": "front",
                        "images": [
                            {
                                "id": image_id,
                                "x": 0.5,
                                "y": 0.5,
                                "scale": 1,
                                "angle": 0,
                            }
                        ],
                    }
                ],
            }
        ]
        payload = {
            "title": title,
            "description": description,
            "blueprint_id": blueprint_id,
            "print_provider_id": print_provider_id,
            "variants": variants,
            "print_areas": print_areas,
        }
        return self._post(f"/shops/{self._shop_id}/products.json", payload)

    def get_product(self, product_id: str) -> Dict:
        """
        Readback verification (step 91): re-fetch a just-created product
        from Printify rather than trusting create_product()'s response
        alone. Confirms the product really exists and really has the
        submitted image attached.
        """
        return self._get(f"/shops/{self._shop_id}/products/{product_id}.json")

    # ── Orders ───────────────────────────────────────────────────────────────

    def create_order(
        self,
        product_id: str,
        variant_id: int,
        quantity: int,
        shipping_address: Dict,
    ) -> str:
        """
        Submit a Printify order. Returns the Printify order ID string.

        shipping_address dict expected keys (from Etsy ShopReceipt fields):
          first_name, last_name, address1, address2, city, region,
          country, zip, email, phone
        These map directly to Printify's address_to fields.
        """
        payload = {
            "line_items": [
                {
                    "product_id": product_id,
                    "variant_id": variant_id,
                    "quantity": quantity,
                }
            ],
            "address_to": {
                "first_name": shipping_address.get("first_name", ""),
                "last_name": shipping_address.get("last_name", ""),
                "address1": shipping_address.get("address1", ""),
                "address2": shipping_address.get("address2", ""),
                "city": shipping_address.get("city", ""),
                "region": shipping_address.get("region", ""),
                "country": shipping_address.get("country", "US"),
                "zip": shipping_address.get("zip", ""),
                "email": shipping_address.get("email", ""),
                "phone": shipping_address.get("phone", ""),
            },
        }
        result = self._post(f"/shops/{self._shop_id}/orders.json", payload)
        return str(result["id"])

    def get_order_status(self, order_id: str) -> Dict:
        """
        Return the Printify order dict.

        Key fields:
          status      — e.g. "fulfilled" when shipped
          shipments   — list of {carrier, number, url, delivered_at}
        """
        return self._get(f"/shops/{self._shop_id}/orders/{order_id}.json")
