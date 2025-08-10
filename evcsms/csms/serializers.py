from rest_framework import serializers
from .models import ChargePoint, Transaction, User, Tenant
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.validators import UniqueValidator
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_decode

User = get_user_model()

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        if not User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("No active user with this email")
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid  = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate(self, attrs):
        try:
            uid = urlsafe_base64_decode(attrs['uid']).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, User.DoesNotExist):
            raise serializers.ValidationError("Invalid UID")

        if not default_token_generator.check_token(user, attrs['token']):
            raise serializers.ValidationError("Invalid or expired token")

        attrs['user'] = user
        return attrs

    def save(self):
        user = self.validated_data['user']
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class ChargePointSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ChargePoint
        # ↓ This is probably where the wrong names are listed
        fields = [
            "id",           # primary key
            "name",
            "connector_id",
            "status",
            "updated",      # <- correct timestamp field
            # "created",    # <- REMOVE or rename to "updated"
            # "cp_id",      # <- REMOVE (model doesn’t have this)
            "price_per_kwh",
            "price_per_hour",
            "location",
        ]
        read_only_fields = ["id", "updated"]




class TransactionSerializer(serializers.ModelSerializer):
    id      = serializers.IntegerField(source="tx_id")
    cp      = serializers.CharField(source="cp_id")
    user    = serializers.CharField(source="user_tag")
    kWh     = serializers.SerializerMethodField()
    Started = serializers.DateTimeField(source="start_time")
    Ended   = serializers.DateTimeField(source="stop_time")
    price_kwh  = serializers.DecimalField(source="price_kwh_at_start",
                                      max_digits=8, decimal_places=3,
                                      required=False)
    price_hour = serializers.DecimalField(source="price_hour_at_start",
                                          max_digits=8, decimal_places=3,
                                          read_only=True)
    total  = serializers.SerializerMethodField()

    def get_kWh(self, obj):
        # assuming `obj.kwh` returns a Decimal or float
        value = obj.kwh() if callable(obj.kwh) else obj.kwh        # ← CALL IT
        return float(value or 0)

    def get_total(self, obj):
        value = obj.total_price() if callable(obj.total_price) else obj.total_price
        return float(value) if value is not None else None

    class Meta:
        model  = Transaction
        fields = ["id","cp","user","kWh","Started","Ended","price_kwh","price_hour","total"]



class SignUpSerializer(serializers.ModelSerializer):
    # we want the raw password only on input, never on output
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    class Meta:                                   # <-- ⬅⬅⬅  **required**
        model  = User
        # change the list if you have extra required fields
        fields = ("username", "email", "password", "role")

    def create(self, validated):

        role = validated.pop("role", "root")
        password = validated.pop("password")
        validated["email"] = validated.get("email") or validated.get("username")
        user = User(role=role, **validated)
        user.set_password(password)
        user.save()

        if role == "root":                        # super-admin → make Tenant
            Tenant.objects.create(
                owner=user,
                ws_key=uuid.uuid4().hex,
            )

        return user



class UserSerializer(serializers.ModelSerializer):
    """
    Minimal serializer used by SignupView.
    • writes: email, password, role
    • reads : id, email, role
    """
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model  = User
        fields = ("id", "email", "password", "role")
        extra_kwargs = {
            "role": {"required": False, "default": "customer"},
        }

    # make sure the password is hashed
    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user



class TokenObtainPairPatchedSerializer(TokenObtainPairSerializer):
    """
    Override to add the user's role (customer / admin / root) to the
    access-token payload AND return it in the login response body.
    """
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        return token

    # optional: surface the role alongside the two JWTs
    def validate(self, attrs):
        data = super().validate(attrs)
        data["role"] = self.user.role
        return data




class MeSerializer(serializers.ModelSerializer):
    # extra read-only fields
    tenant_id = serializers.SerializerMethodField()
    tenant_ws = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ("id", "email", "role", "tenant_id", "tenant_ws")

    # helpers ---------------------------------------------------------
    def _tenant(self, obj):
        try:
            return obj.tenant          # reverse OneToOne (User → Tenant)
        except Tenant.DoesNotExist:
            return None

    def get_tenant_id(self, obj):
        t = self._tenant(obj)
        return t.id if t else None

    def get_tenant_ws(self, obj):
        """
        Return absolute WS URL, e.g.
        ws://147.93.127.215/api/v16/<ws_key>
        """
        t = self._tenant(obj)
        if not t:
            return None

        request = self.context.get("request")
        host    = request.get_host() if request else "147.93.127.215:9000"
        scheme  = "ws"   # you’ll switch to wss:// behind TLS

        return f"{scheme}://{host}/api/v16/{t.ws_key}"
