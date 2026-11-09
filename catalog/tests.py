from decimal import Decimal
# فایل: catalog/tests.py
from django.test import TestCase, Client, RequestFactory, SimpleTestCase
from django.db.models.deletion import ProtectedError
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import admin
from django.urls import reverse
from django.contrib.auth import get_user_model
from catalog.models.product import Product
from catalog.models.inventory import InventoryMovement
from catalog.models.category import Category
from catalog.models.review import Review
from catalog.models.variant import ProductVariant
from catalog.templatetags.storefront_formatting import format_price
from orders.models import Order, OrderItem, Payment
from catalog.services import (
    adjust_product_stock,
    record_physical_stock_count,
    save_product_review,
    toggle_wishlist,
)

User = get_user_model()

class BaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.category = Category.objects.create(name="Cat", slug="cat")
        self.product1 = Product.objects.create(name="P1", slug="p1", is_active=True, category=self.category)
        self.client = Client()

class ProductCatalogSearchTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.searchable_product = Product.objects.create(
            name="لپ تاپ گیمینگ",
            name_en="Gaming Notebook",
            brand="Northwind",
            description="portable workstation",
            highlights="RTX graphics",
            stock=7,
            category=self.category,
        )

    def test_search_matches_existing_product_text_fields(self):
        for query in ("لپ تاپ", "Gaming", "Northwind", "portable workstation", "RTX graphics"):
            with self.subTest(query=query):
                response = self.client.get(reverse("catalog:product_list"), {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    list(response.context["products"].values_list("pk", flat=True)),
                    [self.searchable_product.pk],
                )
                self.assertEqual(response.context["search_query"], query)

    def test_search_excludes_inactive_products(self):
        Product.objects.create(
            name="Inactive Northwind Device",
            slug="inactive-northwind-device",
            is_active=False,
            stock=5,
            category=self.category,
        )
        response = self.client.get(reverse("catalog:product_list"), {"q": "Northwind"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["products"].values_list("pk", flat=True)),
            [self.searchable_product.pk],
        )

    def test_search_keeps_selected_category_filter(self):
        other_category = Category.objects.create(name="Other Category", slug="other-category")
        Product.objects.create(
            name="Northwind Other Device",
            slug="northwind-other-device",
            brand="Northwind",
            stock=3,
            category=other_category,
        )
        response = self.client.get(
            reverse("catalog:product_list_by_category", kwargs={"category_slug": self.category.slug}),
            {"q": "Northwind"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["products"].values_list("pk", flat=True)),
            [self.searchable_product.pk],
        )

    def test_search_trims_query_and_renders_header_controls(self):
        response = self.client.get(reverse("catalog:product_list"), {"q": "  Northwind  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["search_query"], "Northwind")
        self.assertContains(response, 'id="desktop-search-form"')
        self.assertContains(response, 'id="mobile-search-panel"')
        self.assertContains(response, 'id="mobile-navigation-panel"')
        self.assertContains(response, 'name="q"')

    def test_active_categories_are_available_in_header_on_product_detail(self):
        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": self.searchable_product.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.category.get_absolute_url())

    def test_homepage_renders_visual_category_cards(self):
        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="دسته‌بندی‌های فروشگاه"')
        self.assertContains(response, self.category.get_absolute_url())
        self.assertContains(response, self.category.name)

    def test_search_suggestions_reuse_search_fields_and_category_scope(self):
        other_category = Category.objects.create(
            name="Other Suggestion Category",
            slug="other-suggestion-category",
        )
        Product.objects.create(
            name="Northwind in another category",
            slug="northwind-in-another-category",
            brand="Northwind",
            stock=3,
            category=other_category,
        )
        Product.objects.create(
            name="Inactive Northwind",
            slug="inactive-northwind",
            brand="Northwind",
            is_active=False,
            stock=3,
            category=self.category,
        )

        response = self.client.get(
            reverse("catalog:search_suggestions"),
            {"q": "Northwind", "category": self.category.slug},
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], self.searchable_product.name)
        self.assertEqual(results[0]["url"], self.searchable_product.get_absolute_url())
        self.assertEqual(results[0]["category"], self.category.name)

    def test_search_suggestions_ignore_queries_shorter_than_two_characters(self):
        response = self.client.get(
            reverse("catalog:search_suggestions"),
            {"q": "N"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

    def test_price_sorting_uses_only_supported_options(self):
        cheaper = Product.objects.create(
            name="Sortable cheaper item",
            slug="sortable-cheaper-item",
            price=Decimal("100.00"),
            stock=2,
            category=self.category,
        )
        expensive = Product.objects.create(
            name="Sortable expensive item",
            slug="sortable-expensive-item",
            price=Decimal("900.00"),
            stock=2,
            category=self.category,
        )

        ascending = self.client.get(
            reverse("catalog:product_list"),
            {"q": "Sortable", "sort": "price_asc"},
        )
        descending = self.client.get(
            reverse("catalog:product_list"),
            {"q": "Sortable", "sort": "price_desc"},
        )

        self.assertEqual(ascending.context["sort"], "price_asc")
        self.assertEqual(
            list(ascending.context["products"].values_list("pk", flat=True)),
            [cheaper.pk, expensive.pk],
        )
        self.assertEqual(
            list(descending.context["products"].values_list("pk", flat=True)),
            [expensive.pk, cheaper.pk],
        )

    def test_invalid_sort_falls_back_to_newest(self):
        response = self.client.get(
            reverse("catalog:product_list"),
            {"sort": "unsupported-order"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "newest")
        self.assertEqual(
            list(response.context["products"].values_list("pk", flat=True))[:2],
            [self.searchable_product.pk, self.product1.pk],
        )

    def test_product_list_paginates_and_preserves_search_and_sort(self):
        for index in range(13):
            Product.objects.create(
                name=f"PaginationProbe {index:02d}",
                slug=f"pagination-probe-{index:02d}",
                price=Decimal(1000 + index),
                stock=1,
                category=self.category,
            )

        response = self.client.get(
            reverse("catalog:product_list"),
            {"q": "PaginationProbe", "sort": "price_desc", "page": "1"},
        )

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["page_obj"]
        self.assertEqual(page_obj.paginator.count, 13)
        self.assertEqual(len(page_obj.object_list), 12)
        self.assertEqual(page_obj.number, 1)
        self.assertEqual(
            response.context["pagination_query"],
            "q=PaginationProbe&sort=price_desc",
        )
        self.assertContains(response, 'id="product-pagination"')
        self.assertContains(response, "page=2")

    def test_product_cards_expose_accessible_detail_links(self):
        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'aria-label="مشاهده جزئیات {self.searchable_product.name}"',
        )
        self.assertContains(response, 'id="product-grid"')
        self.assertContains(response, 'fa-regular fa-image')

    def test_category_chips_preserve_search_and_sort_query(self):
        response = self.client.get(
            reverse("catalog:product_list_by_category", kwargs={"category_slug": self.category.slug}),
            {"q": "Northwind", "sort": "price_desc", "page": "2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="/?q=Northwind&amp;sort=price_desc"',
        )


class HomepageMerchandisingTests(BaseTestCase):
    def test_homepage_hero_and_trust_strip_show_only_on_unfiltered_root(self):
        homepage = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(homepage.status_code, 200)
        self.assertContains(homepage, 'id="storefront-hero"')
        self.assertContains(homepage, 'id="storefront-hero-heading"')
        self.assertContains(homepage, 'id="storefront-trust-strip"')
        for benefit in (
            "وضعیت موجودی مشخص",
            "امتیاز و نظرات کاربران",
            "انتخاب تنوع محصول",
            "پیگیری سفارش",
        ):
            with self.subTest(benefit=benefit):
                self.assertContains(homepage, benefit)

        category_response = self.client.get(
            reverse(
                "catalog:product_list_by_category",
                kwargs={"category_slug": self.category.slug},
            )
        )
        search_response = self.client.get(
            reverse("catalog:product_list"),
            {"q": self.product1.name},
        )
        for response in (category_response, search_response):
            with self.subTest(path=response.wsgi_request.path, query=response.wsgi_request.GET):
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'id="storefront-hero"')
                self.assertNotContains(response, 'id="storefront-trust-strip"')

    def _record_order_item(
        self,
        product,
        quantity,
        *,
        order_status=Order.Status.PAID,
        payment_status=Payment.Status.SUCCESS,
        stock_reduced=True,
    ):
        order = Order.objects.create(
            user=self.user,
            status=order_status,
            stock_reduced=stock_reduced,
        )
        Payment.objects.create(
            order=order,
            user=self.user,
            amount=product.price * quantity,
            status=payment_status,
        )
        return OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
            subtotal_price=product.price * quantity,
        )

    def test_best_sellers_count_only_successful_fulfillable_sales(self):
        popular = Product.objects.create(
            name="Popular product",
            slug="popular-product",
            price=Decimal("1000"),
            stock=10,
            category=self.category,
        )
        second = Product.objects.create(
            name="Second popular product",
            slug="second-popular-product",
            price=Decimal("2000"),
            stock=10,
            category=self.category,
        )
        cancelled = Product.objects.create(
            name="Cancelled order product",
            slug="cancelled-order-product",
            price=Decimal("3000"),
            stock=10,
            category=self.category,
        )

        self._record_order_item(popular, 2)
        self._record_order_item(popular, 3, order_status=Order.Status.COMPLETED)
        self._record_order_item(second, 4)
        self._record_order_item(
            popular,
            50,
            order_status=Order.Status.PENDING,
            payment_status=Payment.Status.PENDING,
            stock_reduced=False,
        )
        self._record_order_item(
            cancelled,
            100,
            order_status=Order.Status.CANCELLED,
            payment_status=Payment.Status.REFUNDED,
            stock_reduced=False,
        )

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        best_sellers = list(response.context["best_selling_products"])
        self.assertEqual(
            [product.pk for product in best_sellers],
            [popular.pk, second.pk],
        )
        self.assertEqual(best_sellers[0].sold_quantity, 5)

    def test_homepage_showcase_products_are_not_repeated_across_sections(self):
        shared_product = Product.objects.create(
            name="Shared showcase product",
            slug="shared-showcase-product",
            price=Decimal("1000"),
            old_price=Decimal("1200"),
            stock=5,
            category=self.category,
        )
        best_seller = Product.objects.create(
            name="Exclusive best seller",
            slug="exclusive-best-seller",
            price=Decimal("1500"),
            stock=5,
            category=self.category,
        )
        special_offer = Product.objects.create(
            name="Exclusive special offer",
            slug="exclusive-special-offer",
            price=Decimal("800"),
            old_price=Decimal("1000"),
            stock=5,
            category=self.category,
        )
        self._record_order_item(shared_product, 2)
        self._record_order_item(best_seller, 1)

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        best_seller_ids = {
            product.pk for product in response.context["best_selling_products"]
        }
        special_offer_ids = {
            product.pk for product in response.context["special_offer_products"]
        }
        main_grid_ids = {
            product.pk for product in response.context["page_obj"].object_list
        }

        self.assertEqual(best_seller_ids, {shared_product.pk, best_seller.pk})
        self.assertEqual(special_offer_ids, {special_offer.pk})
        self.assertTrue(best_seller_ids.isdisjoint(special_offer_ids))
        self.assertTrue(
            (best_seller_ids | special_offer_ids).isdisjoint(main_grid_ids)
        )

    def test_homepage_with_only_showcase_products_does_not_show_empty_catalog_state(self):
        self.product1.price = Decimal("1000")
        self.product1.stock = 5
        self.product1.save()
        self._record_order_item(self.product1, 1)

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(
            [product.pk for product in response.context["best_selling_products"]],
            [self.product1.pk],
        )
        self.assertContains(response, 'id="best-selling-heading"')
        self.assertContains(
            response,
            "محصولات منتخب در بخش‌های ویژه و پرفروش‌ها نمایش داده می‌شوند.",
        )
        self.assertNotContains(
            response,
            "هنوز محصولی برای نمایش ثبت نشده است.",
        )

    def test_special_offers_require_real_discount_and_available_stock(self):
        discounted = Product.objects.create(
            name="Discounted product",
            slug="discounted-product",
            price=Decimal("800"),
            old_price=Decimal("1000"),
            stock=5,
            category=self.category,
        )
        Product.objects.create(
            name="No actual discount",
            slug="no-actual-discount",
            price=Decimal("1000"),
            old_price=Decimal("800"),
            stock=5,
            category=self.category,
        )
        Product.objects.create(
            name="Sold out discount",
            slug="sold-out-discount",
            price=Decimal("500"),
            old_price=Decimal("1000"),
            stock=0,
            category=self.category,
        )

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        offers = list(response.context["special_offer_products"])
        self.assertEqual([product.pk for product in offers], [discounted.pk])

    def test_homepage_places_curated_shelves_before_latest_product_grid(self):
        special_offer = Product.objects.create(
            name="Ordered special offer",
            slug="ordered-special-offer",
            price=Decimal("800"),
            old_price=Decimal("1000"),
            stock=5,
            category=self.category,
        )
        best_seller = Product.objects.create(
            name="Ordered best seller",
            slug="ordered-best-seller",
            price=Decimal("1200"),
            stock=5,
            category=self.category,
        )
        self._record_order_item(best_seller, 2)

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        category_nav = html.index('aria-label="دسته‌بندی‌های فروشگاه"')
        offer_heading = html.index('id="special-offers-heading"')
        bestseller_heading = html.index('id="best-selling-heading"')
        product_section = html.index('aria-label="فهرست محصولات"')
        product_grid = html.index('id="product-grid"')

        self.assertLess(category_nav, offer_heading)
        self.assertLess(offer_heading, bestseller_heading)
        self.assertLess(bestseller_heading, product_section)
        self.assertLess(product_section, product_grid)
        self.assertContains(response, 'class="category-card group')
        self.assertContains(response, "Ordered special offer")
        self.assertContains(response, "Ordered best seller")
        self.assertNotIn(
            "priority_image=True",
            html,
        )

    def test_homepage_card_rating_uses_only_approved_reviews(self):
        product = Product.objects.create(
            name="Reviewed product",
            slug="reviewed-product",
            price=Decimal("1500"),
            stock=5,
            category=self.category,
        )
        pending_user = User.objects.create_user(
            username="pending-review-user",
            password="test-password",
        )
        Review.objects.create(
            product=product,
            user=self.user,
            rating=5,
            comment="Approved review",
            is_approved=True,
        )
        Review.objects.create(
            product=product,
            user=pending_user,
            rating=1,
            comment="Pending review",
            is_approved=False,
        )

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        product_row = next(
            row for row in response.context["page_obj"].object_list
            if row.pk == product.pk
        )
        self.assertEqual(product_row.review_count, 1)
        self.assertEqual(product_row.avg_rating, Decimal("5"))
        self.assertContains(response, 'fa-solid fa-star text-yellow-400')

    def test_homepage_product_card_formats_current_and_reference_prices(self):
        Product.objects.create(
            name="Grouped price product",
            slug="grouped-price-product",
            price=Decimal("1234567.50"),
            old_price=Decimal("1500000.50"),
            stock=4,
            category=self.category,
        )

        response = self.client.get(reverse("catalog:product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-price-role="current" class="product-price text-xl font-black tabular-nums text-indigo-700">1,234,568</span>',
        )
        self.assertContains(
            response,
            'data-price-role="reference" class="mb-0.5 text-xs font-medium text-slate-400 line-through">1,500,001</span>',
        )


class ReviewServiceTests(BaseTestCase):
    def test_save_review_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "برای ثبت نظر ابتدا وارد حساب کاربری شوید."):
            save_product_review(None, self.product1, 5, "test")

class WishlistServiceTests(BaseTestCase):
    def test_toggle_wishlist_unauthenticated_user(self):
        with self.assertRaisesMessage(ValueError, "کاربر احراز هویت نشده است."):
            toggle_wishlist(None, self.product1.pk)

class ProductDetailViewTests(BaseTestCase):
    def test_product_detail_shows_discount_when_reference_price_exceeds_base_price(self):
        product = Product.objects.create(
            name="Discounted Detail Product",
            slug="discounted-detail-product",
            price=Decimal("1500.00"),
            old_price=Decimal("2000.00"),
            stock=5,
            category=self.category,
        )

        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": product.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-old-price-display"')
        self.assertContains(response, 'data-old-price="2000.00"')
        self.assertContains(
            response,
            'class="text-base text-gray-400 line-through"',
        )
        self.assertContains(response, 'id="product-discount-badge"')
        self.assertContains(
            response,
            'class="bg-rose-500 text-white text-[10px] font-extrabold px-2 py-0.5 rounded-full"',
        )

    def test_product_detail_hides_discount_when_reference_price_is_not_higher(self):
        for index, base_price in enumerate(("2000.00", "2100.00")):
            with self.subTest(base_price=base_price):
                product = Product.objects.create(
                    name="No Discount Detail Product",
                    slug=f"no-discount-detail-product-{index}",
                    price=Decimal(base_price),
                    old_price=Decimal("2000.00"),
                    stock=5,
                    category=self.category,
                )

                response = self.client.get(
                    reverse("catalog:product_detail", kwargs={"slug": product.slug})
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="product-old-price-display"')
                self.assertContains(response, 'data-old-price="2000.00"')
                self.assertContains(
                    response,
                    'class="text-base text-gray-400 line-through hidden"',
                )
                self.assertContains(response, 'id="product-discount-badge"')
                self.assertContains(
                    response,
                    'class="bg-rose-500 text-white text-[10px] font-extrabold px-2 py-0.5 rounded-full hidden"',
                )

    def test_product_detail_does_not_render_discount_without_reference_price(self):
        product = Product.objects.create(
            name="No Reference Price Product",
            slug="no-reference-price-product",
            price=Decimal("1500.00"),
            stock=5,
            category=self.category,
        )

        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": product.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="product-old-price-display"')
        self.assertNotContains(response, 'id="product-discount-badge"')

    def test_product_detail_renders_required_variant_selector(self):
        product = Product.objects.create(
            name="Variant Product",
            slug="variant-product",
            is_active=True,
            stock=10,
            category=self.category,
        )
        variant = ProductVariant.objects.create(
            product=product,
            name="رنگ",
            value="قرمز",
            price_adjustment=150,
        )

        url = reverse(
            "catalog:product_detail",
            kwargs={"slug": product.slug},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-variant"')
        self.assertContains(response, 'name="variant_id"')
        self.assertContains(response, f'value="{variant.pk}"')
        self.assertContains(response, 'required')

    def test_product_detail_uses_cart_availability_rule_not_stock_alone(self):
        product = Product.objects.create(
            name="Unavailable Status Product",
            slug="unavailable-status-product",
            is_active=True,
            stock=5,
            category=self.category,
        )
        # Simulate a persisted availability flag that disagrees with positive stock.
        Product.objects.filter(pk=product.pk).update(is_available_status=False)

        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": product.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["product"].is_available)
        self.assertContains(response, "ناموجود")
        self.assertNotContains(response, "موجود در انبار (5)")
        self.assertNotContains(
            response,
            f'action="{reverse("cart:add", args=[product.pk])}"',
        )

    def test_guest_product_detail_uses_external_javascript_without_inline_handlers(self):
        product = Product.objects.create(
            name="Guest Detail Product",
            slug="guest-detail-product",
            is_active=True,
            stock=2,
            category=self.category,
        )
        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": product.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/js/storefront/details.js"')
        self.assertContains(response, 'data-quantity-action="decrease"')
        self.assertContains(response, 'data-quantity-action="increase"')
        self.assertContains(
            response,
            f'data-endpoint="{reverse("catalog:wishlist_toggle", kwargs={"slug": product.slug})}"',
        )
        self.assertNotContains(response, "onclick=")
        self.assertNotRegex(response.content.decode("utf-8"), r"<script(?![^>]*src=)")
        self.assertNotContains(response, 'id="review-form"')

    def test_authenticated_product_detail_keeps_review_form_with_external_script(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("catalog:product_detail", kwargs={"slug": self.product1.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/js/storefront/details.js"')
        self.assertContains(response, 'id="review-form"')
        self.assertContains(response, 'id="rating-input"')
        self.assertContains(response, 'id="reviews-container"')

    def test_product_detail_post_review_unauthenticated(self):
        url = reverse("catalog:product_detail", kwargs={"slug": self.product1.slug})
        response = self.client.post(url, {"rating": 5, "comment": "test"})
        # در اینجا چون 'login' ندارید، انتظار داریم خطای 403 یا 401 بدهد یا به صفحه اصلی برود
        # تست فقط صحت عدم ثبت نظر را چک می‌کند
        self.assertEqual(Review.objects.count(), 0)

class OtpCsrfIntegrationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Cat OTP", slug="cat-otp")
        self.product = Product.objects.create(
            name="OTP Product",
            slug="otp-product",
            is_active=True,
            category=self.category,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse("catalog:product_detail", kwargs={"slug": self.product.slug})

    def test_otp_page_bootstraps_csrf_cookie_and_send_otp_accepts_csrf(self):
        host = "otp-test.loca.lt"

        response = self.client.get(
            self.url,
            secure=True,
            HTTP_HOST=host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

        csrf_token = response.cookies["csrftoken"].value
        otp_response = self.client.post(
            reverse("accounts:send_otp"),
            data='{"phone":"09123456789"}',
            content_type="application/json",
            secure=True,
            HTTP_HOST=host,
            HTTP_ORIGIN=f"https://{host}",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(otp_response.status_code, 200)
        self.assertEqual(otp_response.json().get("status"), "success")


class CategoryRootValidationRegressionTests(TestCase):
    def test_new_root_category_without_parent_validates(self):
        category = Category(name="Regression root", slug="regression-root")
        category.full_clean()
        self.assertIsNone(category.parent_id)



class ProductVariantPriceValidationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="Variant Price Category",
            slug="variant-price-category",
        )
        self.product = Product.objects.create(
            name="Variant Price Product",
            slug="variant-price-product",
            price=Decimal("100.00"),
            stock=5,
            category=self.category,
        )

    def test_variant_full_clean_rejects_negative_final_price(self):
        variant = ProductVariant(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=Decimal("-100.01"),
        )

        with self.assertRaises(ValidationError) as raised:
            variant.full_clean()

        self.assertIn("price_adjustment", raised.exception.message_dict)

    def test_variant_full_clean_allows_zero_final_price_and_negative_adjustment(self):
        variant = ProductVariant(
            product=self.product,
            name="رنگ",
            value="آبی",
            price_adjustment=Decimal("-100.00"),
        )

        self.assertIsNone(variant.full_clean())

    def test_product_full_clean_rejects_price_change_that_makes_variant_negative(self):
        ProductVariant.objects.create(
            product=self.product,
            name="حافظه",
            value="نسخه گران‌تر",
            price_adjustment=Decimal("-150.00"),
        )
        self.product.price = Decimal("149.99")

        with self.assertRaises(ValidationError) as raised:
            self.product.full_clean()

        self.assertIn("price", raised.exception.message_dict)

    def test_product_full_clean_allows_price_change_that_keeps_variant_at_zero(self):
        ProductVariant.objects.create(
            product=self.product,
            name="حافظه",
            value="نسخه",
            price_adjustment=Decimal("-150.00"),
        )
        self.product.price = Decimal("150.00")

        self.assertIsNone(self.product.full_clean())

    def test_variant_used_in_cart_cannot_be_reassigned_to_another_product(self):
        from cart.models import Cart, CartItem

        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="مشکی",
            price_adjustment=Decimal("0.00"),
        )
        other_product = Product.objects.create(
            name="Other Variant Product",
            slug="other-variant-product",
            price=Decimal("200.00"),
            stock=5,
            category=self.category,
        )
        cart = Cart.objects.create(session_key="variant-price-validation")
        CartItem.objects.create(
            cart=cart,
            product=self.product,
            variant=variant,
            quantity=1,
            unit_price_snapshot=Decimal("100.00"),
            status="saved",
        )
        variant.product = other_product

        with self.assertRaises(ValidationError) as raised:
            variant.full_clean()

        self.assertIn("product", raised.exception.message_dict)


class ProductVariantPriceAdminValidationTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="variant-price-admin",
            email="variant-price-admin@example.test",
            password="TestPassword123!",
        )
        self.request = RequestFactory().post("/admin/")
        self.request.user = self.admin_user
        self.category = Category.objects.create(
            name="Variant Admin Category",
            slug="variant-admin-category",
        )
        self.product = Product.objects.create(
            name="Variant Admin Product",
            slug="variant-admin-product",
            price=Decimal("100.00"),
            stock=5,
            category=self.category,
        )

    def _product_form_data(self, *, price):
        return {
            "name": self.product.name,
            "name_en": self.product.name_en or "",
            "brand": self.product.brand or "",
            "slug": self.product.slug,
            "image": "",
            "description": self.product.description,
            "review": self.product.review,
            "price": str(price),
            "old_price": "",
            "stock": str(self.product.stock),
            "is_active": "on" if self.product.is_active else "",
            "highlights": self.product.highlights,
            "guarantee": self.product.guarantee,
            "delivery_info": self.product.delivery_info,
            "category": str(self.product.category_id),
            "related_products": [],
        }

    def test_product_admin_form_rejects_base_price_that_makes_existing_variant_negative(self):
        ProductVariant.objects.create(
            product=self.product,
            name="حافظه",
            value="نسخه",
            price_adjustment=Decimal("-150.00"),
        )
        product_admin = admin.site._registry[Product]
        form_class = product_admin.get_form(self.request, self.product)
        form = form_class(
            data=self._product_form_data(price=Decimal("50.00")),
            instance=self.product,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("price", form.errors)

    def test_variant_admin_form_rejects_negative_final_price(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="قرمز",
            price_adjustment=Decimal("-50.00"),
        )
        variant_admin = admin.site._registry[ProductVariant]
        form_class = variant_admin.get_form(self.request, variant)
        form = form_class(
            data={
                "product": str(self.product.pk),
                "name": variant.name,
                "value": variant.value,
                "price_adjustment": "-100.01",
            },
            instance=variant,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("price_adjustment", form.errors)

    def test_variant_admin_form_allows_zero_final_price(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="آبی",
            price_adjustment=Decimal("-50.00"),
        )
        variant_admin = admin.site._registry[ProductVariant]
        form_class = variant_admin.get_form(self.request, variant)
        form = form_class(
            data={
                "product": str(self.product.pk),
                "name": variant.name,
                "value": variant.value,
                "price_adjustment": "-100.00",
            },
            instance=variant,
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_variant_admin_form_blocks_reassignment_when_variant_is_in_cart(self):
        from cart.models import Cart, CartItem

        variant = ProductVariant.objects.create(
            product=self.product,
            name="رنگ",
            value="سبز",
            price_adjustment=Decimal("0.00"),
        )
        other_product = Product.objects.create(
            name="Another Variant Admin Product",
            slug="another-variant-admin-product",
            price=Decimal("200.00"),
            stock=5,
            category=self.category,
        )
        cart = Cart.objects.create(session_key="variant-price-admin-validation")
        CartItem.objects.create(
            cart=cart,
            product=self.product,
            variant=variant,
            quantity=1,
            unit_price_snapshot=Decimal("100.00"),
            status="saved",
        )

        variant_admin = admin.site._registry[ProductVariant]
        form_class = variant_admin.get_form(self.request, variant)
        form = form_class(
            data={
                "product": str(other_product.pk),
                "name": variant.name,
                "value": variant.value,
                "price_adjustment": "0.00",
            },
            instance=variant,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("product", form.errors)


class ProductAdminUsabilityTests(TestCase):
    def test_product_fields_are_grouped_and_derived_status_is_read_only(self):
        product_admin = admin.site._registry[Product]

        self.assertEqual(
            tuple(product_admin.readonly_fields),
            ("stock", "is_available_status", "created", "updated", "inventory_management_link"),
        )
        self.assertEqual(tuple(product_admin.list_editable), ())
        self.assertEqual(
            tuple(title for title, _options in product_admin.fieldsets),
            (
                "اطلاعات اصلی محصول",
                "قیمت و موجودی",
                "محتوای صفحه محصول",
                "ارسال و گارانتی",
                "تنظیمات تکمیلی",
            ),
        )

        configured_fields = {
            field
            for _title, options in product_admin.fieldsets
            for field in options["fields"]
        }
        self.assertSetEqual(
            configured_fields,
            {
                "name",
                "name_en",
                "brand",
                "category",
                "image",
                "price",
                "old_price",
                "stock",
                "is_active",
                "is_available_status",
                "description",
                "highlights",
                "review",
                "guarantee",
                "delivery_info",
                "slug",
                "related_products",
                "created",
                "updated",
            },
        )

    def test_product_admin_labels_and_help_text_are_persian_for_sellers(self):
        admin_user = User.objects.create_superuser(
            username="product-admin-labels",
            email="product-admin-labels@example.test",
            password="TestPassword123!",
        )
        category = Category.objects.create(
            name="Seller Product Category",
            slug="seller-product-category",
        )
        product = Product.objects.create(
            category=category,
            name="Seller Product",
            slug="seller-product",
            price=Decimal("100000"),
            stock=2,
        )
        self.client.force_login(admin_user)

        list_response = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "دسته‌بندی")
        self.assertContains(list_response, category.name)
        self.assertNotContains(list_response, ">CATEGORY<")

        form_response = self.client.get(
            reverse("admin:catalog_product_change", args=[product.pk])
        )
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, "دسته‌بندی")
        self.assertContains(form_response, "تصویر اصلی محصول")
        self.assertContains(form_response, "نشانی محصول")
        self.assertContains(form_response, "برای انتخاب چند محصول")
        self.assertContains(form_response, "مدیریت موجودی و ثبت شمارش فیزیکی")
        self.assertNotContains(form_response, ">Category<")
        self.assertNotContains(form_response, ">Image<")
        self.assertNotContains(form_response, ">Slug<")
        self.assertNotIn("stock", form_response.context["adminform"].form.fields)



class InventoryMovementAdminTests(TestCase):
    def test_inventory_movement_admin_is_read_only(self):
        admin_user = User.objects.create_superuser(
            username="inventory-audit-admin",
            email="inventory-audit-admin@example.test",
            password="TestPassword123!",
        )
        request = RequestFactory().get("/admin/catalog/inventorymovement/")
        request.user = admin_user
        movement_admin = admin.site._registry[InventoryMovement]

        self.assertTrue(movement_admin.has_view_permission(request))
        self.assertFalse(movement_admin.has_add_permission(request))
        self.assertFalse(movement_admin.has_change_permission(request))
        self.assertFalse(movement_admin.has_delete_permission(request))


class InventoryServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(
            username="inventory-manager",
            email="inventory-manager@example.test",
            password="TestPassword123!",
        )
        self.category = Category.objects.create(
            name="Inventory Category",
            slug="inventory-category",
        )
        self.product = Product.objects.create(
            name="Inventory Product",
            slug="inventory-product",
            category=self.category,
            stock=5,
            is_active=True,
        )

    def test_relative_increase_and_decrease_record_auditable_movements(self):
        product, increase = adjust_product_stock(
            product_id=self.product.pk,
            delta=3,
            reason="دریافت محموله جدید",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 8)
        self.assertEqual(increase.quantity_before, 5)
        self.assertEqual(increase.quantity_after, 8)
        self.assertEqual(increase.delta, 3)
        self.assertEqual(increase.source, InventoryMovement.Source.MANUAL_ADJUSTMENT)
        self.assertEqual(increase.actor, self.manager)
        self.assertEqual(increase.actor_label, self.manager.get_username())

        product, decrease = adjust_product_stock(
            product_id=self.product.pk,
            delta=-2,
            reason="ثبت کالای آسیب‌دیده",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 6)
        self.assertEqual(decrease.quantity_before, 8)
        self.assertEqual(decrease.quantity_after, 6)
        self.assertEqual(decrease.delta, -2)

        self.assertEqual(
            InventoryMovement.objects.filter(product=self.product).count(),
            2,
        )

    def test_decrease_to_zero_updates_derived_availability(self):
        product, movement = adjust_product_stock(
            product_id=self.product.pk,
            delta=-5,
            reason="خروج کامل موجودی",
            actor=self.manager,
        )

        self.assertEqual(product.stock, 0)
        self.assertFalse(product.is_available_status)
        self.assertFalse(product.is_available)
        self.assertEqual(movement.quantity_after, 0)

    def test_adjustment_cannot_make_stock_negative_or_create_a_movement(self):
        with self.assertRaises(ValidationError):
            adjust_product_stock(
                product_id=self.product.pk,
                delta=-6,
                reason="کاهش بیش از موجودی",
                actor=self.manager,
            )

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_zero_manual_adjustment_and_empty_reason_are_rejected(self):
        for delta, reason in ((0, "عملیات صفر"), (1, "   ")):
            with self.subTest(delta=delta, reason=reason):
                with self.assertRaises(ValidationError):
                    adjust_product_stock(
                        product_id=self.product.pk,
                        delta=delta,
                        reason=reason,
                        actor=self.manager,
                    )

        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_physical_count_records_delta_and_zero_delta_count(self):
        product, correction = record_physical_stock_count(
            product_id=self.product.pk,
            physical_count=3,
            reason="شمارش پایان شیفت",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 3)
        self.assertEqual(correction.delta, -2)
        self.assertEqual(correction.source, InventoryMovement.Source.PHYSICAL_COUNT)

        product, confirmation = record_physical_stock_count(
            product_id=self.product.pk,
            physical_count=3,
            reason="تأیید شمارش مجدد",
            actor=self.manager,
        )
        self.assertEqual(product.stock, 3)
        self.assertEqual(confirmation.delta, 0)
        self.assertEqual(confirmation.quantity_before, 3)
        self.assertEqual(confirmation.quantity_after, 3)
        self.assertEqual(InventoryMovement.objects.count(), 2)

    def test_physical_count_rejects_negative_or_non_integer_values(self):
        for physical_count in (-1, 1.5, True):
            with self.subTest(physical_count=physical_count):
                with self.assertRaises(ValidationError):
                    record_physical_stock_count(
                        product_id=self.product.pk,
                        physical_count=physical_count,
                        reason="شمارش فیزیکی",
                        actor=self.manager,
                    )

        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_stock_operations_require_product_change_permission(self):
        unprivileged_user = User.objects.create_user(
            username="inventory-no-permission",
            password="TestPassword123!",
        )

        with self.assertRaises(PermissionDenied):
            adjust_product_stock(
                product_id=self.product.pk,
                delta=1,
                reason="نباید اجرا شود",
                actor=unprivileged_user,
            )

        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_product_deactivation_remains_effective_after_stock_increase(self):
        inactive_product = Product.objects.create(
            name="Inactive Inventory Product",
            slug="inactive-inventory-product",
            category=self.category,
            stock=0,
            is_active=False,
        )

        product, _movement = adjust_product_stock(
            product_id=inactive_product.pk,
            delta=4,
            reason="ورود موجودی برای محصول غیرفعال",
            actor=self.manager,
        )

        self.assertEqual(product.stock, 4)
        self.assertFalse(product.is_available_status)
        self.assertFalse(product.is_available)



class InventoryAdminOperationViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(
            username="inventory-page-manager",
            email="inventory-page-manager@example.test",
            password="TestPassword123!",
        )
        category = Category.objects.create(
            name="Inventory Page Category",
            slug="inventory-page-category",
        )
        self.product = Product.objects.create(
            name="Inventory Page Product",
            slug="inventory-page-product",
            category=category,
            stock=5,
            is_active=True,
        )
        self.client.force_login(self.manager)
        self.url = reverse(
            "admin:catalog_product_inventory",
            args=[self.product.pk],
        )

    def test_inventory_page_displays_operations_and_recent_history(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "موجودی ثبت‌شده در سامانه")
        self.assertContains(response, "افزایش موجودی (ورود کالا)")
        self.assertContains(response, "کاهش موجودی")
        self.assertContains(response, "ثبت شمارش فیزیکی")
        self.assertContains(response, "آخرین گردش‌های موجودی")

    def test_inventory_page_posts_relative_increase_and_shows_history(self):
        response = self.client.post(
            self.url,
            {
                "operation": "increase",
                "quantity": "4",
                "reason": "دریافت محموله تأمین‌کننده",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)

        movement = InventoryMovement.objects.get(product=self.product)
        self.assertEqual(movement.delta, 4)
        self.assertEqual(movement.quantity_before, 5)
        self.assertEqual(movement.quantity_after, 9)
        self.assertEqual(movement.actor, self.manager)

        history_response = self.client.get(self.url)
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "دریافت محموله تأمین‌کننده")

    def test_inventory_page_allows_zero_physical_count(self):
        response = self.client.post(
            self.url,
            {
                "operation": "physical_count",
                "quantity": "0",
                "reason": "شمارش قفسه و تأیید اتمام موجودی",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        movement = InventoryMovement.objects.get(product=self.product)
        self.assertEqual(movement.source, InventoryMovement.Source.PHYSICAL_COUNT)
        self.assertEqual(movement.delta, -5)

    def test_inventory_page_rejects_zero_relative_adjustment(self):
        response = self.client.post(
            self.url,
            {
                "operation": "decrease",
                "quantity": "0",
                "reason": "تعداد نامعتبر",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تعداد باید بیشتر از صفر باشد.")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)

    def test_inventory_page_denies_staff_without_product_change_permission(self):
        read_only_staff = User.objects.create_user(
            username="inventory-read-only-staff",
            password="TestPassword123!",
            is_staff=True,
        )
        self.client.force_login(read_only_staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(InventoryMovement.objects.count(), 0)



class CategoryAdminImageTests(TestCase):
    def test_category_image_is_optional_and_available_in_admin(self):
        image_field = Category._meta.get_field("image")
        self.assertTrue(image_field.blank)
        self.assertEqual(image_field.default, "")

        category_admin = admin.site._registry[Category]
        request = RequestFactory().get("/admin/catalog/category/add/")
        fieldsets = category_admin.get_fieldsets(request)
        configured_fields = [
            field_name
            for _, options in fieldsets
            for field_name in options.get("fields", ())
        ]
        self.assertIn("image", configured_fields)


class CategoryAdminDeletionSafetyTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="category-delete-safety-admin",
            email="category-delete-safety@example.test",
            password="TestPassword123!",
        )
        self.client.force_login(self.admin_user)
        self.category = Category.objects.create(
            name="Category With Products",
            slug="category-with-products",
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Protected Category Product",
            slug="protected-category-product",
            price=Decimal("120000"),
            stock=2,
            is_active=True,
        )

    def test_category_with_products_cannot_be_deleted_from_admin(self):
        response = self.client.post(
            reverse("admin:catalog_category_delete", args=[self.category.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_empty_category_remains_deletable(self):
        empty_category = Category.objects.create(
            name="Empty Category",
            slug="empty-category",
        )

        response = self.client.post(
            reverse("admin:catalog_category_delete", args=[empty_category.pk]),
            {"post": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=empty_category.pk).exists())

    def test_category_with_products_cannot_be_bulk_deleted_from_admin(self):
        response = self.client.post(
            reverse("admin:catalog_category_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(self.category.pk)],
                "index": "0",
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())



class ProductCategoryModelProtectionTests(TestCase):
    def test_category_with_products_is_protected_outside_admin(self):
        category = Category.objects.create(
            name="ORM Protected Category",
            slug="orm-protected-category",
        )
        product = Product.objects.create(
            category=category,
            name="ORM Protected Product",
            slug="orm-protected-product",
            price=Decimal("1000"),
            stock=1,
            is_active=True,
        )

        with self.assertRaises(ProtectedError):
            category.delete()

        self.assertTrue(Category.objects.filter(pk=category.pk).exists())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

class StorefrontPriceFormattingTests(SimpleTestCase):
    def test_decimal_amounts_are_grouped_and_rounded_half_up(self):
        cases = (
            (Decimal("489000000.00"), "489,000,000"),
            (Decimal("489000000.50"), "489,000,001"),
            (Decimal("1234567.49"), "1,234,567"),
            (Decimal("1234567.50"), "1,234,568"),
            (Decimal("0.49"), "0"),
            (Decimal("0.50"), "1"),
        )

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(format_price(value), expected)

    def test_missing_or_invalid_values_have_safe_fallbacks(self):
        self.assertEqual(format_price(None), "—")
        self.assertEqual(format_price("not-a-number"), "not-a-number")
        self.assertEqual(format_price(Decimal("NaN")), "NaN")

