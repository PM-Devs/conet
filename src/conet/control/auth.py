import os
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Human accounts (§A). Deliberately separate from AgentManifest/Store:
    a human admin configures agent policy, but a human is not an agent."""


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


def create_auth_module(
    db_path: str = 'conet_users.db', secret: str | None = None, cookie_secure: bool | None = None,
) -> SimpleNamespace:
    """Wires up a self-hosted human-auth stack (no third-party auth SaaS):
    SQLAlchemy user storage, JWT bearer + cookie auth, and fastapi-users' routers.

    The first user ever created becomes owner/admin (is_superuser=True) —
    there is no separate bootstrap step.

    cookie_secure defaults to True (browsers only send a Secure cookie over
    HTTPS) unless CONET_DASHBOARD_INSECURE_COOKIES=1 is set — NFR-011 wants
    "run a minimal CoNET network locally" to work over plain http://localhost
    without extra TLS setup. Any deployment reachable over the network
    should run behind TLS and leave this at its secure default.
    """
    resolved_secret = secret or os.environ.get('CONET_AUTH_SECRET', 'dev-secret-change-me')

    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def create_db_and_tables() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_maker() as session:
            yield session

    async def get_user_db(session: AsyncSession = Depends(get_async_session)) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
        yield SQLAlchemyUserDatabase(session, User)

    class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
        reset_password_token_secret = resolved_secret
        verification_token_secret = resolved_secret

        async def create(self, user_create: UserCreate, safe: bool = False, request=None) -> User:  # type: ignore[override]
            # safe=True (always true for the public register endpoint) makes
            # BaseUserManager.create() strip privileged fields like
            # is_superuser from user_create before it ever reaches the DB —
            # that's what makes it "safe". So set them here, after creation,
            # via a direct update rather than fighting that filter.
            sqlalchemy_db = cast(SQLAlchemyUserDatabase, self.user_db)
            existing_count = await sqlalchemy_db.session.scalar(select(func.count()).select_from(User))
            created_user = await super().create(user_create, safe=safe, request=request)
            if existing_count == 0:
                created_user = await self.user_db.update(created_user, {'is_superuser': True, 'is_verified': True})
            return created_user

    async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)) -> AsyncGenerator[UserManager, None]:
        yield UserManager(user_db)

    def get_jwt_strategy() -> JWTStrategy:
        return JWTStrategy(secret=resolved_secret, lifetime_seconds=3600)

    # Two backends, same user store: bearer/JWT for programmatic API/CLI
    # access, cookie for the server-rendered dashboard (Feature Plan §B:
    # "no separate React app") -- a browser navigating between pages can't
    # attach an Authorization header itself the way an API client can.
    bearer_transport = BearerTransport(tokenUrl='auth/jwt/login')
    bearer_backend = AuthenticationBackend(name='jwt', transport=bearer_transport, get_strategy=get_jwt_strategy)

    resolved_cookie_secure = cookie_secure
    if resolved_cookie_secure is None:
        resolved_cookie_secure = os.environ.get('CONET_DASHBOARD_INSECURE_COOKIES') != '1'
    cookie_transport = CookieTransport(cookie_name='conet_auth', cookie_max_age=3600, cookie_secure=resolved_cookie_secure)
    cookie_backend = AuthenticationBackend(name='cookie', transport=cookie_transport, get_strategy=get_jwt_strategy)

    fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [bearer_backend, cookie_backend])

    async def list_users() -> list[User]:
        """Convenience for the dashboard's Team panel — fastapi-users
        exposes no built-in "list all users" endpoint/helper."""
        async with async_session_maker() as session:
            result = await session.execute(select(User))
            return list(result.scalars().all())

    return SimpleNamespace(
        engine=engine,
        create_db_and_tables=create_db_and_tables,
        list_users=list_users,
        fastapi_users=fastapi_users,
        bearer_backend=bearer_backend,
        cookie_backend=cookie_backend,
        get_user_manager=get_user_manager,
        current_active_user=fastapi_users.current_user(active=True),
        current_superuser=fastapi_users.current_user(active=True, superuser=True),
        current_active_user_optional=fastapi_users.current_user(active=True, optional=True),
        UserRead=UserRead,
        UserCreate=UserCreate,
        UserUpdate=UserUpdate,
    )
