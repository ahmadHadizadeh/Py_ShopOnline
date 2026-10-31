from decimal import Decimal
import hashlib
import hmac
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.signals import post_save
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from accounts.models.profile import Profile
from accounts.signals import create_user_profile
from cart.models import Cart, CartItem
from catalog.models.category import Category
from catalog.models.product import Product
from catalog.models.variant import ProductVariant
from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.order_address_snapshot import OrderAddressSnapshot
from orders.models.payment import Payment
from orders.models.shipping import ShippingMethod
from orders.payment.services import PaymentService
from orders.services import OrderService

User = get_user_model()


@override_settings(DEBUG=True)
class PaymentCallbackSecurityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user1 = User.objects.create_user(
            username="user_test_1",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user1,
            phone_number="09110000001",
        )

        self.user2 = User.objects.create_user(
            username="user_test_2",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user2,
            phone_number="09110000002",
        )

        self.client1 = Client()
        self.client1.force_login(self.user1)

        self.client2 = Client()
        self.client2.force_login(self.user2)

        self.category = Category.objects.create(
            name="لپ تاپ",
            slug="laptop",
        )

        self.product = Product.objects.create(
            category=self.category,
            name="Lenovo Legion 5",
            slug="lenovo-legion-5",
            price=Decimal("50000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user1,
            status=Cart.STATUS_ORDERED,
        )

        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=1,
            unit_price_snapshot=Decimal("50000000"),
            status=CartItem.STATUS_ACTIVE,
        )

        self.order = Order.objects.create(
            user=self.user1,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant_id=None,
            product_name=self.product.name,
            variant_name="",
            sku=getattr(self.product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )

        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user1,
            amount=Decimal("50000000"),
            status=Payment.Status.PENDING,
            transaction_code="TRX-TEST-VALID-001",
            gateway_name="mock_gateway",
        )

        self.callback_url = reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "mock_gateway"},
        )

    def callback_signature(self):
        payload = (f"{self.payment.transaction_code}:" f"{self.payment.amount}").encode(
            "utf-8"
        )
        return hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

    def test_callback_ownership_forbidden_for_other_user(self):
        response = self.client2.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_amount_mismatch_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "1000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_invalid_amount_format_returns_bad_request(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "invalid_number_format",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 400)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.PENDING,
        )

    def test_callback_success_flow_preserves_cart(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.cart.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertIsNotNone(
            self.payment.reference_code,
        )

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

        self.assertTrue(
            CartItem.objects.filter(
                cart_id=self.cart.pk,
            ).exists()
        )

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

    def test_callback_failed_flow_updates_payment_status(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "failed",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_failed",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()
        self.cart.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.FAILED,
        )

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

    def test_failed_payment_keeps_order_retryable(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "failed",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_failed",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIsNone(self.order.cancelled_at)

    def test_cancelled_order_cannot_be_paid(self):
        original_transaction_code = self.payment.transaction_code

        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            PaymentService.initiate(
                user=self.user1,
                order_number=self.order.order_number,
            )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(
            self.payment.transaction_code,
            original_transaction_code,
        )
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_callback_idempotency_on_already_successful_payment(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-EXISTING-123"
        self.payment.save(
            update_fields=[
                "status",
                "reference_code",
            ]
        )

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        expected_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

        self.assertRedirects(response, expected_url)

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertEqual(
            self.payment.reference_code,
            "REF-EXISTING-123",
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertIn("بررسی مالی", self.order.admin_note)

        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_unverified_duplicate_callback_cannot_quarantine_successful_payment_order(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-EXISTING-124"
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": "invalid-signature",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIsNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)

    def test_successful_payment_keeps_order_cart_relationship(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.cart.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

        self.assertEqual(
            self.order.cart_id,
            self.cart.pk,
        )

        self.assertEqual(
            self.cart.status,
            Cart.STATUS_ORDERED,
        )

        self.assertTrue(
            CartItem.objects.filter(
                cart_id=self.cart.pk,
            ).exists()
        )

    def test_success_callback_does_not_change_payment_to_failed(self):
        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.payment.refresh_from_db()

        self.assertNotEqual(
            self.payment.status,
            Payment.Status.FAILED,
        )

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )

    def test_successful_payment_reduces_stock_and_marks_order_paid(self):
        initial_stock = self.product.stock
        initial_product_updated = self.product.updated
        initial_order_updated = self.order.updated
        initial_payment_updated = self.payment.updated

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.product.refresh_from_db()
        self.order.refresh_from_db()
        self.payment.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            initial_stock - self.order_item.quantity,
        )

        self.assertTrue(self.order.stock_reduced)

        self.assertEqual(
            self.order.status,
            Order.Status.PAID,
        )

        self.assertEqual(
            self.payment.status,
            Payment.Status.SUCCESS,
        )
        self.assertGreater(self.payment.updated, initial_payment_updated)
        self.assertGreater(self.order.updated, initial_order_updated)
        self.assertGreater(self.product.updated, initial_product_updated)

        self.assertIsNotNone(self.order.paid_at)

    def test_successful_callback_with_insufficient_stock_redirects_to_reconciliation_receipt(self):
        OrderAddressSnapshot.objects.create(
            order=self.order,
            recipient_name="مشتری آزمایشی",
            recipient_mobile="09120000000",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="نشانی آزمایشی",
        )
        Product.objects.filter(pk=self.product.pk).update(
            stock=0,
            is_available_status=False,
        )

        response = self.client1.post(
            self.callback_url,
            {
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": "50000000",
                "signature": self.callback_signature(),
            },
        )
        receipt_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )
        self.assertRedirects(response, receipt_url)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertTrue(self.payment.reference_code)
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, 0)
        self.assertIn("بازپرداخت", self.order.admin_note)

        receipt_response = self.client1.get(receipt_url)
        self.assertEqual(receipt_response.status_code, 200)
        self.assertContains(
            receipt_response,
            "پرداخت در درگاه موفق بوده، اما سفارش در وضعیت لغوشده قرار دارد.",
        )
        self.assertNotContains(
            receipt_response,
            "سفارش شما با موفقیت ثبت شد و در مرحله پردازش قرار گرفت.",
        )

    def test_zarinpal_callback_persists_only_real_provider_reference(self):
        from orders.payment.gateways import (
            GatewayCallbackResult,
            PaymentGatewayRegistry,
        )

        authority = "A000000000000000000000000000001234"
        self.payment.gateway_name = "zarinpal"
        self.payment.transaction_code = authority
        self.payment.save(update_fields=["gateway_name", "transaction_code"])
        gateway = PaymentGatewayRegistry.get("zarinpal")
        verified_result = GatewayCallbackResult(
            transaction_id=authority,
            success=True,
            amount=Decimal("50000000"),
            reference_id="987654321",
            raw_response={"code": 100, "ref_id": "987654321"},
        )
        callback_url = reverse(
            "orders:gateway_payment_callback",
            kwargs={"gateway_name": "zarinpal"},
        )

        with patch.object(gateway, "verify_callback", return_value=verified_result):
            response = self.client1.get(
                callback_url,
                {"Authority": authority, "Status": "OK"},
            )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.payment.reference_code, "987654321")

    def test_zarinpal_callback_without_real_reference_does_not_mark_payment_success(
        self,
    ):
        from orders.payment.gateways import (
            GatewayCallbackResult,
            GatewayVerificationError,
            PaymentGatewayRegistry,
        )

        authority = "A000000000000000000000000000001235"
        self.payment.gateway_name = "zarinpal"
        self.payment.transaction_code = authority
        self.payment.save(update_fields=["gateway_name", "transaction_code"])
        gateway = PaymentGatewayRegistry.get("zarinpal")
        unreferenced_result = GatewayCallbackResult(
            transaction_id=authority,
            success=True,
            amount=Decimal("50000000"),
            reference_id=None,
            raw_response={"code": 100},
        )

        with patch.object(
            gateway,
            "verify_callback",
            return_value=unreferenced_result,
        ):
            with self.assertRaises(GatewayVerificationError):
                PaymentService.handle_callback(
                    gateway_name="zarinpal",
                    data={"Authority": authority, "Status": "OK"},
                )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertIsNone(self.payment.reference_code)

    def test_reduce_item_stock_does_nothing_for_cancelled_order(self):
        OrderService.transition_status(
            order_id=self.order.pk,
            new_status=Order.Status.CANCELLED,
        )

        initial_stock = self.product.stock

        # Keep the original in-memory order stale: the database transition above
        # must still prevent stock reduction when reduce_item_stock() reloads it.
        self.assertNotEqual(self.order.status, Order.Status.CANCELLED)
        self.order.reduce_item_stock()

        self.order.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, initial_stock)


