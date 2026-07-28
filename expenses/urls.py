from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'expenses', views.ExpenseViewSet, basename='expense')
router.register(r'branches', views.BranchViewSet, basename='branch')
router.register(r'petty-cash-debits', views.PettyCashDebitViewSet, basename='petty-cash-debit')
router.register(r'invoices', views.InvoiceViewSet, basename='invoice')
router.register(r'delivery-challans', views.DeliveryChallanViewSet, basename='delivery-challan')
router.register(r'purchase-bills', views.PurchaseBillViewSet, basename='purchase-bill')
router.register(r'purchase-orders', views.PurchaseOrderViewSet, basename='purchase-order')
router.register(r'payment-receipts', views.PaymentReceiptViewSet, basename='payment-receipt')
router.register(r'quotes', views.QuoteViewSet, basename='quote')
router.register(r'bills-of-supply', views.BillOfSupplyViewSet, basename='bill-of-supply')
router.register(r'tax-invoices', views.TaxInvoiceViewSet, basename='tax-invoice')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('petty-cash/summary/', views.petty_cash_summary, name='petty-cash-summary'),
    path('export/', views.export_expenses, name='export'),
    path('import/', views.import_expenses, name='import'),
    path('categories/', views.categories_view, name='categories'),
    path('profit-loss/', views.profit_loss_view, name='profit-loss'),
    path('payment-mode-balances/', views.payment_mode_balances_view, name='payment-mode-balances'),
    path('payment-mode-balances/set/', views.payment_mode_balance_set, name='payment-mode-balance-set'),
    path('payment-mode-balances/delete/', views.payment_mode_balance_delete, name='payment-mode-balance-delete'),
    # Billing Reminders
    path('billing-reminders/', views.billing_reminders_list, name='billing-reminders-list'),
    path('billing-reminders/create/', views.billing_reminder_create, name='billing-reminder-create'),
    path('billing-reminders/<int:pk>/update/', views.billing_reminder_update, name='billing-reminder-update'),
    path('billing-reminders/<int:pk>/toggle-paid/', views.billing_reminder_toggle_paid, name='billing-reminder-toggle-paid'),
    path('billing-reminders/<int:pk>/delete/', views.billing_reminder_delete, name='billing-reminder-delete'),
    path('auth/login/', views.login_view, name='auth-login'),
    path('auth/logout/', views.logout_view, name='auth-logout'),
    path('auth/me/', views.me_view, name='auth-me'),
    # Admin: user management
    path('admin/sections/', views.admin_sections_view, name='admin-sections'),
    path('admin/clear-data-password/', views.clear_data_password_view, name='admin-clear-data-password'),
    path('admin/users/', views.admin_users_view, name='admin-users'),
    path('admin/users/<int:pk>/', views.admin_user_detail_view, name='admin-user-detail'),
]
