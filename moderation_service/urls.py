from __future__ import annotations

from django.contrib import admin
from django.urls import path

from moderation import views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/product-blocking-reasons", views.blocking_reasons, name="product-blocking-reasons"),
    path("api/v1/blocking-reasons", views.blocking_reasons, name="blocking-reasons"),
    path("api/v1/blocking-reasons/<uuid:reason_id>", views.blocking_reason_detail, name="blocking-reason-detail"),
    path("api/v1/tickets/<uuid:ticket_id>/approve", views.approve_ticket, name="approve-ticket"),
    path("api/v1/products/<uuid:product_id>/approve", views.approve_product, name="approve-product"),
]
