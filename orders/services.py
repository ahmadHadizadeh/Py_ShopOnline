# orders/services.py
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderAddressSnapshot, OrderItem, Payment


class OrderService:
    @staticmethod
    @transaction.atomic
    def transition_status(*, order_id, new_status):
        """Apply only safe operational order transitions used by backoffice."""
        order = Order.objects.select_for_update().get(pk=order_id)

        allowed_transitions = {
            Order.Status.PAID: {Order.Status.PROCESSING},
            Order.Status.PROCESSING: {Order.Status.COMPLETED},
            Order.Status.PENDING: {Order.Status.CANCELLED},
            Order.Status.PLACED: {Order.Status.CANCELLED},
        }
        if new_status not in allowed_transitions.get(order.status, set()):
            raise ValidationError(
                f"تغییر وضعیت سفارش از {order.get_status_display()} به وضعیت انتخاب‌شده مجاز نیست."
            )

        now = timezone.now()
        order.status = new_status
        update_fields = ["status", "updated"]

        if new_status == Order.Status.CANCELLED:
            order.cancelled_at = now
            update_fields.append("cancelled_at")
        elif new_status == Order.Status.COMPLETED:
            order.completed_at = now
            update_fields.append("completed_at")

        order.save(update_fields=update_fields)
        return order

    @staticmethod
    def calculate_order_totals(cart, shipping_method=None):
        """محاسبه دقیق مبالغ سفارش بر اساس آیتم‌های فعال سبد و روش ارسال."""
        subtotal = Decimal(str(cart.total_price))
        discount = Decimal(str(cart.discount_amount))

        shipping_amount = Decimal("0")
        if shipping_method:
            shipping_amount = Decimal(
                str(shipping_method.calculate_shipping_cost(subtotal))
            )

        final_amount = (subtotal - discount) + shipping_amount
        if final_amount < 0:
            final_amount = Decimal("0")

        return {
            "subtotal": subtotal,
            "discount": discount,
            "shipping": shipping_amount,
            "final_amount": final_amount,
        }

    @staticmethod
    @transaction.atomic
    def create_order(user, cart, shipping_address, shipping_method, customer_note=""):
        """ایجاد اتمیک سفارش از آیتم‌های فعال سبد، بدون حذف تاریخچه سبد."""
        if not user or not getattr(user, "is_authenticated", False):
            raise ValidationError("برای ثبت سفارش باید وارد حساب کاربری شوید.")

        if cart.user_id != user.id or cart.status != cart.STATUS_ACTIVE:
            raise ValidationError("سبد خرید معتبر نیست.")

        active_items = list(
            cart.items.select_for_update()
            .select_related("product", "variant")
            .filter(status="active")
        )
        if not active_items:
            raise ValidationError("سبد خرید فعال فاقد آیتم قابل سفارش است.")

        totals = OrderService.calculate_order_totals(cart, shipping_method)

        order = Order.objects.create(
            user=user,
            cart=cart,
            order_number=Order.generate_order_number(),
            status=Order.Status.PENDING,
            shipping_method=shipping_method,
            subtotal_amount=totals["subtotal"],
            discount_amount=totals["discount"],
            shipping_amount=totals["shipping"],
            final_amount=totals["final_amount"],
            customer_note=customer_note,
        )

        OrderAddressSnapshot.objects.create(
            order=order,
            recipient_name=shipping_address.recipient_name,
            recipient_mobile=shipping_address.phone_number,
            postal_code=shipping_address.postal_code,
            province=shipping_address.province,
            city=shipping_address.city,
            address_line=shipping_address.address_line,
        )

        order_items = [
            OrderItem(
                order=order,
                product=item.product,
                variant_id=item.variant_id,
                product_name=item.product.name,
                variant_name=(
                    f"{item.variant.name}: {item.variant.value}"
                    if item.variant_id
                    else ""
                ),
                sku=getattr(item.product, "sku", ""),
                quantity=item.quantity,
                unit_price=item.unit_price_snapshot,
                subtotal_price=item.subtotal,
            )
            for item in active_items
        ]
        OrderItem.objects.bulk_create(order_items)

        Payment.objects.create(
            order=order,
            user=user,
            amount=order.final_amount,
            status=Payment.Status.PENDING,
        )

        cart.status = cart.STATUS_ORDERED
        cart.save(update_fields=["status", "updated"])

        return order
