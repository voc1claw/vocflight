"""Supabase-backed persistence helpers for auth, app settings, and logs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests
from werkzeug.security import check_password_hash, generate_password_hash


ALL_MODELS = [
    {"id": "openrouter/elephant-alpha", "label": "Elephant Alpha", "tier": "free"},
    {"id": "z-ai/glm-5.1", "label": "GLM 5.1", "tier": "mid"},
    {"id": "qwen/qwen3.6-plus", "label": "Qwen 3.6 Plus", "tier": "mid"},
    {"id": "minimax/minimax-m2.7", "label": "MiniMax M2.7", "tier": "mid"},
    {"id": "anthropic/claude-opus-4.6", "label": "Claude Opus 4.6", "tier": "high"},
    {"id": "anthropic/claude-sonnet-4.6", "label": "Claude Sonnet 4.6", "tier": "high"},
    {"id": "openai/gpt-5.4", "label": "GPT 5.4", "tier": "high"},
]

DEFAULT_ENABLED_MODEL_IDS = [
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4.6",
    "openrouter/elephant-alpha",
]

DEFAULT_ADMIN_USERNAME = "vocflight"
DEFAULT_ADMIN_PASSWORD_HASH = (
    "scrypt:32768:8:1$Iv7jISOMO6uRNEsI$"
    "e20085e1b429a872aee8f93193657dcda69b8830ad6cbf603eab1d84d88602a72c08400aba5461d71d6aa73c251033d98c81678ef486e5f4c4ffb5ea8f224b12"
)


class SupabaseStore:
    def __init__(self) -> None:
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self._seeded = False

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_role_key)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )

    def seed_defaults(self) -> None:
        if self._seeded or not self.enabled:
            return

        self.ensure_config()
        admin_user = self.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        if not admin_user:
            self.insert(
                "app_users",
                {
                    "username": DEFAULT_ADMIN_USERNAME,
                    "password_hash": DEFAULT_ADMIN_PASSWORD_HASH,
                    "role": "admin",
                    "is_active": True,
                },
            )
        elif admin_user.get("role") != "admin" or not admin_user.get("is_active"):
            self.update(
                "app_users",
                {"role": "admin", "is_active": True},
                filters={"id": admin_user["id"]},
            )

        self._seeded = True

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        self.require_enabled()
        headers = self._headers()
        if prefer:
            headers["Prefer"] = prefer
        response = requests.request(
            method,
            f"{self.url}/rest/v1/{table}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=30,
        )
        response.raise_for_status()
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _filter_params(filters: dict[str, Any] | None) -> dict[str, str]:
        params: dict[str, str] = {}
        for key, value in (filters or {}).items():
            if isinstance(value, bool):
                params[key] = f"eq.{str(value).lower()}"
            else:
                params[key] = f"eq.{value}"
        return params

    def select_many(
        self,
        table: str,
        *,
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params = self._filter_params(filters)
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        result = self._request("GET", table, params=params)
        return result or []

    def select_one(self, table: str, *, filters: dict[str, Any]) -> dict[str, Any] | None:
        rows = self.select_many(table, filters=filters, limit=1)
        return rows[0] if rows else None

    def insert(self, table: str, payload: dict[str, Any] | list[dict[str, Any]]) -> Any:
        return self._request("POST", table, json_body=payload, prefer="return=representation")

    def update(self, table: str, payload: dict[str, Any], *, filters: dict[str, Any]) -> Any:
        return self._request(
            "PATCH",
            table,
            params=self._filter_params(filters),
            json_body=payload,
            prefer="return=representation",
        )

    def delete(self, table: str, *, filters: dict[str, Any]) -> Any:
        return self._request(
            "DELETE",
            table,
            params=self._filter_params(filters),
            prefer="return=representation",
        )

    def ensure_config(self) -> dict[str, Any]:
        config = self.select_one("app_config", filters={"id": "main"})
        if config:
            return config
        payload = {
            "id": "main",
            "registration_enabled": True,
            "registration_password_hash": None,
            "enabled_models": DEFAULT_ENABLED_MODEL_IDS,
        }
        result = self.insert("app_config", payload)
        return result[0] if isinstance(result, list) else payload

    def get_config(self) -> dict[str, Any]:
        config = self.ensure_config()
        config["enabled_models"] = config.get("enabled_models") or DEFAULT_ENABLED_MODEL_IDS
        return config

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        self.ensure_config()
        payload = {**updates, "updated_at": utc_now_iso()}
        result = self.update("app_config", payload, filters={"id": "main"})
        if isinstance(result, list) and result:
            return result[0]
        return self.get_config()

    def list_enabled_models(self) -> list[dict[str, Any]]:
        enabled_ids = set(self.get_config().get("enabled_models") or DEFAULT_ENABLED_MODEL_IDS)
        models = [model for model in ALL_MODELS if model["id"] in enabled_ids]
        return models or [model for model in ALL_MODELS if model["id"] == "openai/gpt-5.4"]

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.select_one("app_users", filters={"username": username})

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        return self.select_one("app_users", filters={"id": user_id})

    def list_users(self) -> list[dict[str, Any]]:
        return self.select_many("app_users", order="created_at.desc")

    def create_user(
        self,
        username: str,
        password: str,
        *,
        role: str = "member",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        password_hash = generate_password_hash(password)
        payload = {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "is_active": True,
            "created_by": created_by,
        }
        result = self.insert("app_users", payload)
        return result[0]

    def verify_user(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.get_user_by_username(username)
        if not user or not user.get("is_active"):
            return None
        if not check_password_hash(user["password_hash"], password):
            return None
        return user

    def delete_user(self, user_id: str) -> None:
        self.delete("app_users", filters={"id": user_id})

    def set_registration_password(self, password: str | None) -> dict[str, Any]:
        password_hash = generate_password_hash(password) if password else None
        return self.update_config({"registration_password_hash": password_hash})

    def verify_registration_password(self, password: str) -> bool:
        password_hash = self.get_config().get("registration_password_hash")
        if not password_hash:
            return True
        return check_password_hash(password_hash, password)

    def log_admin_action(
        self,
        *,
        admin_user_id: str,
        admin_username: str,
        action: str,
        target_type: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.insert(
            "admin_logs",
            {
                "admin_user_id": admin_user_id,
                "admin_username": admin_username,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "details": details or {},
            },
        )

    def list_admin_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.select_many("admin_logs", order="created_at.desc", limit=limit)

    def log_chat_event(
        self,
        *,
        user_id: str,
        username: str,
        role: str,
        session_id: str | None,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> None:
        self.insert(
            "chat_logs",
            {
                "user_id": user_id,
                "username": username,
                "user_role": role,
                "session_id": session_id,
                "request_payload": request_payload,
                "response_payload": response_payload,
            },
        )

    def list_chat_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.select_many("chat_logs", order="created_at.desc", limit=limit)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_bootstrap_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role"),
    }


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)
