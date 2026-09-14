# Py_ShopOnline — Architecture Lock

## Scope
This lock completes the existing ProductVariant → CartItem → OrderItem path using the current architecture. It does not introduce SKU or per-variant inventory because those fields do not exist in the current ProductVariant model.

## Locked contracts
- `ProductVariant` remains an option entity with `product`, `name`, `value`, and `price_adjustment`.
- `CartItem.variant` stores the selected variant and is nullable for products that have no variants.
- A product that has variants must receive a valid variant at add-to-cart time.
- Cart uniqueness: `(cart, product)` when `variant IS NULL`; `(cart, product, variant)` when `variant IS NOT NULL`.
- `CartItem.unit_price_snapshot` is the cart price authority for order creation.
- The current `OrderItem.variant_id` and `variant_name` fields remain historical snapshots; no new FK is introduced in this pass.
- Order creation consumes only `active` cart items.
- The source cart is marked `ordered` after successful order/payment initialization; cart rows are not deleted.
- Existing public cart service functions remain backward-compatible; ID-based helpers are used by current item endpoints.
