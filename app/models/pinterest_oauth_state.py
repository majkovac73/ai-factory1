from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class PinterestOAuthState(Base):
    """Short-lived CSRF `state` values for in-flight Pinterest OAuth flows.

    Persisted (rather than kept in an in-memory set) so a `state` created when
    the authorization URL is generated survives an app restart/redeploy before
    the callback arrives. The in-memory set was wiped on every deploy, which
    produced 'Unknown or expired OAuth state' if the server restarted while the
    user was on Pinterest's consent screen. Rows are single-use and pruned
    after ~1 hour (see pinterest_oauth._consume_state)."""
    __tablename__ = "pinterest_oauth_states"

    state = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
