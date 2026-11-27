"""highlights and scenes

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "highlight_runs",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("transcript_id", sa.Uuid(), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["video_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_highlight_runs_created_by"), "highlight_runs", ["created_by"], unique=False)
    op.create_index(op.f("ix_highlight_runs_job_id"), "highlight_runs", ["job_id"], unique=False)
    op.create_index(op.f("ix_highlight_runs_params_hash"), "highlight_runs", ["params_hash"], unique=False)
    op.create_index(op.f("ix_highlight_runs_source_id"), "highlight_runs", ["source_id"], unique=False)
    op.create_index(op.f("ix_highlight_runs_transcript_id"), "highlight_runs", ["transcript_id"], unique=False)
    op.create_table(
        "highlight_candidates",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("start_seg", sa.Integer(), nullable=False),
        sa.Column("start_word", sa.Integer(), nullable=False),
        sa.Column("end_seg", sa.Integer(), nullable=False),
        sa.Column("end_word", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("llm_score", sa.Integer(), nullable=False),
        sa.Column("standalone_score", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("score_parts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="new", nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status in ('new', 'accepted', 'rejected')", name="ck_highlight_candidates_status"),
        sa.CheckConstraint("end_ms > start_ms", name="ck_highlight_candidates_span"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["highlight_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_highlight_candidates_reviewed_by"), "highlight_candidates", ["reviewed_by"], unique=False)
    op.create_index(op.f("ix_highlight_candidates_run_id"), "highlight_candidates", ["run_id"], unique=False)
    op.add_column("video_sources", sa.Column("scenes_asset_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_video_sources_scenes_asset_id"), "video_sources", ["scenes_asset_id"], unique=False)
    op.create_foreign_key(
        "fk_video_sources_scenes_asset_id",
        "video_sources",
        "media_assets",
        ["scenes_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Autogenerate cannot see CHECK constraint changes: allow the new kind of stored file (scene cuts).
    op.drop_constraint("ck_media_assets_kind", "media_assets", type_="check")
    op.create_check_constraint(
        "ck_media_assets_kind",
        "media_assets",
        "kind in ('original', 'proxy', 'waveform', 'thumbnail', 'audio', 'audio_prep', 'scenes')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_media_assets_kind", "media_assets", type_="check")
    op.execute("DELETE FROM media_assets WHERE kind = 'scenes'")
    op.create_check_constraint(
        "ck_media_assets_kind",
        "media_assets",
        "kind in ('original', 'proxy', 'waveform', 'thumbnail', 'audio', 'audio_prep')",
    )
    op.drop_constraint("fk_video_sources_scenes_asset_id", "video_sources", type_="foreignkey")
    op.drop_index(op.f("ix_video_sources_scenes_asset_id"), table_name="video_sources")
    op.drop_column("video_sources", "scenes_asset_id")
    op.drop_index(op.f("ix_highlight_candidates_run_id"), table_name="highlight_candidates")
    op.drop_index(op.f("ix_highlight_candidates_reviewed_by"), table_name="highlight_candidates")
    op.drop_table("highlight_candidates")
    op.drop_index(op.f("ix_highlight_runs_transcript_id"), table_name="highlight_runs")
    op.drop_index(op.f("ix_highlight_runs_source_id"), table_name="highlight_runs")
    op.drop_index(op.f("ix_highlight_runs_params_hash"), table_name="highlight_runs")
    op.drop_index(op.f("ix_highlight_runs_job_id"), table_name="highlight_runs")
    op.drop_index(op.f("ix_highlight_runs_created_by"), table_name="highlight_runs")
    op.drop_table("highlight_runs")
