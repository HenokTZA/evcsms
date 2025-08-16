from django.urls import path, include
from rest_framework_simplejwt.views import (
    TokenObtainPairView, TokenRefreshView
)
from csms import views
from django.urls import path
from . import views
from .views      import SignupView, LoginView, ChargePointList, TransactionList, ChargePointDetail, PasswordResetRequestView, PasswordResetConfirmView
from .views import GenerateReportView, LogoutView
#from .views_reports import GenerateReportView

urlpatterns = [
    path("auth/signup/", views.SignupView.as_view(), name="signup"),
    path("auth/login/",  views.LoginView.as_view(),  name="login"),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("charge-points/", ChargePointList.as_view(), name="charge-points"),
    path("sessions/",      TransactionList.as_view(), name="sessions"),
    path("me/", views.MeView.as_view(), name="me"),
    path('auth/password/reset/', PasswordResetRequestView.as_view(), name='password_reset'),
    path('auth/password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path("reports/", GenerateReportView.as_view(), name="generate-report"),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path("charge-points/", views.ChargePointList.as_view(), name="cp-list"),
    path("charge-points/<slug:pk>/", views.ChargePointDetail.as_view(), name="cp-detail"),
    path("charge-points/by-code/<slug:cp_id>/", views.ChargePointByCode.as_view(), name="cp-by-code"),
    path("charge-points/<slug:pk>/command/", views.ChargePointCommand.as_view(), name="cp-command"),
]


