from django.urls import path, include
from rest_framework_simplejwt.views import (
    TokenObtainPairView, TokenRefreshView
)
from csms import views
from django.urls import path
from . import views
from .views      import SignupView, LoginView, ChargePointList, TransactionList, ChargePointDetail

urlpatterns = [
    path("auth/signup/", views.SignupView.as_view(), name="signup"),
    path("auth/login/",  views.LoginView.as_view(),  name="login"),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("charge-points/", ChargePointList.as_view(), name="charge-points"),
    path("sessions/",      TransactionList.as_view(), name="sessions"),
    path("me/", views.MeView.as_view(), name="me"),
]

urlpatterns += [
    path("charge-points/<pk>/",                    # GET single CP
         views.ChargePointDetail.as_view()),

    path("charge-points/<pk>/command/",            # POST command
         views.ChargePointCommand.as_view()),
]
