"""Smoke tests for ORM model classes (no live DB required)."""

from sqlalchemy import create_engine

from db.base import Base
from tests.test_template import TestTemplate


class TestGoogleTokensModel(TestTemplate):
    def test_tablename(self):
        from db.models.google_tokens import GoogleToken

        assert GoogleToken.__tablename__ == "google_tokens"

    def test_columns(self):
        from db.models.google_tokens import GoogleToken

        cols = {c.name for c in GoogleToken.__table__.columns}
        expected = {
            "user_id",
            "email",
            "refresh_token_enc",
            "key_id",
            "scopes",
            "granted_at",
            "revoked_at",
            "updated_at",
        }
        assert expected.issubset(cols)

    def test_primary_key_is_user_id(self):
        from db.models.google_tokens import GoogleToken

        pk_cols = {c.name for c in GoogleToken.__table__.primary_key}
        assert pk_cols == {"user_id"}

    def test_can_create_in_sqlite(self):
        # Import to register with metadata
        import db.models.google_tokens  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        assert "google_tokens" in Base.metadata.tables
