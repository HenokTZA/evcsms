from rest_framework import serializers
from .models import ChargePoint, Transaction, User, Tenant
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.validators import UniqueValidator
import uuid
from django.conf import settings

User = get_user_model()

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
        ]
        read_only_fields = ["id", "updated"]




class TransactionSerializer(serializers.ModelSerializer):
    id      = serializers.IntegerField(source="tx_id")
    cp      = serializers.CharField(source="cp_id")
    user    = serializers.CharField(source="user_tag")
    kWh     = serializers.SerializerMethodField()
    Started = serializers.DateTimeField(source="start_time")
    Ended   = serializers.DateTimeField(source="stop_time")

    def get_kWh(self, obj):
        # assuming `obj.kwh` returns a Decimal or float
        return float(obj.kwh or 0)

    class Meta:
        model  = Transaction
        fields = ["id","cp","user","kWh","Started","Ended"]

"""
class SignUpSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model  = User
        fields = ("username", "email", "password", "role")

    def create(self, validated):
        role = validated.pop("role", "customer")           # customers self-register
        user = User(**validated, role=role)
        user.set_password(validated["password"])
        user.save()
        return user

"""
"""
class SignUpSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model  = User
        fields = ("id", "email", "password", "role")

    def create(self, validated):
        # put the email into username so JWT (which expects username) works
        user = User(
            username = validated["email"],
            email    = validated["email"],
            role     = validated.get("role", "customer"),
        )
        user.set_password(validated["password"])
        user.save()
        return user
"""

"""
class SignUpSerializer(serializers.ModelSerializer):
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role     = serializers.ChoiceField(
        choices=User.ROLE_CHOICES,
        default=User.ROLE_CHOICES[0][0]
    )

    class Meta:
        model  = User
        fields = ("email", "password", "role")

    def validate_email(self, email):
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return email

    def create(self, validated_data):
        # Use email as username (must be unique)
        user = User(
            username=validated_data["email"],
            email=validated_data["email"],
            role=validated_data["role"],
        )
        user.set_password(validated_data["password"])
        user.save()
        return user
"""


"""
class SignUpSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = ("username", "email", "password", "role")

    def create(self, data):
        pwd  = data.pop("password")
        role = data.get("role", "customer")
        user = User(**data)
        user.set_password(pwd)
        user.save()

        # ➊ root admin → gets its own fresh tenant
        if role == "root":
            Tenant.objects.create(owner=user, ws_key=uuid.uuid4().hex)

        # ➋ cp admin / customer signing-up with invite code?
        #     Expect the frontend to send ?ws_key=... in the URL
        ws_key = self.context["request"].query_params.get("ws_key")
        if ws_key:
            try:
                Tenant.objects.get(ws_key=ws_key).users.add(user)
            except Tenant.DoesNotExist:
                pass   # optional: raise ValidationError("bad invite")

        return user
"""

"""
class SignUpSerializer(serializers.ModelSerializer):

    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role     = serializers.ChoiceField(choices=User.ROLE_CHOICES)

    class Meta:
        model  = User
        fields = ("email", "password", "role")

    def create(self, validated):
        # username = email so we don’t need a separate field
        user = User.objects.create_user(
            username = validated["email"],
            email    = validated["email"],
            password = validated["password"],
            role     = validated["role"],
        )
        # root-user gets a Tenant automatically
        if user.role == "root":
            Tenant.objects.create(owner=user)
        return user
"""
"""
# csms/serializers.py
class SignUpSerializer(serializers.ModelSerializer):
    def create(self, validated_data):
        role = validated_data.get("role", "user")
        user = User.objects.create_user(**validated_data)

        if role == "root":
            Tenant.objects.create(owner=user, ws_key=uuid.uuid4().hex)

        return user
"""

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
        """
        • Hash the password
        • Persist the user
        • If the user is a “root” owner, also create their Tenant with a ws_key.
        """
        role = validated.pop("role", "root")
        password = validated.pop("password")

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

"""
# csms/serializers.py
class MeSerializer(serializers.Serializer):
    id       = serializers.IntegerField(source="pk")
    email    = serializers.EmailField()
    role     = serializers.CharField()
    ws_url   = serializers.SerializerMethodField()

    def get_ws_url(self, user):
        if user.role == "root":
            return user.tenant.websocket_url(self.context["request"])
        return None

"""
"""
# csms/serializers.py
class MeSerializer(serializers.ModelSerializer):
    ws_url = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ("id", "email", "role", "ws_url")

    def get_ws_url(self, obj):
        return f"ws://{settings.PUBLIC_HOST}/api/v16/{obj.tenant.ws_key}"
"""
"""
class MeSerializer(serializers.ModelSerializer):

    # new computed field
    ws_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = settings.AUTH_USER_MODEL if isinstance(settings.AUTH_USER_MODEL, str) else get_user_model()
        # whatever you were already returning, plus ws_url
        fields = ("id", "username", "email", "role", "ws_url")

    # ----- helpers ----------------------------------------------------
    def get_ws_url(self, user):
        tenant = getattr(user, "tenant", None)
        if not tenant:
            return None
        host = self.context["request"].get_host()            #  e.g.  147.93.127.215:8000
        return f"ws://{host}/api/v16/{tenant.ws_key}"
"""

"""
class MeSerializer(serializers.ModelSerializer):
    ws_url  = serializers.SerializerMethodField()   # <── new
    ws_key  = serializers.SerializerMethodField()   # <── optional

    class Meta:
        model  = User
        fields = ("username", "role", "ws_url", "ws_key")  # add whatever else

    # helpers ---------------------------------------------------------------
    def get_ws_key(self, obj):
        try:
            return obj.tenant.ws_key
        except Tenant.DoesNotExist:
            return None

    def get_ws_url(self, obj):
        key = self.get_ws_key(obj)
        if not key:
            return None
        host = getattr(settings, "PUBLIC_HOST", "127.0.0.1")
        return f"ws://{host}/api/v16/{key}"
"""


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
