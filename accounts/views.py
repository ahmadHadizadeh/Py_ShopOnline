from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import (
    ListView,
    CreateView,
    UpdateView,
    DeleteView,
    DetailView,
)
from .models.address import Address
from .forms import AddressForm, PROVINCES_AND_CITIES
from django.http import JsonResponse
from django.views import View
from orders.models import Order
import jdatetime


class AddressListView(LoginRequiredMixin, ListView):
    model = Address
    template_name = "accounts/address_list.html"
    context_object_name = "addresses"

    def get_queryset(self):
        # کاربر فقط آدرس‌های خودش را می‌بیند
        return Address.objects.filter(user=self.request.user).order_by(
            "-is_default", "-created"
        )


class AddressCreateView(LoginRequiredMixin, CreateView):
    model = Address
    form_class = AddressForm
    template_name = "accounts/address_form.html"
    success_url = reverse_lazy("accounts:address_list")

    def form_valid(self, form):
        # اختصاص دادن کاربر به آدرس در لحظه ثبت
        form.instance.user = self.request.user
        return super().form_valid(form)


class AddressUpdateView(LoginRequiredMixin, UpdateView):
    model = Address
    form_class = AddressForm
    template_name = "accounts/address_form.html"
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        # جلوگیری از دسترسی به آدرس دیگران (IDOR Prevention)
        return Address.objects.filter(user=self.request.user)


class AddressDeleteView(LoginRequiredMixin, DeleteView):
    model = Address
    template_name = "accounts/address_confirm_delete.html"
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        # امنیت در حذف
        return Address.objects.filter(user=self.request.user)


class CitiesByProvinceView(View):
    def get(self, request, *args, **kwargs):
        province = request.GET.get("province", "").strip()
        cities = PROVINCES_AND_CITIES.get(province, [])

        return JsonResponse({"cities": [{"id": city, "text": city} for city in cities]})


class DashboardOrderListView(LoginRequiredMixin, ListView):
    model = Order
    template_name = "dashboard/order_list.html"
    context_object_name = "orders"
    paginate_by = 10

    STATUS_MAP = {
        "current": "pending",
        "delivered": "delivered",
        "returned": "returned",
        "cancelled": "cancelled",
    }

    STATUS_CHOICES = [
        ("current", "جاری"),
        ("delivered", "تحویل شده"),
        ("returned", "مرجوع شده"),
        ("cancelled", "لغو شده"),
    ]

    def get_queryset(self):
        status = self.request.GET.get("status", "current")
        return Order.objects.filter(
            user=self.request.user,
            status=self.STATUS_MAP.get(status, "pending"),
        ).order_by("-created")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for order in context["orders"]:
            order.jalali_created = jdatetime.date.fromgregorian(
                date=order.created.date()
            ).strftime("%Y/%m/%d")
        context["status_choices"] = self.STATUS_CHOICES
        return context


class DashboardOrderDetailView(LoginRequiredMixin, DetailView):
    model = Order
    template_name = "dashboard/order_detail.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_queryset(self):
        # اضافه کردن prefetch_related و select_related برای جلوگیری از N+1
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("address_snapshot")
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.object

        # محاسبه تاریخ شمسی
        order.jalali_created = jdatetime.date.fromgregorian(
            date=order.created.date()
        ).strftime("%Y/%m/%d")

        # برای دسترسی آسان به نام تحویل گیرنده، آن را مستقیم در context قرار می دهیم
        if order.address_snapshot:
            context["recipient_name"] = order.address_snapshot.recipient_name
        else:
            # Fallback به نام کاربر در صورتی که snapshot آدرس موجود نباشد
            context["recipient_name"] = (
                order.user.get_full_name() or order.user.username
            )

        return context
