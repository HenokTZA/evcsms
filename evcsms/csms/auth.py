import jwt, os, asyncio
from aiohttp import web
from datetime import datetime, timedelta
from models import create_user, get_user, pwd_ctx

JWT_SECRET = os.getenv("JWT_SECRET", "change_this")
JWT_ALGO = "HS256"
JWT_EXP   = 3600  # seconds

async def signup(request):
    data = await request.json()
    email = data["email"]
    pw    = data["password"]
    role  = data.get("role", "user")
    if role not in ("user","admin","superadmin"):
        return web.HTTPBadRequest(text="Invalid role")
    if await get_user(email):
        return web.HTTPConflict(text="User exists")
    await create_user(email, pw, role)
    return web.json_response({"status":"ok"})

async def login(request):
    data = await request.json()
    user = await get_user(data["email"])
    if not user or not pwd_ctx.verify(data["password"], user["password"]):
        return web.HTTPUnauthorized(text="Bad creds")
    payload = {
        "sub": str(user["_id"]),
        "email": user["email"],
        "role": user["role"],
        "exp": datetime.utcnow() + timedelta(seconds=JWT_EXP)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    return web.json_response({"token": token})

def jwt_required(fn):
    async def wrapped(request):
        auth = request.headers.get("Authorization","")
        if not auth.startswith("Bearer "):
            raise web.HTTPUnauthorized()
        token = auth.split()[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        except jwt.PyJWTError:
            raise web.HTTPUnauthorized()
        request["user"] = payload
        return await fn(request)
    return wrapped