@override_settings(PAYMENT_DEFAULT_GATEWAY="mock_gateway")
class PaymentServiceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-service-user",
            password="Password123!",
        )
        Profile.objects.create(
            user=self.user,
            phone_number="09110000004",
        )

        self.category = Category.objects.create(
            name="Payment Service",
            slug="payment-service-test",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Payment Service Product",
            slug="payment-service-product",
            price=Decimal("50000000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )
        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )
        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant_id=None,
            product_name=self.product.name,
            variant_name="",
            sku=getattr(self.product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )

    def test_reduce_item_stock_locks_multiple_products_in_primary_key_order(self):
        from unittest.mock import patch

        self.order.items.all().delete()
        second_product = Product.objects.create(
            category=self.category,
            name="Payment Service Second Product",
            slug="payment-service-second-product",
            price=Decimal("100000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )
        # Deliberately create order items in reverse product-PK order.
        for product in (second_product, self.product):
            OrderItem.objects.create(
                order=self.order,
                product=product,
                variant_id=None,
                product_name=product.name,
                variant_name="",
                sku=getattr(product, "sku", None),
                quantity=1,
                unit_price=Decimal("100000"),
                subtotal_price=Decimal("100000"),
            )

        locked_product_ids = []
        manager = Product.objects
        original_select_for_update = manager.select_for_update

        class TrackingQuerySet:
            def __init__(self, queryset):
                self.queryset = queryset

            def get(self, *args, **kwargs):
                locked_product_ids.append(kwargs["pk"])
                return self.queryset.get(*args, **kwargs)

        def track_product_locks(*args, **kwargs):
            return TrackingQuerySet(original_select_for_update(*args, **kwargs))

        with patch.object(
            manager,
            "select_for_update",
            side_effect=track_product_locks,
        ):
            self.order.reduce_item_stock()

        self.assertEqual(
            locked_product_ids,
            sorted([self.product.pk, second_product.pk]),
        )
        self.product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(second_product.stock, 9)

    def test_service_uses_registered_mock_gateway(self):
        payment, order, initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertIsNotNone(payment)
        self.assertEqual(order.pk, self.order.pk)
        self.assertEqual(payment.gateway_name, "mock_gateway")
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.transaction_code, initiation.transaction_id)
        self.assertIn("mock-payment-gateway", initiation.redirect_url)

    def test_failed_payment_can_be_retried_without_cancelling_order(self):
        payment, order, first_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status"])

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

        retry_payment, retry_order, retry_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertEqual(retry_payment.pk, payment.pk)
        self.assertEqual(retry_order.pk, order.pk)
        self.assertEqual(retry_payment.status, Payment.Status.PENDING)
        self.assertNotEqual(
            retry_initiation.transaction_id,
            first_initiation.transaction_id,
        )

    def test_successful_payment_on_pending_order_is_quarantined_without_second_charge(self):
        from orders.payment.gateways import PaymentGatewayRegistry

        payment, _, _ = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=["status", "updated"])
        gateway = PaymentGatewayRegistry.get("mock_gateway")

        with patch.object(gateway, "initiate") as mocked_initiate:
            result_payment, result_order, initiation = PaymentService.initiate(
                user=self.user,
                order_number=self.order.order_number,
            )

        self.assertIsNone(result_payment)
        self.assertIsNone(initiation)
        self.assertEqual(result_order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(result_order.cancelled_at)
        result_order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(result_order.status, Order.Status.CANCELLED)
        self.assertIn("بررسی مالی", result_order.admin_note)
        mocked_initiate.assert_not_called()

    def test_placed_order_timestamp_updates_when_payment_is_initiated(self):
        self.order.status = Order.Status.PLACED
        self.order.save(update_fields=["status", "updated"])
        self.order.refresh_from_db()
        initial_updated = self.order.updated

        PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertGreater(self.order.updated, initial_updated)

    def test_service_reuses_existing_one_to_one_payment(self):
        first_payment, _, _ = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )
        first_pk = first_payment.pk

        second_payment, _, _ = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

        self.assertEqual(second_payment.pk, first_pk)
        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )
        self.assertEqual(second_payment.status, Payment.Status.PENDING)

    def test_repeated_pending_initiation_reuses_existing_gateway_transaction(self):
        from orders.payment.gateways import PaymentGatewayRegistry

        first_payment, _, first_initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )
        gateway = PaymentGatewayRegistry.get("mock_gateway")

        with patch.object(
            gateway, "initiate", wraps=gateway.initiate
        ) as mocked_initiate:
            second_payment, _, second_initiation = PaymentService.initiate(
                user=self.user,
                order_number=self.order.order_number,
            )

        self.assertEqual(first_payment.pk, second_payment.pk)
        self.assertEqual(
            first_initiation.transaction_id, second_initiation.transaction_id
        )
        self.assertEqual(first_initiation.redirect_url, second_initiation.redirect_url)
        mocked_initiate.assert_not_called()


