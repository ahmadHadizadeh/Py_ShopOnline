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
from orders.models.orders import Order

# from orders.models.orders import Order.Status
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

    STATUS_CHOICES = (
        ("all", "همه سفارش‌ها"),
        ("current", "سفارش‌های جاری"),
        ("delivered", "تکمیل شده"),
        ("canceled", "لغو شده"),
    )

    def get_queryset(self):
        queryset = Order.objects.filter(user=self.request.user).order_by("-created")

        selected_status = self.request.GET.get("status", "all").strip()

        if selected_status == "current":
            queryset = queryset.filter(
                status__in=(
                    Order.Status.PENDING,
                    Order.Status.PLACED,
                    Order.Status.PAID,
                    Order.Status.PROCESSING,
                )
            )
        elif selected_status == "delivered":
            queryset = queryset.filter(status=Order.Status.COMPLETED)
        elif selected_status == "canceled":
            queryset = queryset.filter(status=Order.Status.CANCELLED)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        selected_status = self.request.GET.get("status", "all").strip()
        valid_statuses = {status_key for status_key, _ in self.STATUS_CHOICES}

        if selected_status not in valid_statuses:
            selected_status = "all"

        context["status_choices"] = self.STATUS_CHOICES
        context["selected_status"] = selected_status

        return context


class DashboardOrderDetailView(LoginRequiredMixin, DetailView):
    model = Order
    template_name = "dashboard/order_detail.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related(
                "shipping_method",
                "address_snapshot",
            )
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.object

        context["jalali_created"] = jdatetime.datetime.fromgregorian(
            datetime=order.created
        ).strftime("%Y/%m/%d - %H:%M")

        address_snapshot = getattr(
            order,
            "address_snapshot",
            None,
        )

        if address_snapshot is not None:
            context["recipient_name"] = address_snapshot.recipient_name
        else:
            context["recipient_name"] = (
                order.user.get_full_name() or order.user.username
            )

        return context