class PaymentCallbackServiceTests(PaymentServiceTests):
    def setUp(self):
        super().setUp()
        self.payment, self.order, self.initiation = PaymentService.initiate(
            user=self.user,
            order_number=self.order.order_number,
        )

    def test_zarinpal_success_without_reference_id_is_rejected(self):
        from unittest.mock import patch
        from orders.payment.gateways import (
            GatewayCallbackResult,
            GatewayVerificationError,
            PaymentGatewayRegistry,
        )

        payment = self.payment
        payment.gateway_name = "zarinpal"
        payment.save(update_fields=["gateway_name"])
        transaction_id = payment.transaction_code

        class MissingReferenceGateway:
            name = "zarinpal"

            def extract_transaction_id(self, *, data):
                return transaction_id

            def verify_callback(self, *, payment, data):
                return GatewayCallbackResult(
                    transaction_id=transaction_id,
                    success=True,
                    amount=payment.amount,
                    reference_id=current_reference,
                    raw_response={"code": 100},
                )

        gateway = MissingReferenceGateway()

        for current_reference in (None, "", "   "):
            with self.subTest(reference_id=current_reference):
                with patch.object(PaymentGatewayRegistry, "get", return_value=gateway):
                    with self.assertRaises(GatewayVerificationError):
                        PaymentService.handle_callback(
                            gateway_name="zarinpal",
                            data={"Authority": "TEST-AUTHORITY", "Status": "OK"},
                        )

                payment.refresh_from_db()
                payment.order.refresh_from_db()
                self.assertEqual(payment.status, Payment.Status.PENDING)
                self.assertEqual(payment.order.status, Order.Status.PENDING)
                self.assertFalse(payment.reference_code)

    def test_service_handles_mock_callback(self):
        from orders.payment.gateways import PaymentGatewayRegistry

        gateway = PaymentGatewayRegistry.get("mock_gateway")
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.assertTrue(outcome.success)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(gateway.name, self.payment.gateway_name)

    def test_callback_acquires_order_lock_before_payment_lock(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        lock_sequence = []
        order_manager = Order.objects
        payment_manager = Payment.objects
        original_order_select_for_update = order_manager.select_for_update
        original_payment_select_for_update = payment_manager.select_for_update

        def lock_order(*args, **kwargs):
            lock_sequence.append("order")
            return original_order_select_for_update(*args, **kwargs)

        def lock_payment(*args, **kwargs):
            lock_sequence.append("payment")
            return original_payment_select_for_update(*args, **kwargs)

        with (
            patch.object(order_manager, "select_for_update", side_effect=lock_order),
            patch.object(payment_manager, "select_for_update", side_effect=lock_payment),
        ):
            outcome = PaymentService.handle_callback(
                gateway_name="mock_gateway",
                data={
                    "trxid": self.payment.transaction_code,
                    "status": "success",
                    "amount": str(self.payment.amount),
                    "signature": signature,
                },
            )

        self.assertTrue(outcome.success)
        self.assertEqual(lock_sequence[:2], ["order", "payment"])

    def test_service_callback_is_idempotent(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        first = PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.payment.refresh_from_db()
        reference = self.payment.reference_code

        second = PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.payment.refresh_from_db()

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(reference, self.payment.reference_code)

    def test_service_rejects_wrong_gateway_for_payment(self):
        with self.assertRaises(ValidationError):
            PaymentService.handle_callback(
                gateway_name="unknown_gateway",
                data={
                    "trxid": self.payment.transaction_code,
                    "status": "success",
                    "amount": str(self.payment.amount),
                    "signature": "invalid",
                },
            )

    def test_repeated_success_callback_does_not_reduce_stock_twice(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        initial_stock = self.product.stock
        PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.product.refresh_from_db()
        first_stock = self.product.stock

        PaymentService.handle_callback(gateway_name="mock_gateway", data=data)
        self.product.refresh_from_db()

        self.assertEqual(first_stock, initial_stock - 1)
        self.assertEqual(self.product.stock, first_stock)

    def test_successful_payment_with_insufficient_stock_is_recorded_for_reconciliation(self):
        # Inventory can change after checkout but before the provider callback.
        Product.objects.filter(pk=self.product.pk).update(
            stock=0,
            is_available_status=False,
        )
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()

        self.assertTrue(outcome.success)
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertTrue(self.payment.reference_code)
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, 0)
        self.assertIn("بازپرداخت", self.order.admin_note)
        self.assertIn('"status": "success"', self.payment.gateway_response)

    def test_success_callback_for_cancelled_order_records_payment_without_fulfillment(
        self,
    ):
        OrderService.transition_status(
            order_id=self.order.pk,
            new_status=Order.Status.CANCELLED,
        )
        initial_stock = self.product.stock
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()

        self.assertTrue(outcome.success)
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertTrue(self.payment.reference_code)
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, initial_stock)

    def test_success_for_order_without_items_is_recorded_for_reconciliation(self):
        OrderItem.objects.filter(order=self.order).delete()
        initial_stock = self.product.stock
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(outcome.success)
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, initial_stock)
        self.assertIn("بازپرداخت", self.order.admin_note)

    def test_legacy_empty_order_with_stale_stock_flag_is_reconciled(self):
        OrderItem.objects.filter(order=self.order).delete()
        Order.objects.filter(pk=self.order.pk).update(stock_reduced=True)
        initial_stock = self.product.stock
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        outcome = PaymentService.handle_callback(
            gateway_name="mock_gateway",
            data={
                "trxid": self.payment.transaction_code,
                "status": "success",
                "amount": str(self.payment.amount),
                "signature": signature,
            },
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(outcome.success)
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)
        self.assertFalse(self.order.stock_reduced)
        self.assertIsNone(self.order.paid_at)
        self.assertEqual(self.product.stock, initial_stock)
        self.assertIn("بازپرداخت", self.order.admin_note)

    def test_callback_db_failure_rolls_back_payment_transition(self):
        signature = hmac.new(
            settings.PAYMENT_CALLBACK_SECRET.encode("utf-8"),
            f"{self.payment.transaction_code}:{self.payment.amount}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        data = {
            "trxid": self.payment.transaction_code,
            "status": "success",
            "amount": str(self.payment.amount),
            "signature": signature,
        }

        def mutate_then_fail(*args, **kwargs):
            self.payment.status = Payment.Status.SUCCESS
            self.payment.save(update_fields=["status"])
            raise RuntimeError("simulated payment transition failure")

        with patch.object(
            Payment,
            "update_status_and_order",
            side_effect=mutate_then_fail,
        ):
            with self.assertRaises(RuntimeError):
                PaymentService.handle_callback(
                    gateway_name="mock_gateway",
                    data=data,
                )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_callback_rejects_amount_drift_after_gateway_verification(self):
        from orders.payment.gateways import (
            GatewayCallbackResult,
            PaymentGatewayRegistry,
        )

        gateway_name = "drift_test_gateway"
        transaction_id = "DRIFT-TEST-001"
        payment = self.payment
        payment.gateway_name = gateway_name
        payment.transaction_code = transaction_id
        payment.save(update_fields=["gateway_name", "transaction_code"])

        class DriftGateway:
            name = gateway_name

            def extract_transaction_id(self, *, data):
                return transaction_id

            def verify_callback(self, *, payment, data):
                # Simulate a concurrent order/payment change while provider
                # verification is in flight. Final DB validation must use the
                # freshly locked Payment row.
                Payment.objects.filter(pk=payment.pk).update(amount=Decimal("60000000"))
                return GatewayCallbackResult(
                    transaction_id=transaction_id,
                    success=True,
                    amount=Decimal("50000000"),
                    reference_id="DRIFT-REF",
                    raw_response={"code": 100},
                )

            def build_redirect_url(self, *, payment, transaction_id):
                return "/"

            def initiate(self, *, payment):
                raise AssertionError("not used")

        PaymentGatewayRegistry.register(DriftGateway())

        with self.assertRaises(ValidationError):
            PaymentService.handle_callback(
                gateway_name=gateway_name,
                data={},
            )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, Decimal("60000000"))


class PaymentCallbackTransactionBoundaryTests(TransactionTestCase):
    reset_sequences = True

    def test_gateway_verification_runs_outside_database_transaction(self):
        from orders.payment.gateways import (
            GatewayCallbackResult,
            PaymentGatewayRegistry,
        )

        user = User.objects.create_user(username="boundary-user")
        profile = Profile.objects.get(user=user)
        profile.phone_number = "09110000011"
        profile.save(update_fields=["phone_number"])
        category = Category.objects.create(name="Boundary", slug="boundary")
        product = Product.objects.create(
            category=category,
            name="Boundary Product",
            slug="boundary-product",
            price=Decimal("50000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        cart = Cart.objects.create(user=user, status=Cart.STATUS_ORDERED)
        order = Order.objects.create(
            user=user,
            cart=cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            variant_id=None,
            product_name=product.name,
            variant_name="",
            sku=getattr(product, "sku", None),
            quantity=1,
            unit_price=Decimal("50000000"),
            subtotal_price=Decimal("50000000"),
        )
        payment = Payment.objects.create(
            order=order,
            user=user,
            amount=Decimal("50000000"),
            status=Payment.Status.PENDING,
            transaction_code="BOUNDARY-001",
            gateway_name="boundary_test_gateway",
        )

        observed_atomic_state = {"value": None}

        class BoundaryGateway:
            name = "boundary_test_gateway"

            def extract_transaction_id(self, *, data):
                return "BOUNDARY-001"

            def verify_callback(self, *, payment, data):
                observed_atomic_state["value"] = connection.in_atomic_block
                return GatewayCallbackResult(
                    transaction_id="BOUNDARY-001",
                    success=False,
                    amount=Decimal("50000000"),
                    raw_response={"status": "NOK"},
                )

            def build_redirect_url(self, *, payment, transaction_id):
                return "/"

            def initiate(self, *, payment):
                raise AssertionError("not used")

        PaymentGatewayRegistry.register(BoundaryGateway())

        PaymentService.handle_callback(
            gateway_name="boundary_test_gateway",
            data={"Authority": "BOUNDARY-001", "Status": "NOK"},
        )

        self.assertFalse(observed_atomic_state["value"])


@override_settings(DEBUG=True, PAYMENT_DEFAULT_GATEWAY="mock_gateway")
class ProcessPaymentViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_user_profile, sender=User)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_user_profile, sender=User)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-process-user",
            password="Password123!",
        )

        Profile.objects.create(
            user=self.user,
            phone_number="09110000003",
        )

        self.client = Client()
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="پرداخت",
            slug="payment-test",
        )

        self.product = Product.objects.create(
            category=self.category,
            name="Payment Test Product",
            slug="payment-test-product",
            price=Decimal("50000000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )

        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("50000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("50000000"),
            status=Order.Status.PENDING,
        )

        self.url = reverse(
            "orders:initiate_payment",
            kwargs={"order_number": self.order.order_number},
        )

    def test_initiate_payment_creates_single_pending_payment(self):
        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            302,
        )

        payment = Payment.objects.get(order=self.order)

        expected_url = (
            reverse("orders:mock_payment_gateway")
            + f"?trxid={payment.transaction_code}"
            + f"&order={self.order.order_number}"
            + "&amount=50000000"
        )

        self.assertEqual(
            response.url,
            expected_url,
        )

        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )

        self.assertEqual(
            payment.status,
            Payment.Status.PENDING,
        )

        self.assertEqual(
            payment.amount,
            Decimal("50000000"),
        )

        self.assertEqual(
            payment.gateway_name,
            "mock_gateway",
        )

    def test_repeated_initiate_payment_reuses_existing_payment(self):
        first_response = self.client.get(self.url)

        self.assertEqual(
            first_response.status_code,
            302,
        )

        first_payment = Payment.objects.get(order=self.order)
        second_response = self.client.get(self.url)

        self.assertEqual(
            second_response.status_code,
            302,
        )

        second_payment = Payment.objects.get(order=self.order)

        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            1,
        )

        self.assertEqual(
            second_payment.pk,
            first_payment.pk,
        )

        self.assertEqual(
            second_payment.status,
            Payment.Status.PENDING,
        )

    def test_paid_order_without_successful_payment_is_sent_to_reconciliation_not_receipt(self):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status"])

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse("accounts:dashboard_orders"))
        self.assertFalse(Payment.objects.filter(order=self.order).exists())

    def test_paid_order_with_successful_payment_redirects_to_receipt_without_recharging(self):
        self.order.status = Order.Status.PAID
        self.order.stock_reduced = True
        self.order.save(update_fields=["status", "stock_reduced", "updated"])
        payment = Payment.objects.create(
            order=self.order,
            amount=self.order.final_amount,
            status=Payment.Status.SUCCESS,
            gateway_name="mock_gateway",
            transaction_code="PAID-ORDER-EXISTING-TRX",
            reference_code="PAID-ORDER-EXISTING-REF",
        )

        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )
        self.assertEqual(Payment.objects.filter(order=self.order).count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)


class AdminOperationalTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-ops-user",
            email="admin-ops@example.com",
            password="AdminPass123!",
        )
        self.client.force_login(self.admin_user)

        self.category = Category.objects.create(
            name="Admin Ops Category",
            slug="admin-ops-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Admin Ops Product",
            slug="admin-ops-product",
            price=Decimal("1200000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        self.order = Order.objects.create(
            user=self.admin_user,
            subtotal_amount=Decimal("1200000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("150000"),
            final_amount=Decimal("1350000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("1200000"),
            subtotal_price=Decimal("1200000"),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.admin_user,
            amount=Decimal("1350000"),
            status=Payment.Status.PENDING,
            gateway_name="mock_gateway",
            transaction_code="ADMIN-TRX-001",
        )

    def test_operational_models_are_registered_in_admin(self):
        for model in (Order, OrderItem, Payment):
            self.assertIn(model, admin.site._registry)

        order_admin = admin.site._registry[Order]
        payment_admin = admin.site._registry[Payment]

        self.assertIn("status", order_admin.readonly_fields)
        self.assertIn("final_amount", order_admin.readonly_fields)
        self.assertIn("stock_reduced", order_admin.readonly_fields)
        self.assertIn("status", payment_admin.readonly_fields)
        self.assertIn("transaction_code", payment_admin.readonly_fields)

        request = RequestFactory().get(reverse("admin:orders_order_changelist"))
        request.user = self.admin_user
        protected_models = (Order, OrderItem, Payment, OrderAddressSnapshot)
        for model in protected_models:
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request), model.__name__)
            self.assertFalse(model_admin.has_delete_permission(request), model.__name__)

        for inline in order_admin.get_inline_instances(request, self.order):
            self.assertFalse(inline.has_add_permission(request, self.order))
            self.assertFalse(inline.can_delete)

    def test_order_admin_changelist_is_operational(self):
        response = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn(self.order.order_number, content)
        self.assertIn("در انتظار پرداخت", content)

    def test_payment_admin_changelist_is_operational(self):
        response = self.client.get(reverse("admin:orders_payment_changelist"))

        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn(self.order.order_number, content)
        self.assertIn(self.payment.transaction_code, content)


class OrderPaymentDataPermissionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="order-payment-permission-staff",
            email="order-payment-permission@example.test",
            password="Password123!",
            is_staff=True,
        )
        self.order = Order.objects.create(
            user=self.staff,
            order_number="ORD-PAYMENT-PERMISSION",
            status=Order.Status.PAID,
            subtotal_amount=Decimal("125000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("125000"),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.staff,
            amount=Decimal("125000"),
            status=Payment.Status.SUCCESS,
            gateway_name="zarinpal",
            transaction_code="PRIVATE-TRANSACTION-REF-001",
            reference_code="PRIVATE-PROVIDER-REFERENCE-001",
            gateway_response='{"private_provider_payload":"DO-NOT-EXPOSE"}',
        )

    def grant(self, codename):
        permission = Permission.objects.get(
            content_type__app_label="orders",
            codename=codename,
        )
        self.staff.user_permissions.add(permission)

    def test_order_only_staff_cannot_view_payment_fields_in_order_admin(self):
        self.grant("view_order")
        self.client.force_login(self.staff)

        order_admin = admin.site._registry[Order]
        request = RequestFactory().get(reverse("admin:orders_order_changelist"))
        request.user = self.staff

        self.assertNotIn("get_payment_status_display", order_admin.get_list_display(request))
        self.assertNotIn("payment__status", order_admin.get_list_filter(request))
        self.assertFalse(
            any(field.startswith("payment__") for field in order_admin.get_search_fields(request))
        )
        self.assertNotIn("payment", order_admin.get_list_select_related(request))

        payment_inline_instances = [
            inline for inline in order_admin.get_inline_instances(request, self.order)
            if inline.model is Payment
        ]
        self.assertEqual(payment_inline_instances, [])

        changelist_url = reverse("admin:orders_order_changelist")
        list_response = self.client.get(changelist_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, "وضعیت پرداخت")
        self.assertNotContains(list_response, self.payment.transaction_code)
        self.assertNotContains(list_response, self.payment.reference_code)
        self.assertNotContains(list_response, "DO-NOT-EXPOSE")

        detail_response = self.client.get(
            reverse("admin:orders_order_change", args=[self.order.pk])
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, self.payment.transaction_code)
        self.assertNotContains(detail_response, self.payment.reference_code)
        self.assertNotContains(detail_response, "DO-NOT-EXPOSE")

        payment_admin_response = self.client.get(
            reverse("admin:orders_payment_changelist")
        )
        self.assertEqual(payment_admin_response.status_code, 403)

    def test_order_only_staff_cannot_search_or_filter_using_payment_data(self):
        self.grant("view_order")
        self.client.force_login(self.staff)
        changelist_url = reverse("admin:orders_order_changelist")

        search_response = self.client.get(
            changelist_url,
            {"q": self.payment.transaction_code},
        )
        self.assertEqual(search_response.status_code, 200)
        self.assertNotContains(search_response, self.order.order_number)

        direct_filter_response = self.client.get(
            changelist_url,
            {"payment__status__exact": Payment.Status.SUCCESS},
        )
        self.assertEqual(direct_filter_response.status_code, 400)

    def test_view_payment_permission_restores_payment_read_surfaces(self):
        self.grant("view_order")
        self.grant("view_payment")
        self.client.force_login(self.staff)

        list_response = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "وضعیت پرداخت")
        self.assertContains(list_response, "پرداخت موفق")

        detail_response = self.client.get(
            reverse("admin:orders_order_change", args=[self.order.pk])
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.payment.transaction_code)
        self.assertContains(detail_response, self.payment.reference_code)
        self.assertContains(detail_response, "DO-NOT-EXPOSE")

        payment_list_response = self.client.get(
            reverse("admin:orders_payment_changelist")
        )
        self.assertEqual(payment_list_response.status_code, 200)
        self.assertContains(payment_list_response, self.payment.transaction_code)

    def test_change_payment_permission_also_allows_payment_read_surfaces(self):
        self.grant("view_order")
        self.grant("change_payment")
        self.client.force_login(self.staff)

        list_response = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "وضعیت پرداخت")

        detail_response = self.client.get(
            reverse("admin:orders_order_change", args=[self.order.pk])
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.payment.transaction_code)


class OrderStatusTransitionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="order-status-user",
            password="Password123!",
        )
        self.category = Category.objects.create(
            name="Order Status Category",
            slug="order-status-category",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Order Status Product",
            slug="order-status-product",
            price=Decimal("1000000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )

    def create_order(self, status):
        order = Order.objects.create(
            user=self.user,
            subtotal_amount=Decimal("1000000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("1000000"),
            status=status,
            stock_reduced=status in {
                Order.Status.PAID,
                Order.Status.PROCESSING,
                Order.Status.COMPLETED,
            },
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("1000000"),
            subtotal_price=Decimal("1000000"),
        )
        Payment.objects.create(
            order=order,
            status=(
                Payment.Status.SUCCESS
                if status in {
                    Order.Status.PAID,
                    Order.Status.PROCESSING,
                    Order.Status.COMPLETED,
                }
                else Payment.Status.PENDING
            ),
        )
        return order

    def test_paid_order_can_move_to_processing(self):
        order = self.create_order(Order.Status.PAID)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.PROCESSING,
        )
        self.assertEqual(result.status, Order.Status.PROCESSING)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)

    def test_paid_order_without_stock_reduction_cannot_move_to_processing(self):
        order = self.create_order(Order.Status.PAID)
        Order.objects.filter(pk=order.pk).update(stock_reduced=False)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.PROCESSING,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertFalse(order.stock_reduced)

    def test_processing_order_without_stock_reduction_cannot_be_completed(self):
        order = self.create_order(Order.Status.PROCESSING)
        Order.objects.filter(pk=order.pk).update(stock_reduced=False)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.COMPLETED,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assertFalse(order.stock_reduced)

    def test_processing_order_can_move_to_completed_and_sets_timestamp(self):
        order = self.create_order(Order.Status.PROCESSING)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.COMPLETED,
        )
        self.assertEqual(result.status, Order.Status.COMPLETED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)

    def test_processing_order_with_non_successful_payment_cannot_be_completed(self):
        order = self.create_order(Order.Status.PROCESSING)
        payment = Payment.objects.get(order=order)

        for payment_status in (
            Payment.Status.PENDING,
            Payment.Status.FAILED,
            Payment.Status.REFUNDED,
            Payment.Status.CANCELLED,
        ):
            with self.subTest(payment_status=payment_status):
                payment.status = payment_status
                payment.save(update_fields=["status", "updated"])

                with self.assertRaises(ValidationError):
                    OrderService.transition_status(
                        order_id=order.pk,
                        new_status=Order.Status.COMPLETED,
                    )

                order.refresh_from_db()
                self.assertEqual(order.status, Order.Status.PROCESSING)
                self.assertIsNone(order.completed_at)

    def test_processing_order_without_payment_cannot_be_completed(self):
        order = self.create_order(Order.Status.PROCESSING)
        Payment.objects.filter(order=order).delete()

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.COMPLETED,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assertIsNone(order.completed_at)

    def test_unpaid_order_with_stock_reduced_flag_cannot_be_cancelled_without_reconciliation(self):
        order = self.create_order(Order.Status.PENDING)
        Order.objects.filter(pk=order.pk).update(stock_reduced=True)

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.CANCELLED,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertTrue(order.stock_reduced)
        self.assertIsNone(order.cancelled_at)

    def test_unpaid_order_can_be_cancelled_and_sets_timestamp(self):
        order = self.create_order(Order.Status.PENDING)
        result = OrderService.transition_status(
            order_id=order.pk,
            new_status=Order.Status.CANCELLED,
        )
        self.assertEqual(result.status, Order.Status.CANCELLED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(order.cancelled_at)

    def test_paid_order_with_failed_payment_cannot_move_to_processing(self):
        order = self.create_order(Order.Status.PAID)
        payment = Payment.objects.get(order=order)
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated"])

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.PROCESSING,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_successfully_paid_pending_order_cannot_be_cancelled_as_unpaid(self):
        order = self.create_order(Order.Status.PENDING)
        payment = Payment.objects.get(order=order)
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=["status", "updated"])

        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.CANCELLED,
            )

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_invalid_status_transition_is_rejected(self):
        order = self.create_order(Order.Status.PAID)
        with self.assertRaises(ValidationError):
            OrderService.transition_status(
                order_id=order.pk,
                new_status=Order.Status.COMPLETED,
            )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)


class AdminOrderWorkflowConfigurationTests(TestCase):
    def test_order_admin_exposes_persian_stock_reduction_filter_and_badge(self):
        admin_user = User.objects.create_superuser(
            username="stock-reduction-admin",
            email="stock-reduction-admin@example.test",
            password="TestPassword123!",
        )
        recorded_order = Order.objects.create(
            user=admin_user,
            status=Order.Status.PAID,
            stock_reduced=True,
            subtotal_amount=Decimal("50000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        pending_order = Order.objects.create(
            user=admin_user,
            status=Order.Status.PENDING,
            stock_reduced=False,
            subtotal_amount=Decimal("40000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
        )
        self.client.force_login(admin_user)

        list_response = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "وضعیت کاهش موجودی")
        self.assertContains(list_response, "ثبت شده")
        self.assertContains(list_response, "ثبت نشده")
        self.assertNotContains(list_response, "stock reduced")

        recorded_filter = self.client.get(
            reverse("admin:orders_order_changelist"),
            {"stock_reduced": "yes"},
        )
        self.assertEqual(recorded_filter.status_code, 200)
        self.assertContains(recorded_filter, recorded_order.order_number)
        self.assertNotContains(recorded_filter, pending_order.order_number)

        form_response = self.client.get(
            reverse("admin:orders_order_change", args=[pending_order.pk])
        )
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, "وضعیت کاهش موجودی")
        self.assertContains(form_response, "ثبت نشده")
        self.assertNotContains(form_response, "Stock reduced")

        order_admin = admin.site._registry[Order]
        self.assertIn("stock_reduced", order_admin.readonly_fields)
        self.assertIn("stock_reduced_display", order_admin.readonly_fields)
        self.assertIn("stock_reduced_display", order_admin.list_display)

    def test_order_admin_exposes_only_service_backed_workflow_actions(self):
        order_admin = admin.site._registry[Order]
        action_names = {action.__name__ for action in order_admin.actions}
        self.assertEqual(
            action_names,
            {
                "move_paid_orders_to_processing",
                "move_processing_orders_to_completed",
                "cancel_unpaid_orders",
            },
        )


class OrderAdminActionAuditLogTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="order-action-audit-staff",
            email="order-action-audit@example.test",
            password="Password123!",
            is_staff=True,
        )
        change_permission = Permission.objects.get(
            content_type__app_label="orders",
            codename="change_order",
        )
        self.staff.user_permissions.add(change_permission)
        self.client.force_login(self.staff)

    def create_order(self, *, status, payment_status, stock_reduced):
        order = Order.objects.create(
            user=self.staff,
            status=status,
            stock_reduced=stock_reduced,
            subtotal_amount=Decimal("100000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("100000"),
        )
        Payment.objects.create(
            order=order,
            user=self.staff,
            amount=Decimal("100000"),
            status=payment_status,
            gateway_name="mock_gateway",
        )
        return order

    def execute_action(self, action_name, order):
        return self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": action_name,
                "_selected_action": [str(order.pk)],
                "index": "0",
            },
        )

    def assert_action_was_logged(self, order):
        entry = LogEntry.objects.get(
            user=self.staff,
            content_type__app_label="orders",
            content_type__model="order",
            object_id=str(order.pk),
            action_flag=CHANGE,
        )
        self.assertIn("وضعیت سفارش", entry.change_message)
        self.assertIn("عملیات گروهی", entry.change_message)

    def test_processing_action_records_actor_and_status_change(self):
        order = self.create_order(
            status=Order.Status.PAID,
            payment_status=Payment.Status.SUCCESS,
            stock_reduced=True,
        )

        response = self.execute_action("move_paid_orders_to_processing", order)

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assert_action_was_logged(order)

    def test_completion_action_records_actor_and_status_change(self):
        order = self.create_order(
            status=Order.Status.PROCESSING,
            payment_status=Payment.Status.SUCCESS,
            stock_reduced=True,
        )

        response = self.execute_action("move_processing_orders_to_completed", order)

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assert_action_was_logged(order)

    def test_cancellation_action_records_actor_and_status_change(self):
        order = self.create_order(
            status=Order.Status.PENDING,
            payment_status=Payment.Status.PENDING,
            stock_reduced=False,
        )

        response = self.execute_action("cancel_unpaid_orders", order)

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assert_action_was_logged(order)

    def test_rejected_transition_does_not_create_success_audit_entry(self):
        order = self.create_order(
            status=Order.Status.PENDING,
            payment_status=Payment.Status.PENDING,
            stock_reduced=False,
        )

        response = self.execute_action("move_paid_orders_to_processing", order)

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(
            LogEntry.objects.filter(
                user=self.staff,
                content_type__app_label="orders",
                content_type__model="order",
                object_id=str(order.pk),
                action_flag=CHANGE,
            ).exists()
        )

    def test_view_only_staff_cannot_expose_or_execute_status_actions(self):
        order = self.create_order(
            status=Order.Status.PAID,
            payment_status=Payment.Status.SUCCESS,
            stock_reduced=True,
        )
        view_only_staff = User.objects.create_user(
            username="order-action-view-only",
            password="Password123!",
            is_staff=True,
        )
        view_permission = Permission.objects.get(
            content_type__app_label="orders",
            codename="view_order",
        )
        view_only_staff.user_permissions.add(view_permission)

        request = RequestFactory().get(reverse("admin:orders_order_changelist"))
        request.user = view_only_staff
        order_admin = admin.site._registry[Order]
        actions = order_admin.get_actions(request)
        self.assertNotIn("move_paid_orders_to_processing", actions)
        self.assertNotIn("move_processing_orders_to_completed", actions)
        self.assertNotIn("cancel_unpaid_orders", actions)

        self.client.force_login(view_only_staff)
        response = self.execute_action("move_paid_orders_to_processing", order)

        self.assertLess(response.status_code, 500)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertFalse(
            LogEntry.objects.filter(
                user=view_only_staff,
                content_type__app_label="orders",
                content_type__model="order",
                object_id=str(order.pk),
                action_flag=CHANGE,
            ).exists()
        )


class PaymentPresentationContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="payment-presentation-user",
            password="Password123!",
        )
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="Payment Presentation",
            slug="payment-presentation",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Payment Presentation Product",
            slug="payment-presentation-product",
            price=Decimal("125000"),
            stock=10,
            is_active=True,
            is_available_status=True,
        )

        self.cart = Cart.objects.create(
            user=self.user,
            status=Cart.STATUS_ORDERED,
        )
        self.order = Order.objects.create(
            user=self.user,
            cart=self.cart,
            subtotal_amount=Decimal("125000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("15000"),
            final_amount=Decimal("140000"),
            status=Order.Status.PENDING,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("125000"),
            subtotal_price=Decimal("125000"),
        )
        OrderAddressSnapshot.objects.create(
            order=self.order,
            recipient_name="کاربر تست",
            recipient_mobile="09120000000",
            postal_code="1234567890",
            province="تهران",
            city="تهران",
            address_line="آدرس تست",
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.user,
            amount=Decimal("140000"),
            status=Payment.Status.PENDING,
            gateway_name="zarinpal",
            transaction_code="AUTH-PRESENT-001",
        )

    def test_confirmation_pending_state_renders_correct_payment_action(self):
        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("پیش‌فاکتور نهایی سفارش", content)
        self.assertIn("در انتظار پرداخت", content)
        self.assertIn("پرداخت آنلاین و نهایی", content)
        self.assertIn(self.order.order_number, content)
        self.assertIn("تاریخ ثبت:", content)

    def test_confirmation_uses_stored_order_discount_after_product_old_price_changes(self):
        self.order.discount_amount = Decimal("10000")
        self.order.final_amount = Decimal("130000")
        self.order.save(update_fields=["discount_amount", "final_amount", "updated"])

        self.payment.amount = Decimal("130000")
        self.payment.save(update_fields=["amount", "updated"])

        # Product.old_price remains editable in ProductAdmin, but it is not
        # a historical price snapshot for this already-created order.
        self.product.old_price = Decimal("200000")
        self.product.save(update_fields=["old_price", "updated"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تخفیف سفارش:")
        self.assertContains(response, "10,000 تومان")
        self.assertContains(response, "130,000 تومان")
        self.assertNotContains(response, "75,000")

    def test_confirmation_failed_state_exposes_retry_action(self):
        self.payment.status = Payment.Status.FAILED
        self.payment.save(update_fields=["status"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("پرداخت قبلی ناموفق بوده است", content)
        self.assertIn("تلاش مجدد برای پرداخت", content)
        self.assertIn(self.payment.transaction_code, content)

    def test_confirmation_paid_order_redirects_to_success_page(self):
        self.order.status = Order.Status.PAID
        self.order.stock_reduced = True
        self.order.save(update_fields=["status", "stock_reduced", "updated"])

        self.payment.status = Payment.Status.SUCCESS
        self.payment.save(update_fields=["status", "updated"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            ),
        )

    def test_success_receipt_distinguishes_reference_and_transaction(self):
        self.order.status = Order.Status.PAID
        self.order.stock_reduced = True
        self.order.save(update_fields=["status", "stock_reduced", "updated"])
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "REF-PRESENT-001"
        self.payment.save(update_fields=["status", "reference_code", "updated"])

        response = self.client.get(
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("کد رهگیری پرداخت:", content)
        self.assertIn(self.payment.reference_code, content)
        self.assertIn("شناسه تراکنش درگاه:", content)
        self.assertIn(self.payment.transaction_code, content)
        self.assertNotIn("کد پیگیری تراکنش:", content)

    def test_failed_receipt_labels_transaction_code_correctly(self):
        self.payment.status = Payment.Status.FAILED
        self.payment.reference_code = ""
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client.get(
            reverse(
                "orders:payment_failed",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("شناسه تراکنش درگاه:", content)
        self.assertIn(self.payment.transaction_code, content)
        self.assertNotIn("کد رهگیری تراکنش:", content)

    def test_confirmation_paid_order_without_successful_payment_requires_reconciliation(self):
        self.order.status = Order.Status.PAID
        self.order.save(update_fields=["status", "updated"])

        response = self.client.get(
            reverse(
                "orders:order_confirmation",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertRedirects(response, reverse("accounts:dashboard_orders"))

    def test_success_receipt_message_matches_paid_processing_and_completed_states(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.save(update_fields=["status", "updated"])
        receipt_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )
        expected_messages = {
            Order.Status.PAID: "سفارش در انتظار شروع پردازش است",
            Order.Status.PROCESSING: "سفارش شما در حال پردازش است",
            Order.Status.COMPLETED: "سفارش شما تکمیل شده است",
        }

        for status, expected_message in expected_messages.items():
            with self.subTest(order_status=status):
                Order.objects.filter(pk=self.order.pk).update(
                    status=status,
                    stock_reduced=True,
                )
                response = self.client.get(receipt_url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_message)
                self.assertNotContains(
                    response,
                    "پرداخت در درگاه موفق ثبت شده، اما وضعیت سفارش یا ثبت کاهش موجودی",
                )

    def test_success_receipt_flags_successful_payment_with_inconsistent_order_or_inventory(self):
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "RECONCILIATION-REQUIRED-REF"
        self.payment.save(update_fields=["status", "reference_code", "updated"])
        receipt_url = reverse(
            "orders:payment_success",
            kwargs={"order_number": self.order.order_number},
        )

        inconsistent_states = (
            Order.Status.PENDING,
            Order.Status.PLACED,
            Order.Status.PAID,
            Order.Status.PROCESSING,
            Order.Status.COMPLETED,
        )
        for status in inconsistent_states:
            with self.subTest(order_status=status):
                Order.objects.filter(pk=self.order.pk).update(
                    status=status,
                    stock_reduced=False,
                )
                response = self.client.get(receipt_url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "پرداخت در درگاه موفق ثبت شده، اما وضعیت سفارش یا ثبت کاهش موجودی",
                )
                self.assertNotContains(
                    response,
                    "سفارش شما با موفقیت ثبت شد و در مرحله پردازش قرار گرفت.",
                )
                persisted = Order.objects.get(pk=self.order.pk)
                self.assertEqual(persisted.status, status)
                self.assertFalse(persisted.stock_reduced)

    def test_success_receipt_flags_payment_for_cancelled_order(self):
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=["status"])
        self.payment.status = Payment.Status.SUCCESS
        self.payment.reference_code = "LATE-SUCCESS-REF"
        self.payment.save(update_fields=["status", "reference_code"])

        response = self.client.get(
            reverse(
                "orders:payment_success",
                kwargs={"order_number": self.order.order_number},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "پرداخت در درگاه موفق بوده، اما سفارش در وضعیت لغوشده قرار دارد.",
        )
        self.assertNotContains(
            response,
            "سفارش شما با موفقیت ثبت شد و در مرحله پردازش قرار گرفت.",
        )


class AdminOrderedCartProtectionTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-cart-protection",
            email="admin-cart-protection@example.com",
            password="AdminPass123!",
        )
        self.client.force_login(self.admin_user)

        self.category = Category.objects.create(
            name="Admin Cart Protection",
            slug="admin-cart-protection",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Admin Cart Protection Product",
            slug="admin-cart-protection-product",
            price=Decimal("900000"),
            stock=5,
            is_active=True,
            is_available_status=True,
        )
        self.ordered_cart = Cart.objects.create(
            user=self.admin_user,
            status=Cart.STATUS_ORDERED,
        )
        self.cart_item = CartItem.objects.create(
            cart=self.ordered_cart,
            product=self.product,
            quantity=1,
            unit_price_snapshot=Decimal("900000"),
            status=CartItem.STATUS_ACTIVE,
        )
        self.request = self.client.get("/").wsgi_request

    def test_ordered_cart_freezes_identity_and_lifecycle_fields(self):
        cart_admin = admin.site._registry[Cart]

        readonly_fields = set(
            cart_admin.get_readonly_fields(self.request, self.ordered_cart)
        )

        self.assertTrue({"user", "session_key", "status"}.issubset(readonly_fields))

        delete_url = reverse(
            "admin:cart_cart_delete",
            args=[self.ordered_cart.pk],
        )
        response = self.client.post(delete_url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Cart.objects.filter(pk=self.ordered_cart.pk).exists())

    def test_ordered_cart_item_is_readonly_and_cannot_be_deleted(self):
        cart_item_admin = admin.site._registry[CartItem]

        readonly_fields = set(
            cart_item_admin.get_readonly_fields(self.request, self.cart_item)
        )

        self.assertTrue(
            {
                "cart",
                "product",
                "variant",
                "quantity",
                "unit_price_snapshot",
                "status",
            }.issubset(readonly_fields)
        )

        delete_url = reverse(
            "admin:cart_cartitem_delete",
            args=[self.cart_item.pk],
        )
        response = self.client.post(delete_url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_ordered_cart_inline_is_readonly_and_cannot_delete_items(self):
        cart_admin = admin.site._registry[Cart]
        inline_admin = cart_admin.inlines[0](
            CartItem,
            admin.site,
        )

        readonly_fields = set(
            inline_admin.get_readonly_fields(self.request, self.ordered_cart)
        )

        self.assertTrue(
            {"product", "variant", "quantity", "status"}.issubset(readonly_fields)
        )
        self.assertFalse(
            inline_admin.has_delete_permission(
                self.request,
                self.ordered_cart,
            )
        )

    def test_product_with_cart_reference_cannot_be_deleted_from_admin(self):
        product_admin = admin.site._registry[Product]

        self.assertFalse(
            product_admin.has_delete_permission(self.request, self.product)
        )

        delete_url = reverse(
            "admin:catalog_product_delete",
            args=[self.product.pk],
        )
        response = self.client.post(delete_url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_product_with_only_order_history_remains_deletable(self):
        product_admin = admin.site._registry[Product]
        historical_product = Product.objects.create(
            category=self.category,
            name="Historical Product",
            slug="historical-product",
            price=Decimal("700000"),
            stock=0,
            is_active=False,
            is_available_status=False,
        )
        order = Order.objects.create(
            user=self.admin_user,
            subtotal_amount=Decimal("700000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("0"),
            final_amount=Decimal("700000"),
            status=Order.Status.COMPLETED,
        )
        order_item = OrderItem.objects.create(
            order=order,
            product=historical_product,
            product_name=historical_product.name,
            variant_name="",
            sku=None,
            quantity=1,
            unit_price=Decimal("700000"),
            subtotal_price=Decimal("700000"),
        )

        self.assertTrue(
            product_admin.has_delete_permission(self.request, historical_product)
        )

        delete_url = reverse(
            "admin:catalog_product_delete",
            args=[historical_product.pk],
        )
        response = self.client.post(
            delete_url,
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=historical_product.pk).exists())
        order_item.refresh_from_db()
        self.assertIsNone(order_item.product_id)
        self.assertEqual(order_item.product_name, "Historical Product")

    def test_product_variant_with_cart_reference_cannot_be_deleted_from_admin(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=Decimal("50000"),
        )
        self.cart_item.variant = variant
        self.cart_item.unit_price_snapshot = Decimal("950000")
        self.cart_item.save(update_fields=["variant", "unit_price_snapshot", "updated"])

        variant_admin = admin.site._registry[ProductVariant]
        self.assertFalse(variant_admin.has_delete_permission(self.request, variant))

        delete_url = reverse(
            "admin:catalog_productvariant_delete",
            args=[variant.pk],
        )
        response = self.client.post(delete_url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProductVariant.objects.filter(pk=variant.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_ordered_cart_cannot_be_bulk_deleted(self):
        response = self.client.post(
            reverse("admin:cart_cart_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.ordered_cart.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Cart.objects.filter(pk=self.ordered_cart.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_ordered_cart_item_cannot_be_bulk_deleted(self):
        response = self.client.post(
            reverse("admin:cart_cartitem_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.cart_item.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_product_with_cart_reference_cannot_be_bulk_deleted(self):
        response = self.client.post(
            reverse("admin:catalog_product_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.product.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_product_variant_with_cart_reference_cannot_be_bulk_deleted(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="آبی",
            price_adjustment=Decimal("25000"),
        )
        self.cart_item.variant = variant
        self.cart_item.save(update_fields=["variant", "updated"])

        response = self.client.post(
            reverse("admin:catalog_productvariant_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(variant.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProductVariant.objects.filter(pk=variant.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.cart_item.pk).exists())

    def test_active_cart_admin_remains_editable(self):
        active_user = User.objects.create_user(
            username="active-cart-admin-user",
            password="Password123!",
        )
        active_cart = Cart.objects.create(
            user=active_user,
            status=Cart.STATUS_ACTIVE,
        )

        cart_admin = admin.site._registry[Cart]
        cart_readonly = set(cart_admin.get_readonly_fields(self.request, active_cart))
        self.assertNotIn("status", cart_readonly)
        self.assertTrue(cart_admin.has_delete_permission(self.request, active_cart))

        active_item = CartItem.objects.create(
            cart=active_cart,
            product=self.product,
            quantity=1,
            unit_price_snapshot=Decimal("900000"),
            status=CartItem.STATUS_ACTIVE,
        )
        cart_item_admin = admin.site._registry[CartItem]
        item_readonly = set(
            cart_item_admin.get_readonly_fields(self.request, active_item)
        )
        self.assertNotIn("quantity", item_readonly)
        self.assertTrue(
            cart_item_admin.has_delete_permission(self.request, active_item)
        )


@skipUnless(
    connection.vendor == "sqlite",
    "This migration repair test requires the SQLite backend.",
)
class SQLiteOrderItemProductSchemaRepairTests(TestCase):
    def test_repair_converts_legacy_sqlite_schema_and_preserves_history(self):
        import importlib
        import sqlite3

        migration = importlib.import_module(
            "orders.migrations.0007_repair_orderitem_product_schema"
        )

        raw_connection = sqlite3.connect(":memory:")
        raw_connection.execute("PRAGMA foreign_keys = ON")

        raw_connection.executescript("""
            CREATE TABLE "catalog_product" (
                "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                "name" varchar(250) NOT NULL
            );

            CREATE TABLE "orders_order" (
                "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                "order_number" varchar(32) NOT NULL
            );

            CREATE TABLE "orders_orderitem" (
                "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                "product_id" bigint NOT NULL,
                "variant_id" bigint NULL,
                "product_name" varchar(255) NOT NULL,
                "variant_name" varchar(255) NOT NULL,
                "sku" varchar(100) NULL,
                "quantity" integer NOT NULL,
                "unit_price" decimal NOT NULL,
                "subtotal_price" decimal NOT NULL,
                "created" datetime NOT NULL,
                "updated" datetime NOT NULL,
                "order_id" bigint NOT NULL
                    REFERENCES "orders_order" ("id")
                    DEFERRABLE INITIALLY DEFERRED
            );

            CREATE INDEX "orders_orde_order_i_5d347b_idx"
            ON "orders_orderitem" ("order_id");

            CREATE INDEX "orders_orde_variant_164791_idx"
            ON "orders_orderitem" ("variant_id");
            """)

        raw_connection.execute(
            'INSERT INTO "catalog_product" ("name") VALUES (?)',
            ("Valid product",),
        )
        raw_connection.execute(
            'INSERT INTO "orders_order" ("order_number") VALUES (?)',
            ("ORD-REPAIR-TEST",),
        )

        raw_connection.executemany(
            """
            INSERT INTO "orders_orderitem" (
                "product_id",
                "variant_id",
                "product_name",
                "variant_name",
                "sku",
                "quantity",
                "unit_price",
                "subtotal_price",
                "created",
                "updated",
                "order_id"
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    None,
                    "Valid product",
                    "",
                    None,
                    1,
                    100,
                    100,
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:00:00",
                    1,
                ),
                (
                    999,
                    None,
                    "Deleted historical product",
                    "",
                    None,
                    2,
                    200,
                    400,
                    "2026-01-02 00:00:00",
                    "2026-01-02 00:00:00",
                    1,
                ),
            ],
        )
        raw_connection.commit()

        class DebugCursor:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, sql, params=None):
                if params is None:
                    return self._cursor.execute(sql)

                converted_sql = sql.replace("%s", "?")
                result = self._cursor.execute(converted_sql, params)

                # Reproduce Django 6.0.6 SQLite DEBUG interpolation.
                connection.ops.last_executed_query(
                    self._cursor,
                    sql,
                    params,
                )

                return result

            def __getattr__(self, name):
                return getattr(self._cursor, name)

        class CursorContext:
            def __init__(self, cursor):
                self.cursor = cursor

            def __enter__(self):
                return self.cursor

            def __exit__(self, exc_type, exc_value, traceback):
                self.cursor.close()

        class ConnectionAdapter:
            vendor = "sqlite"
            in_atomic_block = False

            def cursor(self):
                return CursorContext(DebugCursor(raw_connection.cursor()))

        class SchemaEditorAdapter:
            connection = ConnectionAdapter()

        schema_editor = SchemaEditorAdapter()

        migration.repair_orderitem_product_schema(
            None,
            schema_editor,
        )

        columns = raw_connection.execute(
            'PRAGMA table_info("orders_orderitem")'
        ).fetchall()
        product_column = next(column for column in columns if column[1] == "product_id")

        self.assertEqual(product_column[3], 0)

        foreign_keys = raw_connection.execute(
            'PRAGMA foreign_key_list("orders_orderitem")'
        ).fetchall()

        self.assertTrue(
            any(
                fk[2] == "catalog_product"
                and fk[3] == "product_id"
                and fk[4] == "id"
                and str(fk[6]).upper() == "NO ACTION"
                for fk in foreign_keys
            )
        )

        rows = raw_connection.execute("""
            SELECT "id", "product_id", "product_name", "quantity", "subtotal_price"
            FROM "orders_orderitem"
            ORDER BY "id"
            """).fetchall()

        self.assertEqual(
            rows,
            [
                (1, 1, "Valid product", 1, 100),
                (2, None, "Deleted historical product", 2, 400),
            ],
        )

        indexes = raw_connection.execute("""
            SELECT "name"
            FROM sqlite_master
            WHERE "type" = 'index'
              AND "tbl_name" = 'orders_orderitem'
              AND "sql" IS NOT NULL
            ORDER BY "name"
            """).fetchall()

        self.assertEqual(
            indexes,
            [
                ("orders_orde_order_i_5d347b_idx",),
                ("orders_orde_variant_164791_idx",),
            ],
        )

        self.assertEqual(
            raw_connection.execute(
                'PRAGMA foreign_key_check("orders_orderitem")'
            ).fetchall(),
            [],
        )
        self.assertEqual(
            raw_connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )

        # Idempotence: once repaired, running the repair again is a no-op.
        migration.repair_orderitem_product_schema(
            None,
            schema_editor,
        )

        self.assertEqual(
            raw_connection.execute(
                'SELECT COUNT(*) FROM "orders_orderitem"'
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            raw_connection.execute(
                'SELECT "product_id" FROM "orders_orderitem" WHERE "id" = 2'
            ).fetchone()[0],
            None,
        )

        raw_connection.close()



class ShippingMethodAdminDeletionSafetyTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="shipping-delete-safety-admin",
            email="shipping-delete-safety@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(self.admin_user)
        self.method = ShippingMethod.objects.create(
            name="Historical Shipping Method",
            description="Method preserved for historic orders",
            cost=Decimal("50000"),
            estimated_delivery_days=3,
            is_active=True,
        )
        self.order = Order.objects.create(
            user=self.admin_user,
            shipping_method=self.method,
            subtotal_amount=Decimal("120000"),
            discount_amount=Decimal("0"),
            shipping_amount=Decimal("50000"),
            final_amount=Decimal("170000"),
            status=Order.Status.PENDING,
        )

    def test_shipping_method_used_by_order_cannot_be_deleted(self):
        response = self.client.post(
            reverse("admin:orders_shippingmethod_delete", args=[self.method.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            ShippingMethod.objects.filter(pk=self.method.pk).exists()
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_method_id, self.method.pk)

    def test_unused_shipping_method_remains_deletable(self):
        unused_method = ShippingMethod.objects.create(
            name="Unused Shipping Method",
            cost=Decimal("30000"),
            estimated_delivery_days=2,
            is_active=True,
        )

        response = self.client.post(
            reverse("admin:orders_shippingmethod_delete", args=[unused_method.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ShippingMethod.objects.filter(pk=unused_method.pk).exists())

    def test_shipping_method_used_by_order_cannot_be_bulk_deleted_from_admin(self):
        response = self.client.post(
            reverse("admin:orders_shippingmethod_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.method.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ShippingMethod.objects.filter(pk=self.method.pk).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_method_id, self.method.pk)

    
    def test_shipping_method_used_by_order_keeps_its_definition_readonly(self):
        shipping_admin = admin.site._registry[ShippingMethod]
        historical_fields = {
            "name",
            "description",
            "cost",
            "free_shipping_threshold",
            "estimated_delivery_days",
        }
        readonly_fields = set(
            shipping_admin.get_readonly_fields(None, self.method)
        )

        self.assertTrue(historical_fields.issubset(readonly_fields))
        self.assertNotIn("is_active", readonly_fields)
        self.assertEqual(shipping_admin.list_editable, ("is_active",))

        response = self.client.post(
            reverse("admin:orders_shippingmethod_change", args=[self.method.pk]),
            {
                "name": "Renamed Historical Method",
                "description": "Changed after order creation",
                "cost": "99000",
                "free_shipping_threshold": "250000",
                "estimated_delivery_days": "9",
                "is_active": "on",
                "_save": "ذخیره",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.method.refresh_from_db()
        self.assertEqual(self.method.name, "Historical Shipping Method")
        self.assertEqual(self.method.description, "Method preserved for historic orders")
        self.assertEqual(self.method.cost, Decimal("50000"))
        self.assertIsNone(self.method.free_shipping_threshold)
        self.assertEqual(self.method.estimated_delivery_days, 3)

        detail_response = self.client.get(
            reverse(
                "accounts:dashboard_order_detail",
                args=[self.order.order_number],
            )
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Historical Shipping Method")
        self.assertNotContains(detail_response, "Renamed Historical Method")

    def test_unused_shipping_method_definition_remains_editable(self):
        unused_method = ShippingMethod.objects.create(
            name="Unused Editable Method",
            description="Before update",
            cost=Decimal("30000"),
            free_shipping_threshold=None,
            estimated_delivery_days=2,
            is_active=True,
        )
        shipping_admin = admin.site._registry[ShippingMethod]
        readonly_fields = set(
            shipping_admin.get_readonly_fields(None, unused_method)
        )
        historical_fields = {
            "name",
            "description",
            "cost",
            "free_shipping_threshold",
            "estimated_delivery_days",
        }
        self.assertTrue(historical_fields.isdisjoint(readonly_fields))

        response = self.client.post(
            reverse("admin:orders_shippingmethod_change", args=[unused_method.pk]),
            {
                "name": "Updated Editable Method",
                "description": "After update",
                "cost": "45000",
                "free_shipping_threshold": "300000",
                "estimated_delivery_days": "5",
                "is_active": "on",
                "_save": "ذخیره",
            },
        )

        self.assertEqual(response.status_code, 302)
        unused_method.refresh_from_db()
        self.assertEqual(unused_method.name, "Updated Editable Method")
        self.assertEqual(unused_method.description, "After update")
        self.assertEqual(unused_method.cost, Decimal("45000"))
        self.assertEqual(unused_method.free_shipping_threshold, Decimal("300000"))
        self.assertEqual(unused_method.estimated_delivery_days, 5)
